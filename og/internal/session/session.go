package session

import (
	"fmt"
	"os"
	"path/filepath"
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
}

// NewSession creates and initializes a new Session.
func NewSession(cfg *config.OGConfig, ui ui.UI) *Session {
	return &Session{
		cfg: cfg,
		ui:  ui,
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
		// Don't exit, history is not critical
	}

	// Initialize process and message managers
	s.processManager = agent.NewProcessManager(s.ui)
	s.messageProcessor = agent.NewMessageProcessor(s.processManager, s.ui, s.cfg.General.VerboseAgent)

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
	if err := s.processManager.Start(s.cfg, s.currentHash, query, cwd); err != nil {
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
