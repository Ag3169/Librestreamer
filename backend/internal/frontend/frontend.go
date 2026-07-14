package frontend

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"

	"librestreamer/backend/internal/config"
	"librestreamer/backend/internal/metrics"
)

type Registrar struct {
	cfg       *config.Config
	collector *metrics.Collector
	client    *http.Client
}

func NewRegistrar(cfg *config.Config, collector *metrics.Collector) *Registrar {
	return &Registrar{
		cfg:       cfg,
		collector: collector,
		client:    &http.Client{Timeout: 10 * time.Second},
	}
}

func (r *Registrar) Start() {
	if !r.cfg.Frontend.Enabled {
		return
	}
	r.register()
	interval := time.Duration(r.cfg.Frontend.HeartbeatInterval) * time.Second
	go r.heartbeatLoop(interval)
}

func (r *Registrar) register() {
	frontendURL := fmt.Sprintf("http://%s:%d/api/backends/register",
		r.cfg.Frontend.FrontendHost, r.cfg.Frontend.FrontendPort)
	payload := map[string]any{
		"id":     r.cfg.Server.ID,
		"name":   r.cfg.Server.Name,
		"host":   r.cfg.Server.Host,
		"port":   r.cfg.Server.Port,
		"secret": r.cfg.Frontend.Secret,
		"type":   "librestreamer",
	}
	data, _ := json.Marshal(payload)
	resp, err := r.client.Post(frontendURL, "application/json", bytes.NewReader(data))
	if err != nil {
		log.Printf("[frontend] registration failed: %v", err)
		return
	}
	resp.Body.Close()
	if resp.StatusCode == 200 || resp.StatusCode == 201 {
		log.Printf("[frontend] registered with frontend at %s:%d", r.cfg.Frontend.FrontendHost, r.cfg.Frontend.FrontendPort)
	} else {
		log.Printf("[frontend] registration returned %d", resp.StatusCode)
	}
}

func (r *Registrar) heartbeatLoop(interval time.Duration) {
	t := time.NewTicker(interval)
	defer t.Stop()
	for range t.C {
		snap := r.collector.Snapshot()
		heartbeatURL := fmt.Sprintf("http://%s:%d/api/backends/heartbeat",
			r.cfg.Frontend.FrontendHost, r.cfg.Frontend.FrontendPort)
		payload := map[string]any{
			"id":      r.cfg.Server.ID,
			"name":    r.cfg.Server.Name,
			"host":    r.cfg.Server.Host,
			"port":    r.cfg.Server.Port,
			"secret":  r.cfg.Frontend.Secret,
			"metrics": snap,
		}
		data, _ := json.Marshal(payload)
		resp, err := r.client.Post(heartbeatURL, "application/json", bytes.NewReader(data))
		if err != nil {
			log.Printf("[frontend] heartbeat failed: %v", err)
			continue
		}
		resp.Body.Close()
	}
}
