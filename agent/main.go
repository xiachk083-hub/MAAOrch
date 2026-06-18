package main

import (
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ── Config ──
var (
	port     = 19998
	token    = ""
	workDir  = ""
	procDir  = ""
	mu       sync.Mutex
)

type Config struct {
	Port    int    `json:"port"`
	Token   string `json:"token"`
	WorkDir string `json:"work_dir"`
}

func loadConfig() {
	paths := []string{"agent_config.json"}
	exe, err := os.Executable()
	if err != nil || exe == "" {
		fmt.Fprintf(os.Stderr, "loadConfig: os.Executable() failed: %v\n", err)
	} else {
		paths = []string{
			filepath.Join(filepath.Dir(exe), "agent_config.json"),
			"agent_config.json",
		}
	}
	for _, cfgPath := range paths {
		data, err := os.ReadFile(cfgPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "loadConfig: tried %s: %v\n", cfgPath, err)
			continue
		}
		// Strip UTF-8 BOM if present (PowerShell adds it)
		data = bytes.TrimPrefix(data, []byte{0xEF, 0xBB, 0xBF})
		var cfg Config
		if err := json.Unmarshal(data, &cfg); err != nil {
			fmt.Fprintf(os.Stderr, "loadConfig: json error for %s: %v\n", cfgPath, err)
			continue
		}
		fmt.Fprintf(os.Stderr, "loadConfig: loaded from %s: work_dir=%q\n", cfgPath, cfg.WorkDir)
		if cfg.Port > 0 {
			port = cfg.Port
		}
		if cfg.Token != "" {
			token = cfg.Token
		}
		if cfg.WorkDir != "" {
			workDir = cfg.WorkDir
		}
		break
	}
}

// ── Auth ──
func checkAuth(r *http.Request) bool {
	if token == "" {
		return true
	}
	h := r.Header.Get("x-agent-token")
	return subtle.ConstantTimeCompare([]byte(h), []byte(token)) == 1
}

func authMiddleware(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !checkAuth(r) {
			jsonError(w, "unauthorized", 401)
			return
		}
		next(w, r)
	}
}

func jsonError(w http.ResponseWriter, msg string, code int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

func jsonOK(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}

// ── Executor ──
type ExecRequest struct {
	Command string   `json:"command"`
	Args    []string `json:"args"`
	Dir     string   `json:"dir"`
	Timeout int      `json:"timeout"` // seconds
}

type ExecResult struct {
	ExitCode int    `json:"exit_code"`
	Stdout   string `json:"stdout"`
	Stderr   string `json:"stderr"`
	Error    string `json:"error,omitempty"`
}

func handleExec(w http.ResponseWriter, r *http.Request) {
	var req ExecRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "invalid body", 400)
		return
	}
	dir := req.Dir
	if dir == "" {
		dir = workDir
	}
	cmd := exec.Command(req.Command, req.Args...)
	cmd.Dir = dir

	timeout := req.Timeout
	if timeout <= 0 {
		timeout = 30
	}

	var result ExecResult
	if req.Command == "" {
		jsonError(w, "command required", 400)
		return
	}

	// Special commands
	switch req.Command {
	case "git_pull":
		cmd = exec.Command("git", "pull")
		cmd.Dir = dir
	case "git_clone":
		if len(req.Args) > 0 {
			cmd = exec.Command("git", append([]string{"clone"}, req.Args...)...)
		}
	}

	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Start()
	if err != nil {
		result.Error = err.Error()
		jsonOK(w, result)
		return
	}

	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
	}()

	select {
	case <-time.After(time.Duration(timeout) * time.Second):
		cmd.Process.Kill()
		result.Error = "timeout"
	case err := <-done:
		if err != nil {
			if exitErr, ok := err.(*exec.ExitError); ok {
				result.ExitCode = exitErr.ExitCode()
			} else {
				result.Error = err.Error()
			}
		}
	}

	result.Stdout = truncate(stdout.String(), 50000)
	result.Stderr = truncate(stderr.String(), 50000)
	jsonOK(w, result)
}

// ── File operations ──
type FileReadRequest struct {
	Path  string `json:"path"`
	Lines int    `json:"lines"` // tail N lines, 0 = all
}

type FileWriteRequest struct {
	Path    string `json:"path"`
	Content string `json:"content"`
	Append  bool   `json:"append"`
}

func handleFileRead(w http.ResponseWriter, r *http.Request) {
	var req FileReadRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "invalid body", 400)
		return
	}
	fp := resolvePath(req.Path)
	if fp == "" {
		jsonError(w, "path not allowed", 403)
		return
	}
	data, err := os.ReadFile(fp)
	if err != nil {
		jsonError(w, err.Error(), 404)
		return
	}
	content := string(data)
	if req.Lines > 0 {
		lines := strings.Split(content, "\n")
		if len(lines) > req.Lines {
			lines = lines[len(lines)-req.Lines:]
		}
		content = strings.Join(lines, "\n")
	}
	jsonOK(w, map[string]interface{}{
		"path":    fp,
		"size":    len(data),
		"content": content,
	})
}

