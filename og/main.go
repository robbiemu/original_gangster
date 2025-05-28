package main

import (
	"bufio"
	"crypto/sha256"
	"embed"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/fatih/color"
	"github.com/pelletier/go-toml/v2"
)

// Configuration structs
type ModelCfg struct {
	Model  string                 `toml:"model"`
	Params map[string]interface{} `toml:"model_params"`
}

type GeneralCfg struct {
	PythonAgentPath string `toml:"python_agent_path"`
	SummaryMode     bool   `toml:"summary_mode"`
	VerboseAgent    bool   `toml:"verbose_agent"`
	SessionTimeout  int    `toml:"session_timeout_minutes"`
}

type OGConfig struct {
	ManagedAgent ModelCfg   `toml:"managed_agent"`
	AuditorAgent ModelCfg   `toml:"auditor_agent"`
	General      GeneralCfg `toml:"general"`
}

// History record
type HistoryRecord struct {
	TS    string `json:"ts"`
	Hash  string `json:"hash"`
	CWD   string `json:"cwd"`
	Query string `json:"query"`
}

// Agent message types
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
	Reason           string        `json:"reason,omitempty"`
	Explanation      string        `json:"explanation,omitempty"`
	Approved         bool          `json:"approved,omitempty"`
}

// AgentAction models a single step in a recipe or fallback.
type AgentAction struct {
	Description string `json:"description"`
	Action      string `json:"action"`
	Tool        string `json:"tool"`
}

// Session manager
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

// ANSI helpers
var (
	green   = color.New(color.FgGreen).SprintFunc()
	blue    = color.New(color.FgBlue).SprintFunc()
	yellow  = color.New(color.FgYellow).SprintFunc()
	red     = color.New(color.FgRed).SprintFunc()
	cyan    = color.New(color.FgCyan).SprintFunc()
	magenta = color.New(color.FgMagenta).SprintFunc()
)

//go:embed prompts/prompts.toml
var embeddedPromptsFS embed.FS

const configFileName = "og_config.toml"

// Utility: data dir, config, history
func getDataDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "share", "og"), nil
}

func getConfigPath() (string, error) {
	dir, err := getDataDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, configFileName), nil
}

func getHistoryPath() (string, error) {
	dir, err := getDataDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "history.json"), nil
}

func getPromptsDir() (string, error) {
	dir, err := getDataDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "prompts"), nil
}

// Default prompts filename
const defaultPromptsFileName = "prompts.toml"

// Default config
func saveDefaultConfig(path string) error {
	// Ensure the parent directory exists
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	defaults := OGConfig{
		ManagedAgent: ModelCfg{
			Model: "ollama/llama3:latest",
			Params: map[string]interface{}{
				"base_url": "http://localhost:11435",
			},
		},
		AuditorAgent: ModelCfg{
			Model: "ollama/gemma3:27b-it",
			Params: map[string]interface{}{
				"base_url":    "http://localhost:11435",
				"temperature": 0.2,
			},
		},
		General: GeneralCfg{
			PythonAgentPath: "~/.local/share/og/agent.py",
			SummaryMode:     true,
			VerboseAgent:    false,
			SessionTimeout:  30,
		},
	}

	b, err := toml.Marshal(defaults)
	if err != nil {
		return err
	}
	if err := os.WriteFile(path, b, 0o644); err != nil {
		return err
	}
	fmt.Println(green("✨ A starter config has been written to:"), cyan(path))
	fmt.Print(yellow("Please update 'python_agent_path' to point to your agent script.\n"))

	fmt.Println(green("✨ A starter config has been written to:"), cyan(path))
	fmt.Print(yellow("Please update 'python_agent_path' to point to your agent script.\n"))

	promptsDir, err := getPromptsDir()
	if err != nil {
		return fmt.Errorf("failed to get prompts directory: %w", err)
	}
	if err := os.MkdirAll(promptsDir, 0o755); err != nil {
		return fmt.Errorf("failed to create prompts directory: %w", err)
	}

	sourcePromptsContent, err := embeddedPromptsFS.ReadFile("prompts/" + defaultPromptsFileName)
	if err != nil {
		return fmt.Errorf("failed to read embedded prompts file: %w", err)
	}

	destinationPromptsPath := filepath.Join(promptsDir, defaultPromptsFileName)

	if err := os.WriteFile(destinationPromptsPath, sourcePromptsContent, 0o644); err != nil {
		return fmt.Errorf("failed to write prompts file to %s: %w", destinationPromptsPath, err)
	}

	fmt.Println(green("✨ Default prompts have been copied to:"), cyan(destinationPromptsPath))

	return nil
}

