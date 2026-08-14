// vision_cli — 图片 → 视觉模型 → 结构化 JSON 证据（Go 单二进制，零运行时依赖）
//
// 目标：目标机部署无需 Node/Python，直接分发 exe。
// 引擎：OpenCode Zen（Anthropic 协议 zen/go 端点，qwen3.8-max，cost=0）。
// 输出契约参照 modlens：ocr 全文 + layout 区域 + semantics 实体 + 原始文本。
//
// 用法：
//   vision_cli <image.png|jpg> [-prompt "额外焦点"] [-o out.json]
//   vision_cli probe <image.png> <stage>   # 关卡判定专用模式（CAN_FARM/CANNOT/SHOTFAIL）
//
// 凭据：VISION_API_KEY 环境变量，或 ~/.maorch_deploy.json 的 vision_api_key 字段。
package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const (
	defaultEndpoint = "https://opencode.ai/zen/go/v1/messages"
	defaultModel    = "qwen3.8-max"
)

// ── 请求/响应结构（Anthropic Messages API 子集）──

type imageBlock struct {
	Type   string `json:"type"`
	Source struct {
		Type      string `json:"type"`
		MediaType string `json:"media_type"`
		Data      string `json:"data"`
	} `json:"source"`
}

type textBlock struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type message struct {
	Role    string `json:"role"`
	Content []any  `json:"content"`
}

type visionRequest struct {
	Model     string    `json:"model"`
	MaxTokens int       `json:"max_tokens"`
	Thinking  map[string]any `json:"thinking,omitempty"`
	Messages  []message `json:"messages"`
}

type visionResponse struct {
	Content []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
	StopReason string `json:"stop_reason"`
	Usage      struct {
		InputTokens  int `json:"input_tokens"`
		OutputTokens int `json:"output_tokens"`
	} `json:"usage"`
	Text string `json:"-"` // 过滤 thinking 块后的纯文本
}

// ── 输出结构（modlens 式契约）──

type outRegion struct {
	ReadingOrder int    `json:"reading_order"`
	Type         string `json:"type"`
	Text         string `json:"text"`
}

type outEvidence struct {
	Ok         bool        `json:"ok"`
	Error      string      `json:"error,omitempty"`
	Summary    string      `json:"summary"`
	OCRFull    string      `json:"ocr_full_text"`
	Layout     []outRegion `json:"layout_regions"`
	Semantics  []struct {
		Name     string `json:"name"`
		Type     string `json:"type"`
		Evidence string `json:"evidence"`
	} `json:"semantics,omitempty"`
	Verdict  string  `json:"verdict,omitempty"` // probe 模式：CAN_FARM/CANNOT/SHOTFAIL/UNKNOWN
	Score    float64 `json:"score,omitempty"`
	Raw      string  `json:"raw"`
	Meta     struct {
		Model        string `json:"model"`
		DurationMs   int64  `json:"duration_ms"`
		InputTokens  int    `json:"input_tokens"`
		OutputTokens int    `json:"output_tokens"`
	} `json:"meta"`
}

func main() {
	if err := run(); err != nil {
		out := outEvidence{Ok: false, Error: err.Error()}
		b, _ := json.MarshalIndent(out, "", "  ")
		fmt.Println(string(b))
		os.Exit(1)
	}
}

func run() error {
	flag.String("prompt", "", "extra focus prompt") // 占位，flags 在子命令前解析
	if len(os.Args) < 2 {
		return errors.New("usage: vision_cli <image> | vision_cli probe <image> <stage>")
	}
	mode := "describe"
	args := os.Args[1:]
	if args[0] == "probe" {
		mode = "probe"
		args = args[1:]
	}
	var imagePath, stage, outPath, prompt, endpoint, model, key string
	if mode == "probe" {
		if len(args) < 2 {
			return errors.New("probe 模式需要 <image> <stage>")
		}
		imagePath, stage = args[0], args[1]
	} else {
		if len(args) < 1 {
			return errors.New("缺少图片路径")
		}
		imagePath = args[0]
	}
	fs := flag.NewFlagSet("v", flag.ExitOnError)
	fs.StringVar(&prompt, "prompt", "", "extra focus prompt")
	fs.StringVar(&outPath, "o", "", "write JSON to file instead of stdout")
	fs.StringVar(&endpoint, "endpoint", os.Getenv("VISION_ENDPOINT"), "anthropic messages endpoint")
	fs.StringVar(&model, "model", defaultModel, "model name")
	fs.StringVar(&key, "key", "", "api key")
	if err := fs.Parse(os.Args[len(os.Args)-len(args):]); err != nil {
		return err
	}
	if endpoint == "" {
		endpoint = defaultEndpoint
	}
	if key == "" {
		key = loadKey()
	}
	if key == "" {
		return errors.New("no api key: set VISION_API_KEY or put vision_api_key in ~/.maorch_deploy.json")
	}

	img, mime, err := readImage(imagePath)
	if err != nil {
		return err
	}

	started := time.Now()
	res, err := callVision(endpoint, model, key, img, mime, mode, stage, prompt)
	if err != nil {
		return err
	}

	ev := parseAnswer(res.Text, mode)
	ev.Meta.Model = model
	ev.Meta.DurationMs = time.Since(started).Milliseconds()
	ev.Meta.InputTokens = res.Usage.InputTokens
	ev.Meta.OutputTokens = res.Usage.OutputTokens

	b, _ := json.MarshalIndent(ev, "", "  ")
	if outPath != "" {
		return os.WriteFile(outPath, b, 0o644)
	}
	fmt.Println(string(b))
	return nil
}

