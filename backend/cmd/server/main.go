package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"librestreamer/backend/internal/api"
	"librestreamer/backend/internal/config"
	"librestreamer/backend/internal/db"
	"librestreamer/backend/internal/frontend"
	"librestreamer/backend/internal/metrics"
	"librestreamer/backend/internal/scanner"
	"librestreamer/backend/internal/stream"
	"librestreamer/backend/internal/upload"
)

func main() {
	cfgPath := flag.String("config", config.DefaultPath(), "path to config.json")
	flag.Parse()

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	// Open database
	database, err := db.Open(cfg.Server.DataDir)
	if err != nil {
		log.Fatalf("db: %v", err)
	}
	defer database.Close()

	// Scanner
	sc := scanner.New(database, cfg.Server.DataDir, cfg.Server.MediaPaths, cfg.Server.Name)

	scanFn := func() (int, error) {
		log.Printf("[server] %s scanning media paths...", cfg.Server.Name)
		n, err := sc.Scan()
		if err != nil {
			log.Printf("[server] scan error: %v", err)
		} else {
			log.Printf("[server] %s indexed %d items", cfg.Server.Name, n)
		}
		return n, err
	}
	scanFn()

	// Metrics collector
	collector := metrics.NewCollector(cfg.Server.ID, cfg.Server.Name)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	collector.Start(ctx, 5*time.Second)

	// Stream service
	streamSvc := stream.New(database, cfg.Server.DataDir,
		cfg.Server.Transcoding.Enabled, cfg.Server.Transcoding.HardwareAccel,
		cfg.Server.Transcoding.MaxConcurrentStreams)

	// Upload service
	uploadSvc := upload.New(cfg.Server.MediaPaths)

	// API server
	addr := fmt.Sprintf("%s:%d", cfg.Server.Host, cfg.Server.Port)
	metricsPort := 0
	if cfg.Monitoring.Enabled {
		metricsPort = cfg.Monitoring.MetricsPort
	}
	srv := api.New(cfg.Frontend.Secret, cfg.Server.ID, cfg.Server.Name,
		database, collector, streamSvc, uploadSvc, addr, metricsPort)
	srv.RescanFn = scanFn

	go func() {
		if err := srv.ListenAndServe(); err != nil && err.Error() != "http: Server closed" {
			log.Fatalf("server: %v", err)
		}
	}()

	// Frontend registration
	registrar := frontend.NewRegistrar(cfg, collector)
	registrar.Start()

	// Wait for shutdown
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	log.Printf("[server] shutting down")
	cancel()
	shutCtx, shutCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer shutCancel()
	srv.Shutdown(shutCtx)
}