// Config loader
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
	if err := toml.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}

	// Expand ~ in paths
	expandPath := func(p string) string {
		if strings.HasPrefix(p, "~/") {
			home, _ := os.UserHomeDir()
			return filepath.Join(home, p[2:])
		}
		return p
	}
	cfg.General.PythonAgentPath = expandPath(cfg.General.PythonAgentPath)

	return &cfg, nil
}

// History persistence
func appendHistory(rec HistoryRecord) {
	path, err := getHistoryPath()
	if err != nil {
		return
	}
	dir := filepath.Dir(path)
	_ = os.MkdirAll(dir, 0o755)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	b, _ := json.Marshal(rec)
	f.Write(b)
	f.Write([]byte("\n"))
}

// Helper to create short session hash
func generateSessionHash(query string, timestamp time.Time) string {
	h := sha256.Sum256([]byte(fmt.Sprintf("%s_%d", query, timestamp.Unix())))
	return fmt.Sprintf("%x", h)[:12]
}

// Python agent management
func (sm *SessionManager) startPythonAgent(query string) error {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	// Prepare model param JSON strings
	mParams, _ := json.Marshal(sm.config.ManagedAgent.Params)
	aParams, _ := json.Marshal(sm.config.AuditorAgent.Params)

	wd, _ := os.Getwd()

	pythonAgentFilePath := sm.config.General.PythonAgentPath

	moduleFileName := filepath.Base(pythonAgentFilePath)
	moduleName := strings.TrimSuffix(moduleFileName, ".py")

	packageDir := filepath.Dir(pythonAgentFilePath)
	packageName := filepath.Base(packageDir)

	pythonPackageRootPath := filepath.Dir(packageDir)

	fullModulePath := fmt.Sprintf("%s.%s", packageName, moduleName)

	cmdArgs := []string{
		"python3",
		"-m",
		fullModulePath,
		"--session-hash", sm.currentHash,
		"--query", query, // Initial query is passed here for planning
		"--workdir", wd,
		"--model", sm.config.ManagedAgent.Model,
		"--model-params", string(mParams),
		"--auditor-model", sm.config.AuditorAgent.Model,
		"--auditor-params", string(aParams),
	}

	if sm.config.General.VerboseAgent {
		cmdArgs = append(cmdArgs, "--verbose")
	}
	if sm.config.General.SummaryMode {
		cmdArgs = append(cmdArgs, "--summary-mode")
	}

	sm.pythonCmd = exec.Command(cmdArgs[0], cmdArgs[1:]...)

	// Set the PYTHONPATH environment variable for the command.
	env := os.Environ() // Get a copy of the current environment

	existingPythonPath := ""
	for _, e := range env {
		if strings.HasPrefix(e, "PYTHONPATH=") {
			existingPythonPath = strings.TrimPrefix(e, "PYTHONPATH=")
			break
		}
	}

	newPythonPathValue := pythonPackageRootPath
	if existingPythonPath != "" {
		newPythonPathValue = existingPythonPath + string(os.PathListSeparator) + pythonPackageRootPath
	}

	sm.pythonCmd.Env = append(env, "PYTHONPATH="+newPythonPathValue)

	stdin, err := sm.pythonCmd.StdinPipe()
	if err != nil {
		return err
	}
	sm.stdinPipe = stdin

	stdout, err := sm.pythonCmd.StdoutPipe()
	if err != nil {
		return err
	}
	sm.stdoutScanner = bufio.NewScanner(stdout)

	// Increase the buffer size for stdout scanner to handle potentially large JSON lines.
	const maxScanTokenSize = 1024 * 1024     // 1 MB
	buf := make([]byte, 0, maxScanTokenSize) // Create a buffer slice with desired capacity
	sm.stdoutScanner = bufio.NewScanner(stdout)
	sm.stdoutScanner.Buffer(buf, maxScanTokenSize) // Set the buffer and maximum token size

	stderr, err := sm.pythonCmd.StderrPipe()
	if err != nil {
		return err
	}
	sm.stderrScanner = bufio.NewScanner(stderr)
	go func() {
		for sm.stderrScanner.Scan() {
			line := sm.stderrScanner.Text()
			fmt.Fprintln(os.Stderr, magenta("[PY STDERR]"), line)
		}
	}()

	if err := sm.pythonCmd.Start(); err != nil {
		return err
	}
	return nil
}

