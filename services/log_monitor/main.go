package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
)

var trackers = make(map[string]*LogTracker)

type LogTracker struct {
	File     string
	Position int64
	LastTask string
}

// readLatest reads new content from a log file since the last position.
func (t *LogTracker) readLatest() (string, error) {
	f, err := os.Open(t.File)
	if err != nil {
		return "", err
	}
	defer f.Close()

	stat, err := f.Stat()
	if err != nil {
		return "", err
	}
	if stat.Size() <= t.Position {
		return "", nil // no new content
	}
	// Read from last position to end (max 64KB to avoid huge reads)
	readSize := stat.Size() - t.Position
	if readSize > 65536 {
		readSize = 65536
		t.Position = stat.Size() - 65536
	}
	buf := make([]byte, readSize)
	_, err = f.ReadAt(buf, t.Position)
	if err != nil {
		return "", err
	}
	t.Position = stat.Size()
	return string(buf), nil
}

func getTracker(instDir, aid string) *LogTracker {
	key := aid
	if t, ok := trackers[key]; ok {
		return t
	}
	logPath := filepath.Join(instDir, "debug", "asst.log")
	t := &LogTracker{
		File:     logPath,
		Position: 0,
	}
	trackers[key] = t
	return t
}

func writeJSON(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}

// GET /tail?inst=path&aid=id&pos=N — read new log content
func handleTail(w http.ResponseWriter, r *http.Request) {
	instDir := r.URL.Query().Get("inst")
	aid := r.URL.Query().Get("aid")
	if instDir == "" || aid == "" {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "missing inst/aid params"})
		return
	}
	t := getTracker(instDir, aid)
	content, err := t.readLatest()
	if err != nil {
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	// Parse for task name
	taskName := ""
	allDone := false
	for _, line := range strings.Split(content, "\n") {
		if strings.Contains(line, "AllTasksCompleted") {
			allDone = true
		}
		if strings.Contains(line, "SubTaskStart") && strings.Contains(line, "taskchain") {
			if idx := strings.Index(line, "\"taskchain\""); idx >= 0 {
				rest := line[idx+12:]
				if end := strings.Index(rest, "\""); end >= 0 {
					taskName = rest[:end]
				}
			}
		}
		// Also check append_task lines
		if strings.Contains(line, "append_task") {
			for _, task := range []string{"StartUp", "Fight", "Recruit", "Infrast", "Mall", "Award", "Roguelike", "Reclamation"} {
				if strings.Contains(line, task) {
					taskName = task
					break
				}
			}
		}
	}
	if taskName != "" && taskName != t.LastTask {
		t.LastTask = taskName
	}
	writeJSON(w, map[string]interface{}{
		"ok":          true,
		"content":     content,
		"position":    t.Position,
		"task":        t.LastTask,
		"all_done":    allDone,
	})
}

// GET /reset?aid=id — reset tracker position to 0
func handleReset(w http.ResponseWriter, r *http.Request) {
	aid := r.URL.Query().Get("aid")
	if aid == "" {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "missing aid"})
		return
	}
	if t, ok := trackers[aid]; ok {
		t.Position = 0
	}
	writeJSON(w, map[string]interface{}{"ok": true})
}

func main() {
	port := "19997"
	if p := os.Getenv("LOG_MONITOR_PORT"); p != "" {
		port = p
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/tail", handleTail)
	mux.HandleFunc("/reset", handleReset)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]string{"status": "ok", "service": "log_monitor"})
	})

	server := &http.Server{Addr: "127.0.0.1:" + port, Handler: mux}
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		server.Close()
	}()

	log.Printf("Log Monitor starting on 127.0.0.1:%s", port)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("Server error: %v", err)
	}
}
