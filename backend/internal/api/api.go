package api

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"time"

	"librestreamer/backend/internal/db"
	"librestreamer/backend/internal/metrics"
	"librestreamer/backend/internal/stream"
	"librestreamer/backend/internal/upload"
)

type Server struct {
	secret     string
	serverID   string
	serverName string
	database   *db.DB
	collector  *metrics.Collector
	streamSvc  *stream.StreamService
	uploadSvc  *upload.Service
	srv        *http.Server
	metricsSrv *http.Server
	RescanFn   func() (int, error)
}

func New(secret, serverID, serverName string, database *db.DB,
	collector *metrics.Collector, streamSvc *stream.StreamService,
	uploadSvc *upload.Service, addr string, metricsPort int) *Server {
	s := &Server{
		secret:     secret,
		serverID:   serverID,
		serverName: serverName,
		database:   database,
		collector:  collector,
		streamSvc:  streamSvc,
		uploadSvc:  uploadSvc,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.health)
	mux.HandleFunc("/api/library", s.auth(s.library))
	mux.HandleFunc("/api/library/", s.auth(s.libraryItem))
	mux.HandleFunc("/api/stream/", s.auth(s.streamHandler))
	mux.HandleFunc("/api/hls/", s.auth(s.hlsHandler))
	mux.HandleFunc("/api/thumbnail/", s.auth(s.thumbnailHandler))
	mux.HandleFunc("/api/metrics", s.auth(s.metricsHandler))
	mux.HandleFunc("/ws/metrics", s.authWS(s.metricsWS))
	mux.HandleFunc("/api/rescan", s.auth(s.rescan))
	mux.HandleFunc("/api/upload", s.auth(s.uploadHandler))
	mux.HandleFunc("/api/dir", s.auth(s.dirHandler))
	s.srv = &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}

	// Separate metrics server if enabled
	if metricsPort > 0 {
		metricsMux := http.NewServeMux()
		metricsMux.HandleFunc("/metrics", collector.ServeHTTP)
		metricsMux.HandleFunc("/ws/metrics", collector.HandleWS)
		s.metricsSrv = &http.Server{Addr: fmt.Sprintf(":%d", metricsPort),
			Handler: metricsMux, ReadHeaderTimeout: 10 * time.Second}
	}

	return s
}

func (s *Server) ListenAndServe() error {
	log.Printf("[api] %s listening on %s", s.serverName, s.srv.Addr)
	if s.metricsSrv != nil {
		go func() {
			log.Printf("[api] metrics on %s", s.metricsSrv.Addr)
			s.metricsSrv.ListenAndServe()
		}()
	}
	return s.srv.ListenAndServe()
}

func (s *Server) Shutdown(ctx context.Context) error {
	if s.metricsSrv != nil {
		s.metricsSrv.Shutdown(ctx)
	}
	return s.srv.Shutdown(ctx)
}

func (s *Server) auth(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		provided := r.Header.Get("X-Librestreamer-Secret")
		if provided == "" {
			provided = r.URL.Query().Get("token")
		}
		if provided != s.secret {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		h(w, r)
	}
}

func (s *Server) authWS(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		provided := r.Header.Get("X-Librestreamer-Secret")
		if provided == "" {
			provided = r.URL.Query().Get("token")
		}
		if provided != s.secret {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		h(w, r)
	}
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":  "ok",
		"server":  s.serverName,
		"id":      s.serverID,
		"version": "1.0.0",
	})
}

func (s *Server) library(w http.ResponseWriter, r *http.Request) {
	itemType := r.URL.Query().Get("type")
	var items []db.Item
	var err error
	if itemType != "" {
		items, err = s.database.ItemsByType(itemType)
	} else {
		items, err = s.database.AllItems()
	}
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"server": s.serverName,
		"items":  items,
		"count":  len(items),
	})
}

func (s *Server) libraryItem(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/library/")
	parts := strings.SplitN(path, "/", 2)
	if len(parts) < 2 {
		http.Error(w, "usage: /api/library/{type}/{id}", http.StatusBadRequest)
		return
	}
	itemType, itemID := parts[0], parts[1]
	it, err := s.database.GetItem(itemID)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	resp := map[string]any{"item": it}
	if it.Type == "show" || (itemType == "show") {
		children, _ := s.database.Children(itemID)
		resp["children"] = children
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) streamHandler(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/stream/")
	it, err := s.database.GetItem(id)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	s.collector.IncActiveStream()
	defer s.collector.DecActiveStream()
	s.streamSvc.ServeDirect(w, r, it)
}

func (s *Server) hlsHandler(w http.ResponseWriter, r *http.Request) {
	// /api/hls/{id} or /api/hls/{id}/{filename}
	path := strings.TrimPrefix(r.URL.Path, "/api/hls/")
	parts := strings.SplitN(path, "/", 2)
	itemID := parts[0]
	filename := ""
	if len(parts) > 1 {
		filename = parts[1]
	}
	it, err := s.database.GetItem(itemID)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	s.streamSvc.ServeHLS(w, r, it, filename)
}

func (s *Server) thumbnailHandler(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/thumbnail/")
	it, err := s.database.GetItem(id)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	s.streamSvc.ServeThumbnail(w, r, it)
}

func (s *Server) metricsHandler(w http.ResponseWriter, r *http.Request) {
	s.collector.ServeHTTP(w, r)
}

func (s *Server) metricsWS(w http.ResponseWriter, r *http.Request) {
	s.collector.HandleWS(w, r)
}

func (s *Server) rescan(w http.ResponseWriter, r *http.Request) {
	if s.RescanFn == nil {
		http.Error(w, "rescan not wired", http.StatusNotImplemented)
		return
	}
	n, err := s.RescanFn()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "items": n})
}

func (s *Server) uploadHandler(w http.ResponseWriter, r *http.Request) {
	if s.uploadSvc == nil {
		http.Error(w, "upload not configured", http.StatusNotImplemented)
		return
	}
	s.uploadSvc.HandleUpload(w, r)
}

func (s *Server) dirHandler(w http.ResponseWriter, r *http.Request) {
	if s.uploadSvc == nil {
		http.Error(w, "not configured", http.StatusNotImplemented)
		return
	}
	s.uploadSvc.HandleListDir(w, r)
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}
