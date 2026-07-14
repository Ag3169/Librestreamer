package stream

import (
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"

	"librestreamer/backend/internal/db"
)

type StreamService struct {
	database    *db.DB
	dataDir     string
	transcoding bool
	hwAccel     string
	maxStreams  int
}

func New(database *db.DB, dataDir string, transcoding bool, hwAccel string, maxStreams int) *StreamService {
	s := &StreamService{
		database:    database,
		dataDir:     dataDir,
		transcoding: transcoding,
		hwAccel:     hwAccel,
		maxStreams:  maxStreams,
	}
	s.logCapabilities()
	return s
}

// logCapabilities reports what's available at startup.
func (s *StreamService) logCapabilities() {
	ffmpeg := false
	ffprobe := false
	if cmd := exec.Command("ffmpeg", "-version"); cmd.Run() == nil {
		ffmpeg = true
	}
	if cmd := exec.Command("ffprobe", "-version"); cmd.Run() == nil {
		ffprobe = true
	}
	log.Printf("[stream] ffmpeg=%v ffprobe=%v transcoding=%v hwaccel=%s",
		ffmpeg, ffprobe, s.transcoding, s.hwAccel)
	if s.transcoding && s.hwAccel != "" && s.hwAccel != "none" {
		if !s.hardwareEncoderAvailable() {
			log.Printf("[stream] WARNING: hardware accel %q requested but not available, will fallback to software", s.hwAccel)
		}
	}
	if !ffmpeg {
		log.Printf("[stream] NOTE: ffmpeg not found - direct streaming only (no HLS, no thumbnails)")
	}
}

// hardwareEncoderAvailable checks if the requested hardware encoder is usable.
func (s *StreamService) hardwareEncoderAvailable() bool {
	var codec string
	switch s.hwAccel {
	case "nvenc":
		codec = "h264_nvenc"
	case "vaapi":
		codec = "h264_vaapi"
	case "qsv", "quicksync":
		codec = "h264_qsv"
	case "amd", "amf":
		codec = "h264_amf"
	default:
		return false
	}
	// Check if ffmpeg lists this encoder
	out, err := exec.Command("ffmpeg", "-encoders").Output()
	if err != nil {
		return false
	}
	return strings.Contains(string(out), codec)
}

// videoEncoder picks the best available encoder.
func (s *StreamService) videoEncoder() string {
	if s.transcoding && s.hwAccel != "" && s.hwAccel != "none" {
		if s.hardwareEncoderAvailable() {
			switch s.hwAccel {
			case "nvenc":
				return "h264_nvenc"
			case "vaapi":
				return "h264_vaapi"
			case "qsv", "quicksync":
				return "h264_qsv"
			case "amd", "amf":
				return "h264_amf"
			}
		}
		// hardware encoder requested but not available - fall back to software
		log.Printf("[stream] hardware encoder %s not available, using software (libx264)", s.hwAccel)
	}
	return "libx264"
}

// ffmpegAvailable checks if ffmpeg is installed and actually works.
func ffmpegAvailable() bool {
	cmd := exec.Command("ffmpeg", "-version")
	cmd.Stdout = nil
	cmd.Stderr = nil
	return cmd.Run() == nil
}

// ServeDirect streams the original file with HTTP Range support.
func (s *StreamService) ServeDirect(w http.ResponseWriter, r *http.Request, item *db.Item) {
	f, err := os.Open(item.Path)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	defer f.Close()

	st, err := f.Stat()
	if err != nil {
		http.Error(w, "stat error", http.StatusInternalServerError)
		return
	}
	size := st.Size()
	mime := item.MimeType
	if mime == "" {
		mime = "application/octet-stream"
	}
	w.Header().Set("Content-Type", mime)
	w.Header().Set("Accept-Ranges", "bytes")
	w.Header().Set("Cache-Control", "no-store")

	rng := r.Header.Get("Range")
	if rng == "" {
		w.Header().Set("Content-Length", strconv.FormatInt(size, 10))
		w.WriteHeader(http.StatusOK)
		io.Copy(w, f)
		return
	}

	const prefix = "bytes="
	if !strings.HasPrefix(rng, prefix) {
		http.Error(w, "invalid range", http.StatusRequestedRangeNotSatisfiable)
		return
	}
	rng = strings.TrimPrefix(rng, prefix)
	parts := strings.SplitN(rng, "-", 2)
	if len(parts) != 2 {
		http.Error(w, "invalid range", http.StatusRequestedRangeNotSatisfiable)
		return
	}
	start, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil {
		http.Error(w, "invalid range", http.StatusRequestedRangeNotSatisfiable)
		return
	}
	var end int64 = size - 1
	if parts[1] != "" {
		if e, err := strconv.ParseInt(parts[1], 10, 64); err == nil && e < end {
			end = e
		}
	}
	if start > end || start >= size {
		http.Error(w, "out of range", http.StatusRequestedRangeNotSatisfiable)
		return
	}
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		http.Error(w, "seek error", http.StatusInternalServerError)
		return
	}
	length := end - start + 1
	w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", start, end, size))
	w.Header().Set("Content-Length", strconv.FormatInt(length, 10))
	w.WriteHeader(http.StatusPartialContent)
	io.CopyN(w, f, length)
}