// loadKey 从 ~/.maorch_deploy.json 读 vision_api_key（不入仓库）。
func loadKey() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	data, err := os.ReadFile(filepath.Join(home, ".maorch_deploy.json"))
	if err != nil {
		return ""
	}
	var cfg map[string]any
	if json.Unmarshal(data, &cfg) != nil {
		return ""
	}
	if k, ok := cfg["vision_api_key"].(string); ok {
		return k
	}
	return ""
}

func readImage(path string) ([]byte, string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, "", fmt.Errorf("read image: %w", err)
	}
	switch strings.ToLower(filepath.Ext(path)) {
	case ".png":
		return data, "image/png", nil
	case ".jpg", ".jpeg":
		return data, "image/jpeg", nil
	case ".webp":
		return data, "image/webp", nil
	default:
		return nil, "", fmt.Errorf("unsupported image type: %s", filepath.Ext(path))
	}
}

func callVision(endpoint, model, key string, img []byte, mime, mode, stage, extra string) (*visionResponse, error) {
	prompt := describePrompt(mode, stage, extra)
	body := visionRequest{
		Model:     model,
		MaxTokens: 8000, // thinking 模型 token 预留（vision.py 实测 <8000 会截断）
		Thinking:  map[string]any{"type": "disabled"},
		Messages: []message{{
			Role: "user",
			Content: []any{
				imageBlock{Type: "image", Source: struct {
					Type      string `json:"type"`
					MediaType string `json:"media_type"`
					Data      string `json:"data"`
				}{Type: "base64", MediaType: mime, Data: base64.StdEncoding.EncodeToString(img)}},
				textBlock{Type: "text", Text: prompt},
			},
		}},
	}
	payload, _ := json.Marshal(body)
	req, err := http.NewRequest("POST", endpoint, strings.NewReader(string(payload)))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", key)
	req.Header.Set("anthropic-version", "2023-06-01")

	client := &http.Client{Timeout: 150 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("vision request: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("vision api %d: %s", resp.StatusCode, truncate(string(raw), 300))
	}
	var vr visionResponse
	if err := json.Unmarshal(raw, &vr); err != nil {
		return nil, fmt.Errorf("decode response: %w", err)
	}
	// 过滤 thinking 块，拼 text 块（vision.py 经验：Zen 响应含 thinking 块）
	var texts []string
	for _, c := range vr.Content {
		if c.Type == "text" {
			texts = append(texts, c.Text)
		}
	}
	vr.Text = strings.Join(texts, "\n")
	if vr.Text == "" {
		return nil, errors.New("empty text from vision model (max_tokens too small?)")
	}
	return &vr, nil
}

func describePrompt(mode, stage, extra string) string {
	switch mode {
	case "probe":
		p := fmt.Sprintf(`这是明日方舟关卡导航后的截图。目标关卡是 %q。
判定规则：
- 能看到「开始行动」按钮（金色高亮）→ verdict=CAN_FARM
- 能看到代理不可用/代理锁定提示（如「代理指挥」灰色、UnableToAgent 提示）→ verdict=CANNOT
- 画面是桌面/标题页/加载中/与关卡无关 → verdict=SHOTFAIL
请以 JSON 回答（无 markdown 代码块），字段：
{"summary":"一句话画面描述","ocr_full_text":"画面可见文字全转写","verdict":"CAN_FARM|CANNOT|SHOTFAIL","score":0.0,"detail":"判定依据"}
日服界面文字是日文，请按日文原文转写。`, stage)
		if extra != "" {
			p += "\n额外注意：" + extra
		}
		return p
	default:
		p := `请详细分析这张图片（明日方舟模拟器截图）。以 JSON 回答（无 markdown 代码块），字段：
{"summary":"一句话描述","ocr_full_text":"画面可见文字全转写（日文按原文）","layout_regions":[{"reading_order":1,"type":"title|subtitle|paragraph|button|icon|list|image","text":"内容"}],"semantics":[{"name":"实体名","type":"实体类型","evidence":"判定依据"}]}`
		if extra != "" {
			p += "\n额外注意：" + extra
		}
		return p
	}
}

// parseAnswer 解析模型 JSON 回答；失败时把原始文本放 raw（消费者兜底）。
func parseAnswer(text string, mode string) outEvidence {
	ev := outEvidence{Raw: text}
	clean := stripCodeFence(text)
	if err := json.Unmarshal([]byte(clean), &ev); err != nil {
		// 非 JSON 回答：structured 信息缺失，保留 raw
		ev.Ok = true
		ev.Summary = truncate(strings.ReplaceAll(clean, "\n", " "), 200)
		ev.OCRFull = clean
		return ev
	}
	ev.Ok = true
	return ev
}

func stripCodeFence(s string) string {
	s = strings.TrimSpace(s)
	s = strings.TrimPrefix(s, "```json")
	s = strings.TrimPrefix(s, "```")
	s = strings.TrimSuffix(s, "```")
	return strings.TrimSpace(s)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// 保证 exec 子进程关闭（无控制台窗口）— Windows 生产机部署时用。
var _ = exec.Command