func handleFileWrite(w http.ResponseWriter, r *http.Request) {
	var req FileWriteRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "invalid body", 400)
		return
	}
	fp := resolvePath(req.Path)
	if fp == "" {
		jsonError(w, "path not allowed", 403)
		return
	}
	os.MkdirAll(filepath.Dir(fp), 0755)
	flag := os.O_WRONLY | os.O_CREATE
	if req.Append {
		flag |= os.O_APPEND
	} else {
		flag |= os.O_TRUNC
	}
	f, err := os.OpenFile(fp, flag, 0644)
	if err != nil {
		jsonError(w, err.Error(), 500)
		return
	}
	defer f.Close()
	if _, err := f.WriteString(req.Content); err != nil {
		jsonError(w, err.Error(), 500)
		return
	}
	jsonOK(w, map[string]string{"path": fp, "status": "ok"})
}

func resolvePath(path string) string {
	abs, err := filepath.Abs(path)
	if err != nil {
		return ""
	}
	// Allow only within workDir or common system paths
	allowed := false
	if workDir != "" && strings.HasPrefix(strings.ToLower(abs), strings.ToLower(workDir)) {
		allowed = true
	}
	if procDir != "" && strings.HasPrefix(strings.ToLower(abs), strings.ToLower(procDir)) {
		allowed = true
	}
	if workDir == "" {
		allowed = true
	}
	if !allowed {
		return ""
	}
	return abs
}

// ── Process management ──
type ProcessInfo struct {
	PID     int    `json:"pid"`
	Name    string `json:"name"`
	Running bool   `json:"running"`
}

func handleProcessStop(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name string `json:"name"`
		PID  int    `json:"pid"`
	}
	json.NewDecoder(r.Body).Decode(&body)

	var cmd *exec.Cmd
	if body.PID > 0 {
		cmd = exec.Command("taskkill", "/F", "/PID", strconv.Itoa(body.PID))
	} else if body.Name != "" {
		cmd = exec.Command("taskkill", "/F", "/IM", body.Name)
	} else {
		jsonError(w, "name or pid required", 400)
		return
	}
	err := cmd.Run()
	if err != nil {
		jsonOK(w, map[string]interface{}{"status": "error", "error": err.Error()})
		return
	}
	jsonOK(w, map[string]string{"status": "killed"})
}

func handleProcessStart(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Command string   `json:"command"`
		Args    []string `json:"args"`
		Dir     string   `json:"dir"`
		Wait    bool     `json:"wait"`
		Timeout int      `json:"timeout"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		jsonError(w, "invalid body", 400)
		return
	}
	if body.Command == "" {
		jsonError(w, "command required", 400)
		return
	}
	dir := body.Dir
	if dir == "" {
		dir = workDir
	}
	cmd := exec.Command(body.Command, body.Args...)
	cmd.Dir = dir
	if err := cmd.Start(); err != nil {
		jsonOK(w, map[string]interface{}{"status": "error", "error": err.Error()})
		return
	}
	go cmd.Wait()
	jsonOK(w, map[string]interface{}{
		"status": "started",
		"pid":    cmd.Process.Pid,
	})
}

// ── Status ──
func handleStatus(w http.ResponseWriter, r *http.Request) {
	hostname, _ := os.Hostname()
	cwd, _ := os.Getwd()
	info := map[string]interface{}{
		"agent":    "maorch-agent",
		"version":  "0.1.0",
		"hostname": hostname,
		"cwd":      cwd,
		"work_dir": workDir,
		"time":     time.Now().Unix(),
	}
	jsonOK(w, info)
}

// ── Utils ──
func truncate(s string, max int) string {
	if len(s) > max {
		return s[:max] + "..."
	}
	return s
}

// ── Main ──
func main() {
	loadConfig()
	procDir = filepath.Join(workDir, "services", "maa")

	mux := http.NewServeMux()
	mux.HandleFunc("/api/agent/exec", authMiddleware(handleExec))
	mux.HandleFunc("/api/agent/read", authMiddleware(handleFileRead))
	mux.HandleFunc("/api/agent/write", authMiddleware(handleFileWrite))
	mux.HandleFunc("/api/agent/process/stop", authMiddleware(handleProcessStop))
	mux.HandleFunc("/api/agent/process/start", authMiddleware(handleProcessStart))
	mux.HandleFunc("/api/agent/status", authMiddleware(handleStatus))

	addr := fmt.Sprintf("0.0.0.0:%d", port)
	fmt.Printf("MAAOrch Agent starting on %s\n", addr)
	if token != "" {
		fmt.Println("Token auth enabled")
	}
	if err := http.ListenAndServe(addr, mux); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to start: %v\n", err)
		os.Exit(1)
	}
}
