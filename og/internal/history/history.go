package history

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/robbiemu/original_gangster/og/internal/config"
)

// HistoryRecord defines the structure for a single history entry.
type HistoryRecord struct {
	TS    string `json:"ts"`
	Hash  string `json:"hash"`
	CWD   string `json:"cwd"`
	Query string `json:"query"`
}

// GetHistoryPath returns the full path to the history file.
func GetHistoryPath() (string, error) {
	dir, err := config.GetDataDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "history.json"), nil
}

// AppendRecord appends a new history record to the history file.
func AppendRecord(rec HistoryRecord) error {
	path, err := GetHistoryPath()
	if err != nil {
		return fmt.Errorf("failed to get history path: %w", err)
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil { // Ensure directory exists
		return fmt.Errorf("failed to create history directory %s: %w", dir, err)
	}

	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return fmt.Errorf("failed to open history file %s: %w", path, err)
	}
	defer f.Close()

	b, err := json.Marshal(rec)
	if err != nil {
		return fmt.Errorf("failed to marshal history record: %w", err)
	}
	if _, err := f.Write(b); err != nil {
		return fmt.Errorf("failed to write history record to file: %w", err)
	}
	if _, err := f.Write([]byte("\n")); err != nil {
		return fmt.Errorf("failed to write newline to history file: %w", err)
	}
	return nil
}

// GenerateSessionHash creates a short unique hash for a session based on query and timestamp.
func GenerateSessionHash(query string, timestamp time.Time) string {
	h := sha256.Sum256([]byte(fmt.Sprintf("%s_%d", query, timestamp.Unix())))
	return fmt.Sprintf("%x", h)[:12]
}
