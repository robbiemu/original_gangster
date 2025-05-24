package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/fatih/color"
)

// OGConfig holds CLI-configurable options loaded from JSON.
type OGConfig struct {
	OllamaModel     string `json:"ollama_model"`
	OllamaHost      string `json:"ollama_host"`
	PythonAgentPath string `json:"python_agent_path"`
	SummaryMode     bool   `json:"summary_mode"`
	VerboseAgent    bool   `json:"verbose_agent"`
	SessionTimeout  int    `json:"session_timeout_minutes"`
}

// AgentMessage represents any JSON message from the Python agent.
type AgentMessage struct {
	Type             string        `json:"type"`
	Message          string        `json:"message,omitempty"`
	Request          string        `json:"request,omitempty"`
	RecipeSteps      []AgentAction `json:"recipe_steps,omitempty"`
	FallbackAction   *AgentAction  `json:"fallback_action,omitempty"`
	Description      string        `json:"description,omitempty"`
	Action           string        `json:"action,omitempty"`
	Tool             string        `json:"tool,omitempty"`
	Output           string        `json:"output,omitempty"`
	Status           string        `json:"status,omitempty"`
	InterpretMessage string        `json:"interpret_message,omitempty"`
	Summary          string        `json:"summary,omitempty"`
	Nutshell         string        `json:"nutshell,omitempty"`
}

// AgentAction models a single step in a recipe or fallback.
type AgentAction struct {
	Description string `json:"description"`
	Action      string `json:"action"`
	Tool        string `json:"tool"`
}

// ApprovalResponse is sent back to Python after user approval prompts.
type ApprovalResponse struct {
	Type     string `json:"type"`
	Approved bool   `json:"approved"`
}

// SessionManager orchestrates the Python subprocess and IPC.
type SessionManager struct {
	currentHash   string
	sessionStart  time.Time
	config        *OGConfig
	pythonCmd     *exec.Cmd
	stdinPipe     io.WriteCloser
	stdoutScanner *bufio.Scanner
	stderrScanner *bufio.Scanner
	mu            sync.Mutex
}

var (
	green   = color.New(color.FgGreen).SprintFunc()
	blue    = color.New(color.FgBlue).SprintFunc()
	yellow  = color.New(color.FgYellow).SprintFunc()
	red     = color.New(color.FgRed).SprintFunc()
	cyan    = color.New(color.FgCyan).SprintFunc()
	magenta = color.New(color.FgMagenta).SprintFunc()
)

const configFileName = "og_config.json"

// generateSessionHash produces a short hash to persist session state.
func generateSessionHash(query string, timestamp time.Time) string {
	h := sha256.Sum256([]byte(fmt.Sprintf("%s_%d", query, timestamp.Unix())))
	return fmt.Sprintf("%x", h)[:12]
}

// getConfigPath returns the path to ~/.local/share/og_config.json
func getConfigPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	dir := filepath.Join(home, ".local", "share")
	return filepath.Join(dir, configFileName), nil
}

// loadConfig reads and unmarshals the JSON config.
func loadConfig() (*OGConfig, error) {
	path, err := getConfigPath()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cfg OGConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	if strings.HasPrefix(cfg.PythonAgentPath, "~/") {
		home, _ := os.UserHomeDir()
		cfg.PythonAgentPath = filepath.Join(home, cfg.PythonAgentPath[2:])
	}
	return &cfg, nil
}

