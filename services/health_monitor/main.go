package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
)

type MemInfo struct {
	TotalGB     float64 `json:"total_gb"`
	AvailableGB float64 `json:"available_gb"`
	UsedGB      float64 `json:"used_gb"`
	UsedPercent int     `json:"used_percent"`
}

type ProcessInfo struct {
	PID     int    `json:"pid"`
	Name    string `json:"name"`
	MemMB   int64  `json:"mem_mb"`
	Running bool   `json:"running"`
}

type SystemStatus struct {
	Memory    MemInfo       `json:"memory"`
	Processes []ProcessInfo `json:"processes"`
	Overloaded bool         `json:"overloaded"`
}

func getMemoryInfo() MemInfo {
	// Use wmic to get memory info (works on all Windows versions)
	cmd := exec.Command("wmic", "OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/format:csv")
	out, err := cmd.Output()
	if err != nil {
		return MemInfo{}
	}
	lines := strings.Split(string(out), "\n")
	for _, line := range lines {
		fields := strings.Split(strings.TrimSpace(line), ",")
		if len(fields) >= 3 {
			total, _ := strconv.ParseFloat(strings.TrimSpace(fields[1]), 64)
			free, _ := strconv.ParseFloat(strings.TrimSpace(fields[2]), 64)
			if total > 0 {
				totalGB := total / 1024 / 1024
				freeGB := free / 1024 / 1024
				return MemInfo{
					TotalGB:     totalGB,
					AvailableGB: freeGB,
					UsedGB:      totalGB - freeGB,
					UsedPercent: int((total - free) * 100 / total),
				}
			}
		}
	}
	return MemInfo{}
}

func findMAAs() []ProcessInfo {
	var result []ProcessInfo
	cmd := exec.Command("tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq MAA.exe")
	out, err := cmd.Output()
	if err != nil {
		return result
	}
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		// CSV format: "MAA.exe","PID","Session","Session#","Mem Usage"
		parts := strings.Split(line, "\",\"")
		if len(parts) >= 5 {
			pidStr := strings.Trim(parts[1], "\"")
			memStr := strings.Trim(parts[4], "\"\r\n")
			pid, _ := strconv.Atoi(pidStr)
			memMB := parseMem(memStr)
			result = append(result, ProcessInfo{
				PID: pid, Name: "MAA.exe",
				MemMB: memMB, Running: true,
			})
		}
	}
	return result
}

func parseMem(s string) int64 {
	s = strings.TrimSpace(s)
	if strings.HasSuffix(s, "K") {
		v, _ := strconv.ParseInt(strings.TrimSuffix(s, "K"), 10, 64)
		return v / 1024
	}
	if strings.HasSuffix(s, "M") {
		v, _ := strconv.ParseInt(strings.TrimSuffix(s, "M"), 10, 64)
		return v
	}
	v, _ := strconv.ParseInt(s, 10, 64)
	return v / 1024 / 1024
}

func writeJSON(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}

func handleStatus(w http.ResponseWriter, r *http.Request) {
	mem := getMemoryInfo()
	procs := findMAAs()
	var totalMaaMem int64
	for _, p := range procs {
		totalMaaMem += p.MemMB
	}
	overloaded := mem.AvailableGB < 4.0 || totalMaaMem > 4096

	writeJSON(w, SystemStatus{
		Memory:    mem,
		Processes: procs,
		Overloaded: overloaded,
	})
}

func main() {
	port := "19996"
	if p := os.Getenv("HEALTH_MONITOR_PORT"); p != "" {
		port = p
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/status", handleStatus)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]string{"status": "ok", "service": "health_monitor"})
	})

	server := &http.Server{Addr: "127.0.0.1:" + port, Handler: mux}
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		server.Close()
	}()

	log.Printf("Health Monitor starting on 127.0.0.1:%s", port)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("Server error: %v", err)
	}
}
