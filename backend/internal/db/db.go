package db

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

type DB struct {
	*sql.DB
}

func Open(dataDir string) (*DB, error) {
	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		return nil, fmt.Errorf("create data dir: %w", err)
	}
	dbPath := filepath.Join(dataDir, "library.db")
	sdb, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	sdb.SetMaxOpenConns(1) // sqlite limitation
	d := &DB{sdb}
	if err := d.migrate(); err != nil {
		return nil, fmt.Errorf("migrate: %w", err)
	}
	return d, nil
}

func (d *DB) migrate() error {
	_, err := d.Exec(`
CREATE TABLE IF NOT EXISTS items (
	id TEXT PRIMARY KEY,
	type TEXT NOT NULL,
	title TEXT NOT NULL,
	year INTEGER DEFAULT 0,
	path TEXT NOT NULL,
	size INTEGER DEFAULT 0,
	mime_type TEXT,
	library_name TEXT,
	parent_id TEXT DEFAULT '',
	show_name TEXT DEFAULT '',
	season INTEGER DEFAULT 0,
	episode INTEGER DEFAULT 0,
	bitrate INTEGER DEFAULT 0,
	resolution TEXT DEFAULT '',
	codec TEXT DEFAULT '',
	duration INTEGER DEFAULT 0,
	has_thumbnail INTEGER DEFAULT 0,
	thumbnail_path TEXT DEFAULT '',
	created_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_type ON items(type);
CREATE INDEX IF NOT EXISTS idx_items_parent ON items(parent_id);
CREATE TABLE IF NOT EXISTS thumbnails (
	id TEXT PRIMARY KEY,
	path TEXT NOT NULL,
	width INTEGER DEFAULT 0,
	height INTEGER DEFAULT 0
);
`)
	return err
}

func (d *DB) ClearItems() error {
	_, err := d.Exec("DELETE FROM items")
	return err
}

func (d *DB) InsertItem(it Item) error {
	_, err := d.Exec(
		`INSERT OR REPLACE INTO items (id,type,title,year,path,size,mime_type,library_name,
			parent_id,show_name,season,episode,bitrate,resolution,codec,duration,
			has_thumbnail,thumbnail_path,created_at)
		 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		it.ID, it.Type, it.Title, it.Year, it.Path, it.Size, it.MimeType,
		it.LibraryName, it.ParentID, it.ShowName, it.Season, it.Episode,
		it.Bitrate, it.Resolution, it.Codec, it.Duration,
		btoi(it.HasThumbnail), it.ThumbnailPath, it.CreatedAt,
	)
	return err
}

type Item struct {
	ID            string
	Type          string
	Title         string
	Year          int
	Path          string
	Size          int64
	MimeType      string
	LibraryName   string
	ParentID      string
	ShowName      string
	Season        int
	Episode       int
	Bitrate       int
	Resolution    string
	Codec         string
	Duration      int
	HasThumbnail  bool
	ThumbnailPath string
	CreatedAt     int64
}

func btoi(b bool) int {
	if b {
		return 1
	}
	return 0
}

func itob(i int) bool {
	return i != 0
}

func (d *DB) AllItems() ([]Item, error) {
	rows, err := d.Query(`SELECT id,type,title,year,path,size,mime_type,library_name,
		parent_id,show_name,season,episode,bitrate,resolution,codec,duration,
		has_thumbnail,thumbnail_path,created_at FROM items ORDER BY title`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Item
	for rows.Next() {
		var it Item
		var ht int
		if err := rows.Scan(&it.ID, &it.Type, &it.Title, &it.Year, &it.Path, &it.Size,
			&it.MimeType, &it.LibraryName, &it.ParentID, &it.ShowName, &it.Season,
			&it.Episode, &it.Bitrate, &it.Resolution, &it.Codec, &it.Duration,
			&ht, &it.ThumbnailPath, &it.CreatedAt); err != nil {
			return nil, err
		}
		it.HasThumbnail = itob(ht)
		out = append(out, it)
	}
	return out, nil
}

func (d *DB) ItemsByType(t string) ([]Item, error) {
	rows, err := d.Query(`SELECT id,type,title,year,path,size,mime_type,library_name,
		parent_id,show_name,season,episode,bitrate,resolution,codec,duration,
		has_thumbnail,thumbnail_path,created_at FROM items WHERE type=? ORDER BY title`, t)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Item
	for rows.Next() {
		var it Item
		var ht int
		if err := rows.Scan(&it.ID, &it.Type, &it.Title, &it.Year, &it.Path, &it.Size,
			&it.MimeType, &it.LibraryName, &it.ParentID, &it.ShowName, &it.Season,
			&it.Episode, &it.Bitrate, &it.Resolution, &it.Codec, &it.Duration,
			&ht, &it.ThumbnailPath, &it.CreatedAt); err != nil {
			return nil, err
		}
		it.HasThumbnail = itob(ht)
		out = append(out, it)
	}
	return out, nil
}

func (d *DB) GetItem(id string) (*Item, error) {
	row := d.QueryRow(`SELECT id,type,title,year,path,size,mime_type,library_name,
		parent_id,show_name,season,episode,bitrate,resolution,codec,duration,
		has_thumbnail,thumbnail_path,created_at FROM items WHERE id=?`, id)
	var it Item
	var ht int
	err := row.Scan(&it.ID, &it.Type, &it.Title, &it.Year, &it.Path, &it.Size,
		&it.MimeType, &it.LibraryName, &it.ParentID, &it.ShowName, &it.Season,
		&it.Episode, &it.Bitrate, &it.Resolution, &it.Codec, &it.Duration,
		&ht, &it.ThumbnailPath, &it.CreatedAt)
	if err != nil {
		return nil, err
	}
	it.HasThumbnail = itob(ht)
	return &it, nil
}

func (d *DB) Children(parentID string) ([]Item, error) {
	rows, err := d.Query(`SELECT id,type,title,year,path,size,mime_type,library_name,
		parent_id,show_name,season,episode,bitrate,resolution,codec,duration,
		has_thumbnail,thumbnail_path,created_at FROM items WHERE parent_id=? ORDER BY season,episode`, parentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Item
	for rows.Next() {
		var it Item
		var ht int
		if err := rows.Scan(&it.ID, &it.Type, &it.Title, &it.Year, &it.Path, &it.Size,
			&it.MimeType, &it.LibraryName, &it.ParentID, &it.ShowName, &it.Season,
			&it.Episode, &it.Bitrate, &it.Resolution, &it.Codec, &it.Duration,
			&ht, &it.ThumbnailPath, &it.CreatedAt); err != nil {
			return nil, err
		}
		it.HasThumbnail = itob(ht)
		out = append(out, it)
	}
	return out, nil
}

func (d *DB) ThumbnailPath(id string) (string, error) {
	var path string
	err := d.QueryRow("SELECT thumbnail_path FROM items WHERE id=?", id).Scan(&path)
	return path, err
}
