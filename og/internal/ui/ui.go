package ui

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"github.com/fatih/color"
)

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
// Copied from main.go to avoid circular dependency, consider a shared types package if this grows.
// For now, let's keep it simple and assume necessary fields are here.
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

// UI interface defines methods for user interaction.
type UI interface {
	PrintHelp()
	PromptForApproval(message string) bool
	PrintAgentMessage(msg AgentMessage, verbose bool) // Combines display logic
	PrintColored(c func(a ...interface{}) string, format string, a ...interface{})
	PrintStderr(line string)
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
func (c *ConsoleUI) PrintAgentMessage(msg AgentMessage, verbose bool) {
	switch msg.Type {
	case "log":
		if verbose {
			fmt.Printf("%s %s\n", magenta("[AGENT]"), msg.Message)
		}
	case "error":
		fmt.Printf("%s %s", red("[ERROR]"), msg.Message)
	case "unsafe":
		fmt.Printf("%s %s", red("[UNSAFE]"), msg.Reason)
		exp := strings.TrimSpace(msg.Explanation)
		if exp != "" {
			fmt.Println(yellow("Explanation:"))
			fmt.Println(exp)
		}
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
		} else {
			// This is a single-step plan. No initial approval prompt.
			// Just log what the agent plans to do and send the command to Python.
			fmt.Printf("\n%s\n", blue("Proposed Action:"))
			s := msg.RecipeSteps[0]
			fmt.Printf("  %s 1. %s\n      %s: %s (%s)\n", cyan("Action"), s.Description, yellow("Act"), s.Action, s.Tool)
			fmt.Println(yellow("Auto-proceeding to execution for individual step approval."))
		}

	case "request_approval":
		// This case is now ONLY for individual step approvals (triggered by ProxyTool)
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
	default:
		if msg.Message != "" {
			fmt.Printf(yellow("Unknown message type: %s\n"), msg.Type)
			fmt.Println(msg.Message)
		} else {
			fmt.Printf(yellow("Unknown message type: %s (no message content)\n"), msg.Type)
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
func (c *ConsoleUI) PrintStderr(line string) {
	fmt.Fprintln(os.Stderr, magenta("[PY STDERR]"), line)
}

// Expose color functions
func (c *ConsoleUI) Green(a ...interface{}) string   { return green(a...) }
func (c *ConsoleUI) Blue(a ...interface{}) string    { return blue(a...) }
func (c *ConsoleUI) Yellow(a ...interface{}) string  { return yellow(a...) }
func (c *ConsoleUI) Red(a ...interface{}) string     { return red(a...) }
func (c *ConsoleUI) Cyan(a ...interface{}) string    { return cyan(a...) }
func (c *ConsoleUI) Magenta(a ...interface{}) string { return magenta(a...) }
