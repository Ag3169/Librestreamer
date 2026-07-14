package scanner

import (
	"crypto/sha1"
	"encoding/hex"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"librestreamer/backend/internal/db"
)

type Scanner struct {
	database   *db.DB
	dataDir    string
	mediaPaths []string
	serverName string
}

func New(database *db.DB, dataDir string, mediaPaths []string, serverName string) *Scanner {
	return &Scanner{
		database:   database,
		dataDir:    dataDir,
		mediaPaths: mediaPaths,
		serverName: serverName,
	}
}

func (s *Scanner) Scan() (int, error) {
	var allItems []db.Item
	now := time.Now().Unix()

	for _, mediaPath := range s.mediaPaths {
		items := s.scanPath(mediaPath)
		for i := range items {
			items[i].CreatedAt = now
		}
		allItems = append(allItems, items...)
	}

	if err := s.database.ClearItems(); err != nil {
		return 0, fmt.Errorf("clear items: %w", err)
	}
	for _, it := range allItems {
		if err := s.database.InsertItem(it); err != nil {
			log.Printf("[scanner] insert error: %v", err)
		}
	}
	log.Printf("[scanner] indexed %d items", len(allItems))
	return len(allItems), nil
}

func (s *Scanner) scanPath(root string) []db.Item {
	st, err := os.Stat(root)
	if err != nil {
		log.Printf("[scanner] cannot stat %s: %v", root, err)
		return nil
	}
	if !st.IsDir() {
		return nil
	}
	// detect category from folder name
	base := strings.ToLower(filepath.Base(root))
	category := "movies"
	switch {
	case strings.Contains(base, "movie"):
		category = "movies"
	case strings.Contains(base, "tv") || strings.Contains(base, "show"):
		category = "tv"
	case strings.Contains(base, "music") || strings.Contains(base, "audio"):
		category = "music"
	}

	switch category {
	case "movies":
		return s.scanMovies(root)
	case "tv":
		return s.scanTV(root)
	default:
		return s.scanMusic(root)
	}
}

func (s *Scanner) scanMovies(root string) []db.Item {
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	var items []db.Item
	thumbDir := filepath.Join(s.dataDir, "thumbnails")
	os.MkdirAll(thumbDir, 0o755)

	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		movieDir := filepath.Join(root, e.Name())
		mediaFile := findMediaFile(movieDir)
		if mediaFile == "" {
			continue
		}
		title, year := parseTitleYear(e.Name())
		id := hashID(s.serverName, mediaFile)
		st, _ := os.Stat(mediaFile)
		thumbPath := filepath.Join(thumbDir, id+".jpg")
		hasThumb := generateThumbnail(mediaFile, thumbPath, 320, 180)

		items = append(items, db.Item{
			ID:            id,
			Type:          "movie",
			Title:         title,
			Year:          year,
			Path:          mediaFile,
			Size:          st.Size(),
			MimeType:      mimeTypeForExt(filepath.Ext(mediaFile)),
			LibraryName:   filepath.Base(root),
			Resolution:    probeResolution(mediaFile),
			Codec:         probeCodec(mediaFile),
			Duration:      probeDuration(mediaFile),
			HasThumbnail:  hasThumb,
			ThumbnailPath: thumbPath,
		})
	}
	return items
}

