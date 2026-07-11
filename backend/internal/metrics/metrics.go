package metrics

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"runtime"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/mem"
	"librestreamer/backend/internal/nvidia"
)

type Snapshot struct {
	ServerID      string  `json:"server_id"`
	ServerName    string  `json:"server_name"`
	CPUUsagePct   float64 `json:"cpu_usage_pct"`
	CPUCores      int     `json:"cpu_cores"`
	MemoryTotal   uint64  `json:"memory_total_bytes"`
	MemoryUsed    uint64  `json:"memory_used_bytes"`
	MemoryPct     float64 `json:"memory_usage_pct"`
	DiskTotal     uint64  `json:"disk_total_bytes"`
	DiskUsed      uint64  `json:"disk_used_bytes"`
	DiskPct       float64 `json:"disk_usage_pct"`
	GPUName       string  `json:"gpu_name,omitempty"`
	GPUUsagePct   float64 `json:"gpu_usage_pct,omitempty"`
	GPUMemUsed    uint64  `json:"gpu_mem_used_bytes,omitempty"`
	GPUMemTotal   uint64  `json:"gpu_mem_total_bytes,omitempty"`
	ActiveStreams int     `json:"active_streams"`
	NetworkRx     uint64  `json:"network_rx_bytes,omitempty"`
	NetworkTx     uint64  `json:"network_tx_bytes,omitempty"`
	UptimeSeconds int64   `json:"uptime_seconds"`
	Timestamp     int64   `json:"timestamp"`
}

type Collector struct {
	serverID      string
	serverName    string
	mu            sync.RWMutex
	snap          *Snapshot
	startTime     time.Time
	activeStreams int
}

func NewCollector(serverID, serverName string) *Collector {
	c := &Collector{
		serverID:   serverID,
		serverName: serverName,
		startTime:  time.Now(),
	}
	c.snap = c.collect()
	return c
}

func (c *Collector) Start(ctx context.Context, every time.Duration) {
	go func() {
		t := time.NewTicker(every)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				s := c.collect()
				c.mu.Lock()
				c.snap = s
				c.mu.Unlock()
			}
		}
	}()
}

func (c *Collector) Snapshot() *Snapshot {
	c.mu.RLock()
	defer c.mu.RUnlock()
	if c.snap == nil {
		return &Snapshot{ServerID: c.serverID, ServerName: c.serverName}
	}
	out := *c.snap
	out.ActiveStreams = c.activeStreams
	out.UptimeSeconds = int64(time.Since(c.startTime).Seconds())
	return &out
}

func (c *Collector) IncActiveStream() {
	c.mu.Lock()
	c.activeStreams++
	c.mu.Unlock()
}

func (c *Collector) DecActiveStream() {
	c.mu.Lock()
	if c.activeStreams > 0 {
		c.activeStreams--
	}
	c.mu.Unlock()
}

func (c *Collector) collect() *Snapshot {
	s := &Snapshot{
		ServerID:   c.serverID,
		ServerName: c.serverName,
		CPUCores:   runtime.NumCPU(),
		Timestamp:  time.Now().Unix(),
	}
	if v, err := mem.VirtualMemory(); err == nil {
		s.MemoryTotal = v.Total
		s.MemoryUsed = v.Used
		s.MemoryPct = v.UsedPercent
	}
	if pcts, err := cpu.Percent(time.Second, false); err == nil && len(pcts) > 0 {
		s.CPUUsagePct = pcts[0]
	}
	if d, err := disk.Usage("/"); err == nil {
		s.DiskTotal = d.Total
		s.DiskUsed = d.Used
		s.DiskPct = d.UsedPercent
	}
	if name, usage, used, total, ok := nvidia.Probe(); ok {
		s.GPUName = name
		s.GPUUsagePct = usage
		s.GPUMemUsed = used
		s.GPUMemTotal = total
	}
	return s
}

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

// HandleWS handles WebSocket connections for real-time metrics streaming.
func (c *Collector) HandleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[metrics] ws upgrade error: %v", err)
		return
	}
	defer conn.Close()

	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		snap := c.Snapshot()
		data, _ := json.Marshal(snap)
		if err := conn.WriteMessage(websocket.TextMessage, data); err != nil {
			break
		}
		select {
		case <-ticker.C:
		case <-r.Context().Done():
			return
		}
	}
}

// ServeHTTP returns the current snapshot as JSON.
func (c *Collector) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(c.Snapshot())
}