// ServeThumbnail serves a thumbnail/poster image.
func (s *StreamService) ServeThumbnail(w http.ResponseWriter, r *http.Request, item *db.Item) {
	if !item.HasThumbnail || item.ThumbnailPath == "" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, item.ThumbnailPath)
}

// StartHLS generates an HLS playlist for an item and returns the playlist path.
// If transcoding is disabled or ffmpeg is not available, it returns an error
// so the caller can fall back to direct streaming.
func (s *StreamService) StartHLS(item *db.Item, quality string) (string, error) {
	if !ffmpegAvailable() {
		return "", fmt.Errorf("ffmpeg not available")
	}

	hlsDir := filepath.Join(s.dataDir, "hls", item.ID)
	if err := os.MkdirAll(hlsDir, 0o755); err != nil {
		return "", err
	}
	playlistPath := filepath.Join(hlsDir, "playlist.m3u8")

	// Check if already generated
	if _, err := os.Stat(playlistPath); err == nil {
		return playlistPath, nil
	}

	// Build ffmpeg command for HLS transcoding
	encoder := s.videoEncoder()
	args := []string{"-y", "-i", item.Path}
	args = append(args, "-c:v", encoder, "-c:a", "aac")

	// Apply quality preset
	switch quality {
	case "720p":
		args = append(args, "-vf", "scale=-2:720")
	case "480p":
		args = append(args, "-vf", "scale=-2:480")
	default:
		// original quality
	}

	args = append(args,
		"-f", "hls",
		"-hls_time", "6",
		"-hls_list_size", "0",
		"-hls_segment_filename", filepath.Join(hlsDir, "seg_%03d.ts"),
		playlistPath,
	)

	log.Printf("[stream] generating HLS for %s with %s", item.Title, encoder)
	cmd := exec.Command("ffmpeg", args...)
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Run(); err != nil {
		// If hardware encoder failed, try software fallback
		if encoder != "libx264" {
			log.Printf("[stream] %s failed, falling back to libx264", encoder)
			args[4] = "libx264" // replace encoder in args
			cmd2 := exec.Command("ffmpeg", args...)
			cmd2.Stdout = nil
			cmd2.Stderr = nil
			if err2 := cmd2.Run(); err2 == nil {
				return playlistPath, nil
			}
		}
		return "", fmt.Errorf("hls generation failed: %w", err)
	}

	return playlistPath, nil
}

// ServeHLS serves HLS playlist and segment files.
// Falls back to direct streaming if HLS generation fails (no ffmpeg, no GPU, etc.)
func (s *StreamService) ServeHLS(w http.ResponseWriter, r *http.Request, item *db.Item, filename string) {
	hlsDir := filepath.Join(s.dataDir, "hls", item.ID)

	// Auto-generate if not exists
	if _, err := os.Stat(filepath.Join(hlsDir, "playlist.m3u8")); err != nil {
		if _, err := s.StartHLS(item, ""); err != nil {
			// HLS generation failed - fall back to direct streaming
			log.Printf("[stream] HLS failed for %s, falling back to direct: %v", item.Title, err)
			s.ServeDirect(w, r, item)
			return
		}
	}
	if filename == "" {
		filename = "playlist.m3u8"
	}
	fp := filepath.Join(hlsDir, filename)
	if !strings.HasPrefix(fp, hlsDir) {
		http.Error(w, "invalid path", http.StatusBadRequest)
		return
	}
	ext := strings.ToLower(filepath.Ext(filename))
	switch ext {
	case ".m3u8":
		w.Header().Set("Content-Type", "application/vnd.apple.mpegurl")
	case ".ts":
		w.Header().Set("Content-Type", "video/mp2t")
	}
	http.ServeFile(w, r, fp)
}
