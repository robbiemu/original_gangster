package session

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/robbiemu/original_gangster/og/internal/agent"   // Import the agent package
	"github.com/robbiemu/original_gangster/og/internal/config"  // Import the config package
	"github.com/robbiemu/original_gangster/og/internal/history" // Import the history package
	"github.com/robbiemu/original_gangster/og/internal/ui"      // Import the ui package
)

// Session manages the overall interaction flow with the agent.
type Session struct {
	currentHash      string
	sessionStart     time.Time
	cfg              *config.OGConfig
	processManager   *agent.ProcessManager
	messageProcessor *agent.MessageProcessor
	ui               ui.UI
	minGoLogLevel    ui.LogLevel
	cacheCfg         config.CacheCfg
}

// NewSession creates and initializes a new Session.
func NewSession(cfg *config.OGConfig, ui ui.UI, cacheCfg config.CacheCfg) *Session {
	return &Session{
		cfg:           cfg,
		ui:            ui,
		minGoLogLevel: cfg.General.VerbosityLevel,
		cacheCfg:      cacheCfg,
	}
}

// Run executes the main session logic.
func (s *Session) Run(query string) error {
	s.sessionStart = time.Now()
	cwd, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current working directory: %w", err)
	}
	s.currentHash = history.GenerateSessionHash(query, s.sessionStart)

	rec := history.HistoryRecord{
		TS:    s.sessionStart.Format(time.RFC3339),
		Hash:  s.currentHash,
		CWD:   cwd,
		Query: query,
	}
	if err := history.AppendRecord(rec); err != nil {
		s.ui.PrintColored(s.ui.Red, "Failed to append history: %v\n", err)
	}

	// Initialize process and message managers
	s.processManager = agent.NewProcessManager(s.ui, s.minGoLogLevel)
	s.messageProcessor = agent.NewMessageProcessor(s.processManager, s.ui, s.minGoLogLevel)

	// Clean up old cache files before starting a new session
	if err := s.cleanupCacheFiles(); err != nil {
		s.ui.PrintColored(s.ui.Red, "Warning: Failed to clean up old cache files: %v\n", err)
	}

	// Set up temporary directory cleanup
	tempDirPath := filepath.Join(os.TempDir(), "og", s.currentHash)
	defer func() {
		if err := os.RemoveAll(tempDirPath); err != nil {
			s.ui.PrintColored(s.ui.Red, "Error cleaning up temporary directory %s: %v\n", tempDirPath, err)
		} else {
			s.ui.PrintColored(s.ui.Green, "Cleaned up temporary directory: %s\n", s.ui.Cyan(tempDirPath))
		}
	}()

	// Start Python agent
	if err := s.processManager.Start(s.cfg, s.currentHash, query, cwd, s.cacheCfg.JSONLogs, s.cacheCfg.Directory); err != nil {
		return fmt.Errorf("failed to start python agent: %w", err)
	}
	defer s.processManager.Stop() // Ensure Python agent is stopped

	// Run the main loop to process messages from Python
	if err := s.messageProcessor.ProcessMessages(); err != nil {
		return fmt.Errorf("error during agent message processing loop: %w", err)
	}

	s.ui.PrintColored(s.ui.Blue, "🚀 OG session ended.\n")
	return nil
}

// cleanupCacheFiles removes old session JSON files based on expiration.
func (s *Session) cleanupCacheFiles() error {
	if s.cacheCfg.Expiration <= 0 {
		s.ui.PrintColored(s.ui.Blue, "Cache expiration not set or invalid (<=0 days). Skipping old session file cleanup.\n")
		return nil // No expiration set
	}

	cacheDir := s.cacheCfg.Directory
	if cacheDir == "" {
		// This should ideally be handled by LoadConfig, but as a fallback
		dataDir, err := config.GetDataDir()
		if err != nil {
			return fmt.Errorf("could not determine default cache directory: %w", err)
		}
		cacheDir = dataDir
	}

	expirationThreshold := time.Now().Add(time.Duration(-s.cacheCfg.Expiration) * 24 * time.Hour)

	s.ui.PrintColored(s.ui.Blue, "Cleaning up cache files in %s older than %s...\n", s.ui.Cyan(cacheDir), expirationThreshold.Format("2006-01-02 15:04:05"))

	files, err := os.ReadDir(cacheDir)
	if err != nil {
		if os.IsNotExist(err) {
			s.ui.PrintColored(s.ui.Yellow, "Cache directory %s does not exist, no files to clean.\n", cacheDir)
			return nil
		}
		return fmt.Errorf("failed to read cache directory %s: %w", cacheDir, err)
	}

	for _, file := range files {
		if strings.HasSuffix(file.Name(), ".json") && !file.IsDir() {
			s.deleteFileIfExpired(filepath.Join(cacheDir, file.Name()), expirationThreshold)
		}
	}
	return nil
}

// deleteFileIfExpired checks a file's modification time and deletes it if it's older than the threshold.
func (s *Session) deleteFileIfExpired(filePath string, threshold time.Time) {
	fileInfo, err := os.Stat(filePath)
	if err != nil {
		s.ui.PrintColored(s.ui.Red, "Error stat-ing file %s: %v\n", filePath, err)
		return
	}

	if fileInfo.ModTime().Before(threshold) {
		if err := os.Remove(filePath); err != nil {
			s.ui.PrintColored(s.ui.Red, "Error deleting expired file %s: %v\n", filePath, err)
		} else {
			s.ui.PrintColored(s.ui.Green, "Deleted expired file: %s\n", s.ui.Cyan(filepath.Base(filePath)))
		}
	}
}