// sendCommand marshals and sends a generic command to Python.
func (sm *SessionManager) sendCommand(cmdType string, data map[string]interface{}) error {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	payload := map[string]interface{}{"type": cmdType}
	for k, v := range data {
		payload[k] = v
	}

	b, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(sm.stdinPipe, "%s\n", string(b))
	return err
}

// Main processing loop
func (sm *SessionManager) runLoop() error {
	for sm.stdoutScanner.Scan() {
		line := strings.TrimSpace(sm.stdoutScanner.Text())
		if line == "" {
			continue
		}
		var msg AgentMessage
		if err := json.Unmarshal([]byte(line), &msg); err != nil {
			// Raw output or non-JSON log
			fmt.Println(line)
			continue
		}
		cont, err := handleAgentMessage(msg, sm)
		if err != nil {
			return err
		}
		if !cont {
			break // Agent signalled session end
		}
	}
	if err := sm.stdoutScanner.Err(); err != nil && err != io.EOF {
		return err
	}
	return nil
}

// Cleanup
func (sm *SessionManager) stop() {
	sm.mu.Lock()
	defer sm.mu.Unlock()
	if sm.stdinPipe != nil {
		sm.stdinPipe.Close() // Close stdin to signal EOF to Python
	}
	if sm.pythonCmd != nil && sm.pythonCmd.Process != nil {
		// Give Python a moment to exit gracefully after stdin closure
		done := make(chan struct{})
		go func() {
			sm.pythonCmd.Wait()
			close(done)
		}()
		select {
		case <-done:
			// Python exited cleanly
		case <-time.After(5 * time.Second):
			// Timeout, force kill
			fmt.Fprintln(os.Stderr, yellow("Python agent did not exit gracefully, forcing kill."))
			sm.pythonCmd.Process.Kill()
		}
	}
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
		if sm.config.General.VerboseAgent {
			fmt.Printf("%s %s\n", magenta("[AGENT]"), msg.Message)
		}
	case "error":
		fmt.Printf("%s %s", red("[ERROR]"), msg.Message)
		return false, nil // End session on error
	case "unsafe":
		fmt.Printf("%s %s", red("[UNSAFE]"), msg.Reason)
		exp := strings.TrimSpace(msg.Explanation)
		if exp != "" {
			fmt.Println(yellow("Explanation:"))
			fmt.Println(exp)
		}
		return false, nil // End session on unsafe
	case "plan":
		fmt.Printf("\n%s\n%s %s\n", yellow("🧠 Plan:"), blue("Request:"), msg.Request)

		// Determine if this is a multi-step recipe (has more than one command block or a fallback)
		// A single-step plan has exactly one recipe step and no fallback.
		isMultiStepRecipe := len(msg.RecipeSteps) > 1 || msg.FallbackAction != nil

		if isMultiStepRecipe {
			// This is a multi-step recipe or has a fallback. Request overall recipe approval.
			fmt.Printf("\n%s\n", blue("Steps:"))
			for i, s := range msg.RecipeSteps {
				fmt.Printf("  %s %d. %s\n      %s: %s (%s)\n", cyan("Step"), i+1, s.Description, yellow("Act"), s.Action, s.Tool)
			}
			if msg.FallbackAction != nil {
				fmt.Printf("\n%s %s (%s)\n", yellow("Fallback:"), msg.FallbackAction.Action, msg.FallbackAction.Tool)
			}

			if promptForApproval("Proceed with recipe?") {
				// Send command to Python to execute recipe (implies pre-approval)
				return true, sm.sendCommand("execute_recipe", nil)
			} else {
				// User denied the entire recipe
				fmt.Println(yellow("🚫 Recipe denied by user. Session ending."))
				// For now, no fallback execution on Go side. Python will exit.
				return false, nil // End session
			}
		} else {
			// This is a single-step plan. No initial approval prompt.
			// Just log what the agent plans to do and send the command to Python.
			fmt.Printf("\n%s\n", blue("Proposed Action:"))
			s := msg.RecipeSteps[0]
			fmt.Printf("  %s 1. %s\n      %s: %s (%s)\n", cyan("Action"), s.Description, yellow("Act"), s.Action, s.Tool)
			fmt.Println(yellow("Auto-proceeding to execution for individual step approval."))
			// Send new command for single actions
			return true, sm.sendCommand("execute_single_action", nil)
		}

	case "request_approval":
		// This case is now ONLY for individual step approvals (triggered by ProxyTool)
		fmt.Printf("\n%s\n  %s %s\n  %s %s (%s)\n", yellow("🤖 Approval Needed"),
			cyan("Desc:"), msg.Description,
			yellow("Cmd:"), msg.Action, msg.Tool)
		approved := promptForApproval("Execute step?") // Specific prompt for individual steps
		// Send user approval response back to Python
		return true, sm.sendCommand("user_approval_response", map[string]interface{}{"approved": approved})

	case "final_summary":
		// This is the new termination point after user denial or successful completion.
		if sm.config.General.SummaryMode {
			fmt.Printf("\n%s\n  %s %s\n  %s %s\n", green("🏁 Summary:"), cyan("Nutshell:"), msg.Nutshell, cyan("Details:"), msg.Summary)
		}
		return false, nil // Session ended

	case "result":
		fmt.Printf("\n%s %s%s\n%s %s\n", green("Result:"), getStatusEmoji(msg.Status), msg.Status,
			blue("Info:"), msg.InterpretMessage)
		if trimmed := strings.TrimSpace(msg.Output); trimmed != "" {
			fmt.Printf("\n%s\n%s\n", green("Output:"), formatOutput(msg.Output))
		}
	default:
		if msg.Message != "" {
			fmt.Printf(yellow("Unknown message type: %s\n"), msg.Type)
			fmt.Println(msg.Message)
		} else {
			fmt.Printf(yellow("Unknown message type: %s (no message content)\n"), msg.Type)
		}
	}
	return true, nil // Continue session
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