func (s *Scanner) scanTV(root string) []db.Item {
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	var items []db.Item
	thumbDir := filepath.Join(s.dataDir, "thumbnails")
	os.MkdirAll(thumbDir, 0o755)

	for _, showEntry := range entries {
		if !showEntry.IsDir() {
			continue
		}
		showDir := filepath.Join(root, showEntry.Name())
		showID := hashID(s.serverName, showDir)
		showThumb := filepath.Join(thumbDir, showID+".jpg")

		// check if there's a poster image
		posterPath := findImageFile(showDir)
		if posterPath != "" {
			copyFile(posterPath, showThumb)
		} else {
			// try to generate from first episode
			firstEp := findMediaFile(showDir)
			if firstEp != "" {
				generateThumbnail(firstEp, showThumb, 320, 180)
			}
		}

		hasShowThumb := fileExists(showThumb)
		items = append(items, db.Item{
			ID:            showID,
			Type:          "show",
			Title:         showEntry.Name(),
			Path:          showDir,
			LibraryName:   filepath.Base(root),
			HasThumbnail:  hasShowThumb,
			ThumbnailPath: showThumb,
		})

		// scan seasons
		seasons, err := os.ReadDir(showDir)
		if err != nil {
			continue
		}
		for _, seasonEntry := range seasons {
			if !seasonEntry.IsDir() {
				continue
			}
			seasonDir := filepath.Join(showDir, seasonEntry.Name())
			seasonNum := parseSeasonNum(seasonEntry.Name())
			eps, err := os.ReadDir(seasonDir)
			if err != nil {
				continue
			}
			for _, ep := range eps {
				if ep.IsDir() {
					continue
				}
				ext := strings.ToLower(filepath.Ext(ep.Name()))
				if !isMediaExt(ext) {
					continue
				}
				epPath := filepath.Join(seasonDir, ep.Name())
				epID := hashID(s.serverName, epPath)
				epNum, epTitle := parseEpisodeInfo(ep.Name())
				st, _ := os.Stat(epPath)
				epThumb := filepath.Join(thumbDir, epID+".jpg")
				hasEpThumb := generateThumbnail(epPath, epThumb, 320, 180)

				items = append(items, db.Item{
					ID:            epID,
					Type:          "episode",
					Title:         fmt.Sprintf("S%02dE%02d %s", seasonNum, epNum, epTitle),
					Path:          epPath,
					Size:          st.Size(),
					MimeType:      mimeTypeForExt(ext),
					LibraryName:   filepath.Base(root),
					ParentID:      showID,
					ShowName:      showEntry.Name(),
					Season:        seasonNum,
					Episode:       epNum,
					Resolution:    probeResolution(epPath),
					Codec:         probeCodec(epPath),
					Duration:      probeDuration(epPath),
					HasThumbnail:  hasEpThumb,
					ThumbnailPath: epThumb,
				})
			}
		}
	}
	return items
}

func (s *Scanner) scanMusic(root string) []db.Item {
	var items []db.Item
	thumbDir := filepath.Join(s.dataDir, "thumbnails")
	os.MkdirAll(thumbDir, 0o755)

	filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		ext := strings.ToLower(filepath.Ext(d.Name()))
		if !isMediaExt(ext) {
			return nil
		}
		id := hashID(s.serverName, path)
		st, _ := os.Stat(path)
		items = append(items, db.Item{
			ID:          id,
			Type:        "music",
			Title:       strings.TrimSuffix(d.Name(), filepath.Ext(d.Name())),
			Path:        path,
			Size:        st.Size(),
			MimeType:    mimeTypeForExt(ext),
			LibraryName: filepath.Base(root),
			Bitrate:     probeBitrate(path),
			Duration:    probeDuration(path),
		})
		return nil
	})
	return items
}

// --- helpers ---

var mediaExts = map[string]string{
	".mkv":  "video/x-matroska",
	".mp4":  "video/mp4",
	".webm": "video/webm",
	".avi":  "video/x-msvideo",
	".mov":  "video/quicktime",
	".mp3":  "audio/mpeg",
	".flac": "audio/flac",
	".wav":  "audio/wav",
	".m4a":  "audio/mp4",
}

var imageExts = map[string]bool{
	".jpg": true, ".jpeg": true, ".png": true, ".webp": true,
}

func isMediaExt(ext string) bool {
	_, ok := mediaExts[ext]
	return ok
}

func hashID(parts ...string) string {
	h := sha1.New()
	fmt.Fprintln(h, strings.Join(parts, "|"))
	return hex.EncodeToString(h.Sum(nil))[:16]
}

func findMediaFile(dir string) string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return ""
	}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		if isMediaExt(strings.ToLower(filepath.Ext(e.Name()))) {
			return filepath.Join(dir, e.Name())
		}
	}
	return ""
}

