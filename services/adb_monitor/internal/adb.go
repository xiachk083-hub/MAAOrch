package internal

import (
	"encoding/json"
	"net/http"
	"os/exec"
	"strings"
)

var (
	adbPath     string
	mumuCliPath string
)

func init() {
	// Find adb.exe (same logic as find_mumu_cli)
	adbPath = findADB()
	mumuCliPath = findMumuCli()
}

func writeJSON(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}

func writeError(w http.ResponseWriter, code int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

func runCmd(name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// HandlePing pings an ADB device: /ping?addr=127.0.0.1:16384
func HandlePing(w http.ResponseWriter, r *http.Request) {
	addr := r.URL.Query().Get("addr")
	if addr == "" {
		writeError(w, 400, "missing addr parameter")
		return
	}
	if adbPath == "" {
		writeError(w, 500, "adb not found")
		return
	}
	out, err := runCmd(adbPath, "-s", addr, "shell", "echo", "ping")
	if err != nil || !strings.Contains(out, "ping") {
		writeJSON(w, map[string]interface{}{"ok": false, "output": out, "error": errStr(err)})
		return
	}
	writeJSON(w, map[string]interface{}{"ok": true, "output": out})
}

// HandleConnect connects to an ADB device: /connect?addr=127.0.0.1:16384
func HandleConnect(w http.ResponseWriter, r *http.Request) {
	addr := r.URL.Query().Get("addr")
	if addr == "" {
		writeError(w, 400, "missing addr parameter")
		return
	}
	if adbPath == "" {
		writeError(w, 500, "adb not found")
		return
	}
	out, err := runCmd(adbPath, "connect", addr)
	connected := err == nil && (strings.Contains(out, "connected") || strings.Contains(out, "already"))
	writeJSON(w, map[string]interface{}{
		"ok":     connected,
		"output": out,
		"error":  errStr(err),
	})
}

// HandleDevices lists ADB devices
func HandleDevices(w http.ResponseWriter, r *http.Request) {
	if adbPath == "" {
		writeError(w, 500, "adb not found")
		return
	}
	// First check ADB server health
	out, err := runCmd(adbPath, "devices")
	if err != nil && (strings.Contains(out, "protocol fault") || strings.Contains(out, "connection reset")) {
		// Kill and restart ADB server
		runCmd(adbPath, "kill-server")
		runCmd(adbPath, "start-server")
		out, err = runCmd(adbPath, "devices")
	}
	writeJSON(w, map[string]interface{}{
		"ok":     err == nil,
		"output": out,
		"error":  errStr(err),
	})
}

// HandleADBHealth checks ADB server health and fixes if needed
func HandleADBHealth(w http.ResponseWriter, r *http.Request) {
	if adbPath == "" {
		writeError(w, 500, "adb not found")
		return
	}
	out, err := runCmd(adbPath, "devices")
	neededFix := false
	if err != nil && (strings.Contains(out, "protocol fault") || strings.Contains(out, "connection reset")) {
		neededFix = true
		runCmd(adbPath, "kill-server")
		runCmd(adbPath, "start-server")
		out, err = runCmd(adbPath, "devices")
	}
	writeJSON(w, map[string]interface{}{
		"ok":         err == nil,
		"needed_fix": neededFix,
		"output":     out,
		"error":      errStr(err),
	})
}

func errStr(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
