package agent

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/robbiemu/original_gangster/og/internal/ui"
)

// MessageProcessor handles messages received from the Python agent.
type MessageProcessor struct {
	processManager *ProcessManager
	ui             ui.UI
	minGoLogLevel  ui.LogLevel
}

// NewMessageProcessor creates a new MessageProcessor.
func NewMessageProcessor(pm *ProcessManager, ui ui.UI, minGoLogLevel ui.LogLevel) *MessageProcessor {
	return &MessageProcessor{
		processManager: pm,
		ui:             ui,
		minGoLogLevel:  minGoLogLevel,
	}
}

// ProcessMessages reads messages from the Python agent's stdout and processes them.
// It returns true if the session should continue, false otherwise.
func (mp *MessageProcessor) ProcessMessages() error {
	scanner := mp.processManager.StdoutScanner()
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var msg ui.AgentMessage
		if err := json.Unmarshal([]byte(line), &msg); err != nil {
			// Raw output or non-JSON log from Python (e.g., Python's internal prints)
			// Only print if Go's verbosity is set to debug or lower
			if mp.minGoLogLevel <= ui.LogLevelDebug {
				fmt.Fprintln(os.Stderr, line)
			}
			continue
		}

		cont, err := mp.HandleMessage(msg)
		if err != nil {
			return err
		}
		if !cont {
			return nil // Agent signalled session end, no error.
		}
	}
	if err := scanner.Err(); err != nil && err != io.EOF {
		return fmt.Errorf("error reading from stdout scanner: %w", err)
	}
	return nil
}

// HandleMessage processes a single AgentMessage from Python.
// Returns true if the session should continue, false if it should terminate.
func (mp *MessageProcessor) HandleMessage(msg ui.AgentMessage) (bool, error) {
	mp.ui.PrintAgentMessage(msg, mp.minGoLogLevel) // Delegate display to UI

	switch msg.Type {
	case "error":
		return false, nil // End session on error
	case "unsafe":
		return false, nil // End session on unsafe
	case "plan":
		// Determine if this is a multi-step recipe for approval flow
		isMultiStepRecipe := len(msg.RecipeSteps) > 1 || msg.FallbackAction != nil
		if isMultiStepRecipe {
			if mp.ui.PromptForApproval("Proceed with recipe?") {
				return true, mp.processManager.SendCommand("execute_recipe", nil)
			} else {
				mp.ui.PrintColored(mp.ui.Yellow, "🚫 Recipe denied by user. Session ending.\n")
				return false, nil // User denied, end session
			}
		} else {
			// Single-step plan, auto-proceed to individual step approval (handled by ProxyTool)
			return true, mp.processManager.SendCommand("execute_single_action", nil)
		}
	case "request_approval":
		approved := mp.ui.PromptForApproval("Execute step?")
		return true, mp.processManager.SendCommand("user_approval_response", map[string]interface{}{"approved": approved})
	case "final_summary":
		return false, nil // Session ended cleanly
	case "deny_current_action": // Specific message from Python to indicate user denial handled by Python
		return false, nil // Python already knows, just terminate Go side loop
	default:
		// For other types like "log" or "result", just continue
		return true, nil
	}
}