func printHelp() {
	fmt.Print(`OG: Command-line AI agent

Usage:
  og <prompt>             Run OG agent on a prompt (natural language or shell-like)
  og init                 Write default config to ~/.local/share/og/og_config.toml
  og --help, -h           Show this help message

Examples:
  og "summarize this repo"
  og "generate a gitignore for Rust"
  og "list files modified in last commit"

Config:
  Config file: ~/.local/share/og/og_config.toml

Tips:
- Set 'python_agent_path' in your config to your agent.py script
- 'init' will generate a starter config file

`)
}

// Main entry
func main() {
	helpFlag := flag.Bool("help", false, "show help message")
	hFlag := flag.Bool("h", false, "show help message (shorthand)")

	verboseFlag := flag.Bool("verbose", false, "run in verbose mode")

	flag.Usage = printHelp
	flag.Parse()

	// If help is requested, show help and exit
	if *helpFlag || *hFlag {
		printHelp()
		return
	}

	args := flag.Args() // Everything after flags

	// "og init"
	if len(args) >= 1 && args[0] == "init" {
		if path, err := getConfigPath(); err == nil {
			if err := saveDefaultConfig(path); err != nil {
				fmt.Println(red("Failed to write default config:"), err)
				os.Exit(1)
			}
			fmt.Println(green("OG config initialized."))
		} else {
			fmt.Println(red("Failed to determine config path:"), err)
			os.Exit(1)
		}
		return
	}

	cfg, err := loadConfig()
	if err != nil {
		fmt.Println(red("Failed to load config:"), err)
		fmt.Println(yellow("Run `og init` first to create a default configuration."))
		os.Exit(1)
	}
	if *verboseFlag {
		cfg.General.VerboseAgent = true
	}

	if len(args) < 1 {
		fmt.Println(yellow("Usage: og <prompt>"))
		os.Exit(1)
	}

	query := strings.Join(args, " ")
	cwd, _ := os.Getwd()
	sessionHash := generateSessionHash(query, time.Now())
	rec := HistoryRecord{
		TS:    time.Now().Format(time.RFC3339),
		Hash:  sessionHash,
		CWD:   cwd,
		Query: query,
	}
	appendHistory(rec)

	sm := &SessionManager{
		currentHash:  sessionHash,
		sessionStart: time.Now(),
		config:       cfg,
	}

	// Start Python agent once for the session
	if err := sm.startPythonAgent(query); err != nil {
		fmt.Println(red("Error starting python agent:"), err)
		os.Exit(1)
	}
	defer sm.stop() // Ensure Python agent is stopped when Go program exits

	// Run the main loop to process messages from Python
	if err := sm.runLoop(); err != nil {
		fmt.Println(red("Error during agent loop:"), err)
		os.Exit(1)
	}
	fmt.Println(blue("🚀 OG session ended."))
}
