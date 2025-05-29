package ui

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"github.com/fatih/color"
)

// LogLevel defines the verbosity level for logging.
type LogLevel int

const (
	LogLevelDebug LogLevel = iota // 0 - Most verbose (includes all lower levels)
	LogLevelInfo                  // 1 - Default operational messages (includes warn, none)
	LogLevelWarn                  // 2 - Warnings, non-fatal issues (includes none)
	LogLevelNone                  // 3 - Only critical errors/core interaction messages
)

// ParseLogLevel converts a string to a LogLevel. Defaults to LogLevelInfo on error.
func ParseLogLevel(s string) (LogLevel, error) {
	switch strings.ToLower(s) {
	case "debug":
		return LogLevelDebug, nil
	case "info":
		return LogLevelInfo, nil
	case "warn":
		return LogLevelWarn, nil
	case "none":
		return LogLevelNone, nil
	default:
		return LogLevelInfo, fmt.Errorf("unknown log level '%s', defaulting to 'info'", s)
	}
}

// String returns the string representation of the LogLevel.
func (l LogLevel) String() string {
	switch l {
	case LogLevelDebug:
		return "debug"
	case LogLevelInfo:
		return "info"
	case LogLevelWarn:
		return "warn"
	case LogLevelNone:
		return "none"
	default:
		return fmt.Sprintf("UNKNOWN_LOG_LEVEL(%d)", l)
	}
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

// AgentMessage represents the structure of messages from the Python agent.
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
	Location         string        `json:"location,omitempty"`
}

// AgentAction models a single step in a recipe or fallback.
type AgentAction struct {
	Description string `json:"description"`
	Action      string `json:"action"`
	Tool        string `json:"tool"`
}

// UI interface defines methods for user interaction.
type UI interface {
	PrintHelp()
	PromptForApproval(message string) bool
	PrintAgentMessage(msg AgentMessage, minGoLogLevel LogLevel)
	PrintColored(c func(a ...interface{}) string, format string, a ...interface{})
	PrintStderr(line string, minGoLogLevel LogLevel)
	// Expose color functions directly for external use
	Green(a ...interface{}) string
	Blue(a ...interface{}) string
	Yellow(a ...interface{}) string
	Red(a ...interface{}) string
	Cyan(a ...interface{}) string
	Magenta(a ...interface{}) string
}

// ConsoleUI implements the UI interface for console output.
type ConsoleUI struct{}

// NewConsoleUI creates a new ConsoleUI instance.
func NewConsoleUI() *ConsoleUI {
	return &ConsoleUI{}
}