// saveDefaultConfig writes a starter config with placeholders.
func saveDefaultConfig(path string) error {
	defaults := OGConfig{
		OllamaModel:     "llama3",
		OllamaHost:      "http://localhost:11434",
		PythonAgentPath: "~/.local/share/og_agent.py",
		SummaryMode:     true,
		VerboseAgent:    false,
		SessionTimeout:  30,
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(defaults, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(path, b, 0644); err != nil {
		return err
	}
	fmt.Printf(yellow("Created default config at %s\n"), path)
	fmt.Print(yellow("Please update 'python_agent_path' to point to your agent script.\n"))
	return nil
}

// startPythonAgent launches the Python subprocess for planning or execution.
func (sm *SessionManager) startPythonAgent(query, mode string) error {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	args := []string{
		sm.config.PythonAgentPath,
		"--session-hash", sm.currentHash,
		"--query", query,
		"--model", sm.config.OllamaModel,
		"--api-base", sm.config.OllamaHost,
	}
	if sm.config.VerboseAgent {
		args = append(args, "--verbose")
	}
	if sm.config.SummaryMode {
		args = append(args, "--summary-mode")
	}
	switch mode {
	case "recipe":
		args = append(args, "--execute-recipe")
	case "fallback":
		args = append(args, "--execute-fallback")
	}

	sm.pythonCmd = exec.Command("python3", args...)
	var err error
	sm.stdinPipe, err = sm.pythonCmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := sm.pythonCmd.StdoutPipe()
	if err != nil {
		return err
	}
	stderr, err := sm.pythonCmd.StderrPipe()
	if err != nil {
		return err
	}
	sm.stdoutScanner = bufio.NewScanner(stdout)
	sm.stderrScanner = bufio.NewScanner(stderr)

	if err := sm.pythonCmd.Start(); err != nil {
		return err
	}
	return nil
}

// sendApproval marshals and sends an approval response.
func (sm *SessionManager) sendApproval(approved bool, responseType string) error {
	sm.mu.Lock()
	defer sm.mu.Unlock()
	resp := ApprovalResponse{Type: responseType, Approved: approved}
	b, err := json.Marshal(resp)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(sm.stdinPipe, "%s\n", string(b))
	return err
}

// stop terminates the Python subprocess and closes pipes.
func (sm *SessionManager) stop() {
	sm.mu.Lock()
	defer sm.mu.Unlock()
	if sm.stdinPipe != nil {
		sm.stdinPipe.Close()
	}
	if sm.pythonCmd != nil && sm.pythonCmd.Process != nil {
		sm.pythonCmd.Process.Kill()
		sm.pythonCmd.Wait()
	}
}

// runLoop reads NDJSON messages and dispatches them.
func (sm *SessionManager) runLoop() error {
	for sm.stdoutScanner.Scan() {
		line := sm.stdoutScanner.Text()
		var msg AgentMessage
		if err := json.Unmarshal([]byte(line), &msg); err != nil {
			// Raw output
			fmt.Println(line)
			continue
		}
		cont, err := handleAgentMessage(msg, sm)
		if err != nil {
			return err
		}
		if !cont {
			break
		}
	}
	if err := sm.stdoutScanner.Err(); err != nil && err != io.EOF {
		return err
	}
	return nil
}

// promptForApproval shows a yes/no prompt.
func promptForApproval(message string) bool {
	fmt.Printf("\n%s\n", yellow(message))
	fmt.Printf("%s [y/N]: ", blue("Approve?"))
	reader := bufio.NewReader(os.Stdin)
	input, _ := reader.ReadString('\n')
	return strings.ToLower(strings.TrimSpace(input)) == "y"
}

// handleAgentMessage processes each JSON message from Python.
func handleAgentMessage(msg AgentMessage, sm *SessionManager) (bool, error) {
	switch msg.Type {
	case "log":
		if sm.config.VerboseAgent {
			fmt.Printf("%s %s\n", magenta("[AGENT]"), msg.Message)
		}
	case "error":
		fmt.Printf("%s %s\n", red("[ERROR]"), msg.Message)
		return false, nil
	case "plan":
		fmt.Printf("\n%s\n%s %s\n\n%s\n", yellow("🧠 Plan:"), blue("Request:"), msg.Request, blue("Steps:"))
		for i, s := range msg.RecipeSteps {
			fmt.Printf("  %s %d. %s\n      %s: %s (%s)\n", cyan("Step"), i+1, s.Description, yellow("Act"), s.Action, s.Tool)
		}
		if msg.FallbackAction != nil {
			fmt.Printf("\n%s %s (%s)\n", yellow("Fallback:"), msg.FallbackAction.Action, msg.FallbackAction.Tool)
		}
		if promptForApproval("Proceed with recipe?") {
			sm.stop()
			return true, sm.startPythonAgent(msg.Request, "recipe")
		} else {
			sm.stop()
			return true, sm.startPythonAgent(msg.Request, "fallback")
		}
	case "request_approval":
		fmt.Printf("\n%s\n  %s %s\n  %s %s (%s)\n", yellow("🤖 Approval Needed"),
			cyan("Desc:"), msg.Description,
			yellow("Cmd:"), msg.Action, msg.Tool)
		sm.sendApproval(promptForApproval("Execute?"), "approval_response")
	case "result":
		fmt.Printf("\n%s %s%s\n%s %s\n", green("Result:"), getStatusEmoji(msg.Status), msg.Status,
			blue("Info:"), msg.InterpretMessage)
		if trimmed := strings.TrimSpace(msg.Output); trimmed != "" {
			fmt.Printf("\n%s\n%s\n", green("Output:"), formatOutput(msg.Output))
		}
	case "final_summary":
		if sm.config.SummaryMode {
			fmt.Printf("\n%s\n  %s %s\n  %s %s\n", green("🏁 Summary:"), cyan("Nutshell:"), msg.Nutshell, cyan("Details:"), msg.Summary)
		}
		return false, nil
	default:
		fmt.Printf(yellow("Unknown message type: %s\n"), msg.Type)
	}
	return true, nil
}

// getStatusEmoji returns a small icon for status.
func getStatusEmoji(status string) string {
	switch status {
	case "success":
		return "✅ "
	case "failure":
		return "❌ "
	case "cancelled":
		return "⚠️ "
	default:
		return ""
	}
}

// formatOutput indents multi-line tool output.
func formatOutput(output string) string {
	lines := strings.Split(output, "\n")
	for i := range lines {
		lines[i] = "    " + lines[i]
	}
	return strings.Join(lines, "\n")
}

func main() {
	// Handle "init" command
	if len(os.Args) >= 2 && os.Args[1] == "init" {
		if path, err := getConfigPath(); err == nil {
			if err := saveDefaultConfig(path); err != nil {
				fmt.Fprintln(os.Stderr, red("Init failed:"), err)
				os.Exit(1)
			}
			os.Exit(0)
		} else {
			fmt.Fprintln(os.Stderr, red("Cannot locate config path:"), err)
			os.Exit(1)
		}
	}
	// Load config
	cfg, err := loadConfig()
	if err != nil {
		fmt.Fprintln(os.Stderr, red("Config error:"), err)
		fmt.Fprintln(os.Stderr, yellow("Run `og init` first"))
		os.Exit(1)
	}
	// Build query and session
	query := strings.Join(os.Args[1:], " ")
	sessionHash := generateSessionHash(query, time.Now())
	sm := &SessionManager{
		currentHash:  sessionHash,
		sessionStart: time.Now(),
		config:       cfg,
	}
	// Start initial plan
	if err := sm.startPythonAgent(query, ""); err != nil {
		fmt.Fprintln(os.Stderr, red("Failed to start agent:"), err)
		os.Exit(1)
	}
	// Process until final_summary
	if err := sm.runLoop(); err != nil {
		fmt.Fprintln(os.Stderr, red("Error during runLoop:"), err)
		sm.stop()
		os.Exit(1)
	}
	sm.stop()
	fmt.Println(blue("🚀 OG session ended."))
}
