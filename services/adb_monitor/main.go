package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"github.com/xiachk083-hub/MAAOrch/services/adb_monitor/internal"
)

func main() {
	port := "19998"
	if p := os.Getenv("ADB_MONITOR_PORT"); p != "" {
		port = p
	}

	mux := http.NewServeMux()

	// ADB operations
	mux.HandleFunc("/ping", internal.HandlePing)
	mux.HandleFunc("/connect", internal.HandleConnect)
	mux.HandleFunc("/devices", internal.HandleDevices)
	mux.HandleFunc("/health", internal.HandleADBHealth)

	// mumu-cli operations
	mux.HandleFunc("/mumu/info", internal.HandleMumuInfo)
	mux.HandleFunc("/mumu/launch", internal.HandleMumuLaunch)
	mux.HandleFunc("/mumu/shutdown", internal.HandleMumuShutdown)

	// Health check
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]string{"status": "ok", "service": "adb_monitor"})
	})

	server := &http.Server{
		Addr:    "127.0.0.1:" + port,
		Handler: mux,
	}

	// Graceful shutdown
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		log.Println("Shutting down...")
		server.Close()
	}()

	log.Printf("ADB Monitor starting on 127.0.0.1:%s", port)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("Server error: %v", err)
	}
}