// PrintHelp prints the application's help message.
func (c *ConsoleUI) PrintHelp() {
	fmt.Print(`OG: Command-line AI agent

Usage:
  og <prompt>             Run OG agent on a prompt (natural language or shell-like)
  og init                 Write default config to ~/.local/share/og/og_config.toml
  og --help, -h           Show this help message
  og --verbosity <level>  Set log verbosity (debug, info, warn, none)

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

// PromptForApproval shows a yes/no prompt and returns true if approved.
func (c *ConsoleUI) PromptForApproval(message string) bool {
	fmt.Printf("\n%s\n", yellow(message))
	fmt.Printf("%s [y/N]: ", blue("Approve?"))
	reader := bufio.NewReader(os.Stdin)
	input, _ := reader.ReadString('\n')
	return strings.ToLower(strings.TrimSpace(input)) == "y"
}

// PrintAgentMessage processes and prints each JSON message from Python.
func (c *ConsoleUI) PrintAgentMessage(msg AgentMessage, minGoLogLevel LogLevel) {
	// Core messages always print regardless of Go verbosity level
	switch msg.Type {
	case "error":
		fmt.Printf("%s %s\n", red("[ERROR]"), msg.Message)
	case "unsafe":
		fmt.Printf("%s %s\n", red("[UNSAFE]"), msg.Reason)
		exp := strings.TrimSpace(msg.Explanation)
		if exp != "" {
			fmt.Println(yellow("Explanation:"))
			fmt.Println(exp)
		}
	case "plan":
		fmt.Printf("\n%s\n%s %s\n", yellow("🧠 Plan:"), blue("Request:"), msg.Request)

		isMultiStepRecipe := len(msg.RecipeSteps) > 1 || msg.FallbackAction != nil

		if isMultiStepRecipe {
			fmt.Printf("\n%s\n", blue("Steps:"))
			for i, s := range msg.RecipeSteps {
				fmt.Printf("  %s %d. %s\n      %s: %s (%s)\n", cyan("Step"), i+1, s.Description, yellow("Act"), s.Action, s.Tool)
			}
			if msg.FallbackAction != nil {
				fmt.Printf("\n%s %s (%s)\n", yellow("Fallback:"), msg.FallbackAction.Action, msg.FallbackAction.Tool)
			}
		} else {
			fmt.Printf("\n%s\n", blue("Proposed Action:"))
			s := msg.RecipeSteps[0]
			fmt.Printf("  %s 1. %s\n      %s: %s (%s)\n", cyan("Action"), s.Description, yellow("Act"), s.Action, s.Tool)
			fmt.Println(yellow("Auto-proceeding to execution for individual step approval."))
		}

	case "request_approval":
		fmt.Printf("\n%s\n  %s %s\n  %s %s (%s)\n", yellow("🤖 Approval Needed"),
			cyan("Desc:"), msg.Description,
			yellow("Cmd:"), msg.Action, msg.Tool)
	case "final_summary":
		fmt.Printf("\n%s\n  %s %s\n  %s %s\n", green("🏁 Summary:"), cyan("Nutshell:"), msg.Nutshell, cyan("Details:"), msg.Summary)
	case "result":
		fmt.Printf("\n%s %s%s\n%s %s\n", green("Result:"), getStatusEmoji(msg.Status), msg.Status,
			blue("Info:"), msg.InterpretMessage)
		if trimmed := strings.TrimSpace(msg.Output); trimmed != "" {
			fmt.Printf("\n%s\n%s\n", green("Output:"), formatOutput(msg.Output))
		}
	case "deny_current_action":
		// This message just signals Go to terminate, Python already handles the user-facing output
		return
	default:
		// Categorized log messages, filtered by minGoLogLevel
		var msgLevel LogLevel
		var levelTag string
		var colorFunc func(a ...interface{}) string

		switch msg.Type {
		case "debug_log":
			msgLevel = LogLevelDebug
			levelTag = "DEBUG"
			colorFunc = c.Magenta
		case "info_log":
			msgLevel = LogLevelInfo
			levelTag = "INFO"
			colorFunc = c.Blue
		case "warn_log":
			msgLevel = LogLevelWarn
			levelTag = "WARN"
			colorFunc = c.Yellow
		default:
			// Fallback for unexpected message types or internal prints from Python
			msgLevel = LogLevelInfo // Default to info if type is not recognized
			levelTag = "UNKNOWN"
			colorFunc = c.Yellow
		}

		if msgLevel >= minGoLogLevel {
			location := ""
			if msg.Location != "" {
				location = fmt.Sprintf(" {%s}", msg.Location)
			}
			fmt.Printf("%s%s %s\n", colorFunc(fmt.Sprintf("[%s]", levelTag)), location, msg.Message)
		}
	}
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

// PrintColored prints a formatted message with a specific color.
func (c *ConsoleUI) PrintColored(colorFunc func(a ...interface{}) string, format string, a ...interface{}) {
	fmt.Print(colorFunc(fmt.Sprintf(format, a...)))
}

// PrintStderr prints messages from the Python agent's stderr stream.
func (c *ConsoleUI) PrintStderr(line string, minGoLogLevel LogLevel) {
	if minGoLogLevel <= LogLevelDebug { // Only print stderr at debug level
		fmt.Fprintln(os.Stderr, magenta("[PY STDERR]"), line)
	}
}

// Expose color functions
func (c *ConsoleUI) Green(a ...interface{}) string   { return green(a...) }
func (c *ConsoleUI) Blue(a ...interface{}) string    { return blue(a...) }
func (c *ConsoleUI) Yellow(a ...interface{}) string  { return yellow(a...) }
func (c *ConsoleUI) Red(a ...interface{}) string     { return red(a...) }
func (c *ConsoleUI) Cyan(a ...interface{}) string    { return cyan(a...) }
func (c *ConsoleUI) Magenta(a ...interface{}) string { return magenta(a...) }
