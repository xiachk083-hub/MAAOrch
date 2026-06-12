package internal

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

func findMumuCli() string {
	// Check standard paths + drive scan (same logic as Python find_mumu_cli)
	candidates := []string{
		filepath.Join(os.Getenv("USERPROFILE"), "MuMuPlayer", "nx_main", "mumu-cli.exe"),
		`C:\Program Files\Netease\MuMuPlayer-12.0\shell\mumu-cli.exe`,
		`C:\Program Files\Netease\MuMuPlayer-12.0\nx_main\mumu-cli.exe`,
		`C:\Program Files\Netease\MuMuPlayer\nx_main\mumu-cli.exe`,
		`D:\Program Files\Netease\MuMuPlayer-12.0\shell\mumu-cli.exe`,
		`D:\Program Files\Netease\MuMuPlayer-12.0\nx_main\mumu-cli.exe`,
	}
	for _, c := range candidates {
		if _, err := os.Stat(c); err == nil {
			return c
		}
	}
	if env := os.Getenv("MUMU_CLI_HOME"); env != "" {
		p := filepath.Join(env, "mumu-cli.exe")
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	// Drive scan
	for _, d := range []string{"C:", "D:", "E:", "F:", "G:", "H:"} {
		base := d + `\`
		if _, err := os.Stat(base); err != nil {
			continue
		}
		filepath.Walk(base, func(path string, info os.FileInfo, err error) error {
			if err != nil || info == nil {
				return filepath.SkipDir
			}
			if info.IsDir() && strings.Contains(strings.ToLower(info.Name()), "mumu") {
				for _, sub := range []string{"nx_main\\mumu-cli.exe", "shell\\mumu-cli.exe"} {
					p := filepath.Join(path, sub)
					if _, err := os.Stat(p); err == nil {
						candidates = append(candidates, p)
					}
				}
				return filepath.SkipDir
			}
			return nil
		})
	}
	// Return the first valid path found
	if len(candidates) > 0 {
		// Check the ones from drive scan
		for _, c := range candidates {
			if _, err := os.Stat(c); err == nil {
				return c
			}
		}
	}
	return ""
}

func findADB() string {
	cli := findMumuCli()
	if cli != "" {
		adb := filepath.Join(filepath.Dir(cli), "adb.exe")
		if _, err := os.Stat(adb); err == nil {
			return adb
		}
	}
	// Fallback: search for adb.exe in common locations
	for _, d := range []string{"C:", "D:", "E:"} {
		filepath.Walk(d+`\`, func(path string, info os.FileInfo, err error) error {
			if err != nil || info == nil {
				return filepath.SkipDir
			}
			if strings.EqualFold(info.Name(), "adb.exe") {
				adbPath = path
				return filepath.SkipDir
			}
			if info.IsDir() && strings.Contains(info.Name(), "$") {
				return filepath.SkipDir
			}
			return nil
		})
		if adbPath != "" {
			break
		}
	}
	return adbPath
}

// HandleMumuInfo returns emulator info: /mumu/info?vm=0
func HandleMumuInfo(w http.ResponseWriter, r *http.Request) {
	vm := r.URL.Query().Get("vm")
	if mumuCliPath == "" {
		writeError(w, 500, "mumu-cli not found")
		return
	}
	args := []string{"info", "--vmindex", vm}
	out, err := runCmd(mumuCliPath, args...)
	if err != nil {
		writeError(w, 500, "mumu-cli error: "+err.Error())
		return
	}
	var data interface{}
	json.Unmarshal([]byte(out), &data)
	writeJSON(w, map[string]interface{}{"ok": true, "data": data})
}

// HandleMumuLaunch launches an emulator VM: /mumu/launch?vm=0
func HandleMumuLaunch(w http.ResponseWriter, r *http.Request) {
	vm := r.URL.Query().Get("vm")
	action := "launch"
	if r.Method == "POST" {
		if err := r.ParseForm(); err == nil {
			if a := r.FormValue("action"); a != "" {
				action = a
			}
		}
	}
	if mumuCliPath == "" {
		writeError(w, 500, "mumu-cli not found")
		return
	}
	out, err := runCmd(mumuCliPath, "control", "--vmindex", vm, action)
	if err != nil {
		writeError(w, 500, "mumu-cli error: "+err.Error())
		return
	}
	writeJSON(w, map[string]interface{}{"ok": true, "output": out})
}

// HandleMumuShutdown shuts down an emulator VM: /mumu/shutdown?vm=0
func HandleMumuShutdown(w http.ResponseWriter, r *http.Request) {
	HandleMumuLaunch(w, r) // reuse with action=shutdown
}
