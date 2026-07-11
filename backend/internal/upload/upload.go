package upload

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

type Service struct {
	mediaPaths []string
}

func New(mediaPaths []string) *Service {
	return &Service{mediaPaths: mediaPaths}
}

func (s *Service) resolveTarget(category, subpath string) (string, error) {
	cat := strings.ToLower(strings.TrimSpace(category))
	if cat == "" {
		cat = "movies"
	}
	switch cat {
	case "movie":
		cat = "movies"
	case "tv", "shows", "tvshows":
		cat = "tv"
	case "audio":
		cat = "music"
	}

	// Find matching media path for category
	var base string
	for _, mp := range s.mediaPaths {
		low := strings.ToLower(filepath.Base(mp))
		if (cat == "movies" && strings.Contains(low, "movie")) ||
			(cat == "tv" && (strings.Contains(low, "tv") || strings.Contains(low, "show"))) ||
			(cat == "music" && (strings.Contains(low, "music") || strings.Contains(low, "audio"))) {
			base = mp
			break
		}
	}
	if base == "" && len(s.mediaPaths) > 0 {
		base = s.mediaPaths[0]
	}
	if base == "" {
		return "", fmt.Errorf("no media path configured for category %s", cat)
	}

	if subpath != "" {
		subpath = filepath.Clean("/" + subpath)
		if strings.Contains(subpath, "..") {
			return "", fmt.Errorf("invalid subpath")
		}
	}
	return filepath.Join(base, subpath), nil
}

func (s *Service) HandleUpload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := r.ParseMultipartForm(1 << 30); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}

	category := r.FormValue("category")
	subpath := r.FormValue("subpath")

	targetDir, err := s.resolveTarget(category, subpath)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if err := os.MkdirAll(targetDir, 0o755); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}

	files := r.MultipartForm.File["file"]
	if len(files) == 0 {
		files = r.MultipartForm.File["files"]
	}
	if len(files) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "no files provided"})
		return
	}

	var saved []string
	for _, fh := range files {
		safeName := filepath.Base(fh.Filename)
		if safeName == "." || safeName == "/" || safeName == "" {
			continue
		}
		destPath := filepath.Join(targetDir, safeName)
		src, err := fh.Open()
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
			return
		}
		dst, err := os.Create(destPath)
		if err != nil {
			src.Close()
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
			return
		}
		written, err := io.Copy(dst, src)
		src.Close()
		dst.Close()
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
			return
		}
		log.Printf("[upload] saved %s (%d bytes)", safeName, written)
		saved = append(saved, destPath)
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status": "ok",
		"saved":  saved,
		"count":  len(saved),
	})
}

func (s *Service) HandleListDir(w http.ResponseWriter, r *http.Request) {
	category := r.URL.Query().Get("category")
	subpath := r.URL.Query().Get("subpath")

	targetDir, err := s.resolveTarget(category, subpath)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}

	entries, err := os.ReadDir(targetDir)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"entries": []any{}, "path": targetDir})
		return
	}
	var out []any
	for _, e := range entries {
		info, _ := e.Info()
		out = append(out, map[string]any{
			"name":  e.Name(),
			"isDir": e.IsDir(),
			"size": func() int64 {
				if info != nil {
					return info.Size()
				}
				return 0
			}(),
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"entries": out, "path": targetDir})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}
