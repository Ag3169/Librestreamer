package config

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

type TranscodingCfg struct {
	Enabled              bool   `json:"enabled"`
	HardwareAccel        string `json:"hardware_accel"`
	MaxConcurrentStreams int    `json:"max_concurrent_streams"`
}

type ServerCfg struct {
	ID          string         `json:"id"`
	Name        string         `json:"name"`
	Host        string         `json:"host"`
	Port        int            `json:"port"`
	DataDir     string         `json:"data_dir"`
	MediaPaths  []string       `json:"media_paths"`
	Transcoding TranscodingCfg `json:"transcoding"`
}

type FrontendCfg struct {
	Enabled           bool   `json:"enabled"`
	FrontendHost      string `json:"frontend_host"`
	FrontendPort      int    `json:"frontend_port"`
	Secret            string `json:"secret"`
	HeartbeatInterval int    `json:"heartbeat_interval"`
}

type MonitoringCfg struct {
	Enabled     bool `json:"enabled"`
	MetricsPort int  `json:"metrics_port"`
}

type Config struct {
	Server     ServerCfg     `json:"server"`
	Frontend   FrontendCfg   `json:"frontend"`
	Monitoring MonitoringCfg `json:"monitoring"`
}

func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}
	var c Config
	if err := json.Unmarshal(data, &c); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}
	if c.Server.Name == "" {
		c.Server.Name = "librestreamer-server"
	}
	if c.Server.Port == 0 {
		c.Server.Port = 8080
	}
	if c.Server.ID == "" {
		c.Server.ID = "auto-" + fmt.Sprintf("%d", os.Getpid())
	}
	if c.Server.DataDir == "" {
		c.Server.DataDir = "./data"
	}
	if len(c.Server.MediaPaths) == 0 {
		return nil, fmt.Errorf("config: at least one media_path is required")
	}
	if c.Server.Transcoding.MaxConcurrentStreams == 0 {
		c.Server.Transcoding.MaxConcurrentStreams = 4
	}
	if c.Frontend.HeartbeatInterval == 0 {
		c.Frontend.HeartbeatInterval = 30
	}
	applyEnvOverrides(&c)
	return &c, nil
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		var n int
		fmt.Sscanf(v, "%d", &n)
		if n > 0 {
			return n
		}
	}
	return fallback
}

func envBool(key string, fallback bool) bool {
	if v := os.Getenv(key); v != "" {
		return v == "1" || v == "true" || v == "yes"
	}
	return fallback
}

func applyEnvOverrides(c *Config) {
	c.Server.ID = envOr("LIBRESTREAMER_SERVER_ID", c.Server.ID)
	c.Server.Name = envOr("LIBRESTREAMER_SERVER_NAME", c.Server.Name)
	c.Server.Host = envOr("LIBRESTREAMER_SERVER_HOST", c.Server.Host)
	c.Server.Port = envInt("LIBRESTREAMER_SERVER_PORT", c.Server.Port)
	c.Server.DataDir = envOr("LIBRESTREAMER_DATA_DIR", c.Server.DataDir)
	c.Frontend.Secret = envOr("LIBRESTREAMER_FRONTEND_SECRET", c.Frontend.Secret)
	c.Frontend.FrontendHost = envOr("LIBRESTREAMER_FRONTEND_HOST", c.Frontend.FrontendHost)
	c.Frontend.FrontendPort = envInt("LIBRESTREAMER_FRONTEND_PORT", c.Frontend.FrontendPort)
	c.Frontend.Enabled = envBool("LIBRESTREAMER_FRONTEND_ENABLED", c.Frontend.Enabled)
	c.Monitoring.Enabled = envBool("LIBRESTREAMER_MONITORING_ENABLED", c.Monitoring.Enabled)
	c.Monitoring.MetricsPort = envInt("LIBRESTREAMER_METRICS_PORT", c.Monitoring.MetricsPort)
	if v := os.Getenv("LIBRESTREAMER_MEDIA_PATHS"); v != "" {
		c.Server.MediaPaths = splitPaths(v)
	}
}

func splitPaths(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

func DefaultPath() string {
	if p := os.Getenv("LIBRESTREAMER_CONFIG"); p != "" {
		return p
	}
	return "config.json"
}