func findImageFile(dir string) string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return ""
	}
	candidates := []string{"poster", "folder", "cover", "default"}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		base := strings.TrimSuffix(strings.ToLower(e.Name()), filepath.Ext(e.Name()))
		ext := strings.ToLower(filepath.Ext(e.Name()))
		if !imageExts[ext] {
			continue
		}
		for _, c := range candidates {
			if base == c {
				return filepath.Join(dir, e.Name())
			}
		}
	}
	for _, e := range entries {
		if !e.IsDir() && imageExts[strings.ToLower(filepath.Ext(e.Name()))] {
			return filepath.Join(dir, e.Name())
		}
	}
	return ""
}

func parseTitleYear(name string) (string, int) {
	i := strings.LastIndex(name, "(")
	if i < 0 {
		return name, 0
	}
	rest := strings.TrimSuffix(name[i+1:], ")")
	var y int
	fmt.Sscanf(rest, "%d", &y)
	return strings.TrimSpace(name[:i]), y
}

func parseSeasonNum(name string) int {
	var n int
	fmt.Sscanf(strings.ToLower(name), "season %d", &n)
	if n == 0 {
		fmt.Sscanf(name, "S%d", &n)
	}
	if n == 0 {
		fmt.Sscanf(name, "%d", &n)
	}
	return n
}

func parseEpisodeInfo(name string) (int, string) {
	lower := strings.ToLower(name)
	var n int
	idx := strings.Index(lower, "s0")
	if idx >= 0 {
		rest := lower[idx:]
		var s, e int
		if _, err := fmt.Sscanf(rest, "s%de%d", &s, &e); err == nil && e > 0 {
			n = e
		}
	}
	if n == 0 {
		fmt.Sscanf(lower, "%d", &n)
	}
	title := strings.TrimSuffix(name, filepath.Ext(name))
	if i := strings.Index(title, " - "); i >= 0 {
		return n, strings.TrimSpace(title[i+3:])
	}
	return n, title
}

func mimeTypeForExt(ext string) string {
	if m, ok := mediaExts[strings.ToLower(ext)]; ok {
		return m
	}
	return "application/octet-stream"
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func copyFile(src, dst string) {
	data, err := os.ReadFile(src)
	if err != nil {
		return
	}
	os.WriteFile(dst, data, 0o644)
}

// generateThumbnail uses ffmpeg to extract a thumbnail from a video file.
// Returns true if successful.
func generateThumbnail(videoPath, thumbPath string, w, h int) bool {
	if fileExists(thumbPath) {
		return true
	}
	// try ffmpeg
	cmd := exec.Command("ffmpeg", "-y", "-i", videoPath,
		"-ss", "00:00:05", "-vframes", "1",
		"-vf", fmt.Sprintf("scale=%d:%d", w, h),
		"-q:v", "2", thumbPath)
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Run(); err != nil {
		// ffmpeg not available or failed - create placeholder
		return false
	}
	return fileExists(thumbPath)
}

// probe functions use ffprobe to extract metadata
func probeResolution(path string) string {
	out, err := exec.Command("ffprobe", "-v", "error", "-select_streams", "v:0",
		"-show_entries", "stream=width,height", "-of", "csv=p=0", path).Output()
	if err != nil {
		return ""
	}
	s := strings.TrimSpace(string(out))
	if s == "" {
		return ""
	}
	parts := strings.Split(s, ",")
	if len(parts) >= 2 {
		w, _ := strconv.Atoi(parts[0])
		h, _ := strconv.Atoi(parts[1])
		return fmt.Sprintf("%dx%d", w, h)
	}
	return s
}

func probeCodec(path string) string {
	out, err := exec.Command("ffprobe", "-v", "error", "-select_streams", "v:0",
		"-show_entries", "stream=codec_name", "-of", "csv=p=0", path).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func probeDuration(path string) int {
	out, err := exec.Command("ffprobe", "-v", "error",
		"-show_entries", "format=duration", "-of", "csv=p=0", path).Output()
	if err != nil {
		return 0
	}
	f, _ := strconv.ParseFloat(strings.TrimSpace(string(out)), 64)
	return int(f)
}

func probeBitrate(path string) int {
	out, err := exec.Command("ffprobe", "-v", "error",
		"-show_entries", "format=bit_rate", "-of", "csv=p=0", path).Output()
	if err != nil {
		return 0
	}
	n, _ := strconv.Atoi(strings.TrimSpace(string(out)))
	return n
}
