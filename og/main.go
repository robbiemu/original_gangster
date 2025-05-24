package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"

	"github.com/fatih/color" // For colored output
)

// Config struct to match the JSON configuration file
type OGConfig struct {
	OllamaModel        string `json:"ollama_model"`
	OllamaHost         string `json:"ollama_host"`
	PythonAgentPath    string `json:"python_agent_path"`
	SummaryMode        bool   `json:"summary_mode"`
	VerboseAgent       bool   `json:"verbose_agent"`
	DefaultHistoryPath string `json:"default_history_path"` // Not used directly but part of config
}

// Message types for communication between Go and Python
type PythonMessage struct {
	Type             string        `json:"type"`
	Message          string        `json:"message,omitempty"`           // For "log", "error"
	Request          string        `json:"request,omitempty"`           // For "plan"
	RecipeSteps      []AgentAction `json:"recipe_steps,omitempty"`      // For "plan"
	FallbackAction   *AgentAction  `json:"fallback_action,omitempty"`   // For "plan"
	Description      string        `json:"description,omitempty"`       // For "request_approval"
	ActionStr        string        `json:"action_str,omitempty"`        // For "request_approval"
	Tool             string        `json:"tool,omitempty"`              // For "request_approval"
	Output           string        `json:"output,omitempty"`            // For "result"
	Status           string        `json:"status,omitempty"`            // For "result"
	InterpretMessage string        `json:"interpret_message,omitempty"` // For "result"
	Summary          string        `json:"summary,omitempty"`           // For "final_summary"
	Nutshell         string        `json:"nutshell,omitempty"`          // For "final_summary"
}

type AgentAction struct {
	Description string `json:"description"`
	ActionStr   string `json:"action_str"`
	Tool        string `json:"tool"`
}

type GoResponse struct {
	Type     string `json:"type"`
	Approved bool   `json:"approved"`
}

var (
	cfg     OGConfig
	green   = color.New(color.FgGreen).SprintFunc()
	blue    = color.New(color.FgBlue).SprintFunc()
	yellow  = color.New(color.FgYellow).SprintFunc()
	red     = color.New(color.FgRed).SprintFunc()
	cyan    = color.New(color.FgCyan).SprintFunc()
	magenta = color.New(color.FgMagenta).SprintFunc()
)

const configFileName = "og_config.json"

func getConfigPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("could not get user home directory: %w", err)
	}
	configDir := filepath.Join(home, ".local", "share")
	return filepath.Join(configDir, configFileName), nil
}

func loadConfig() error {
	configPath, err := getConfigPath()
	if err != nil {
		return err
	}

	configFile, err := os.ReadFile(configPath)
	if err != nil {
		return fmt.Errorf("could not read config file '%s': %w", configPath, err)
	}

	if err := json.Unmarshal(configFile, &cfg); err != nil {
		return fmt.Errorf("could not parse config file '%s': %w", configPath, err)
	}

	// Expand tilde in PythonAgentPath if present
	if strings.HasPrefix(cfg.PythonAgentPath, "~/") {
		home, err := os.UserHomeDir()
		if err != nil {
			return fmt.Errorf("failed to expand home dir in python_agent_path: %w", err)
		}
		cfg.PythonAgentPath = filepath.Join(home, cfg.PythonAgentPath[2:])
	}
	return nil
}

func saveDefaultConfig(configPath string) error {
	defaultCfg := OGConfig{
		OllamaModel:        "llama3",
		OllamaHost:         "http://localhost:11434",
		PythonAgentPath:    "~/.local/share/og_agent.py", // Placeholder, user must update
		SummaryMode:        true,
		VerboseAgent:       false,
		DefaultHistoryPath: "~/.local/share/og_history.json",
	}

	configDir := filepath.Dir(configPath)
	if err := os.MkdirAll(configDir, 0755); err != nil {
		return fmt.Errorf("failed to create config directory '%s': %w", configDir, err)
	}

	data, err := json.MarshalIndent(defaultCfg, "", "  ")
	if err != nil {
		return fmt.Errorf("failed to marshal default config: %w", err)
	}

	if err := os.WriteFile(configPath, data, 0644); err != nil {
		return fmt.Errorf("failed to write default config to '%s': %w", configPath, err)
	}
	fmt.Printf(yellow("Created default config at %s. Please update 'python_agent_path' to point to your og_agent.py script.\n"), configPath)
	return nil
}

func promptForApproval(message string) bool {
	fmt.Printf("\n%s\n", yellow(message))
	fmt.Printf("%s [y/N]: ", blue("Approve?"))
	reader := bufio.NewReader(os.Stdin)
	input, _ := reader.ReadString('\n')
	return strings.ToLower(strings.TrimSpace(input)) == "y"
}

func main() {
	if len(os.Args) < 2 {
		fmt.Printf("Usage: %s <query>\n", os.Args[0])
		fmt.Println("       or use 'og init' to create a default config.")
		os.Exit(1)
	}

	configPath, err := getConfigPath()
	if err != nil {
		fmt.Printf(red("Error getting config path: %s\n"), err)
		os.Exit(1)
	}

	if os.Args[1] == "init" {
		if err := saveDefaultConfig(configPath); err != nil {
			fmt.Printf(red("Error saving default config: %s\n"), err)
			os.Exit(1)
		}
		os.Exit(0)
	}

	if err := loadConfig(); err != nil {
		fmt.Printf(red("Error loading config: %s\n"), err)
		fmt.Printf(yellow("Run 'og init' to create a default config file and update '%s'.\n"), configPath)
		os.Exit(1)
	}

	query := strings.Join(os.Args[1:], " ")

	// Prepare command arguments for Python agent
	pythonArgs := []string{
		cfg.PythonAgentPath,
		"--query", query,
		"--model", cfg.OllamaModel,
		"--host", cfg.OllamaHost,
	}
	if cfg.VerboseAgent {
		pythonArgs = append(pythonArgs, "--verbose")
	}
	if cfg.SummaryMode {
		pythonArgs = append(pythonArgs, "--summary-mode")
	}

	cmd := exec.Command("python3", pythonArgs...) // Use python3, adjust if your python is just 'python'

	stdinPipe, err := cmd.StdinPipe()
	if err != nil {
		fmt.Printf(red("Error creating stdin pipe: %s\n"), err)
		os.Exit(1)
	}
	defer stdinPipe.Close()

	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		fmt.Printf(red("Error creating stdout pipe: %s\n"), err)
		os.Exit(1)
	}
	defer stdoutPipe.Close()

	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		fmt.Printf(red("Error creating stderr pipe: %s\n"), err)
		os.Exit(1)
	}

	if err := cmd.Start(); err != nil {
		fmt.Printf(red("Error starting Python agent: %s\n"), err)
		fmt.Printf(yellow("Ensure Python is installed and '%s' is a valid path.\n"), cfg.PythonAgentPath)
		os.Exit(1)
	}

	var wg sync.WaitGroup
	wg.Add(2)

	// Goroutine to read stdout from Python
	go func() {
		defer wg.Done()
		scanner := bufio.NewScanner(stdoutPipe)
		for scanner.Scan() {
			line := scanner.Text()
			var msg PythonMessage
			err := json.Unmarshal([]byte(line), &msg)
			if err != nil {
				// If not JSON, it might be raw output from smolagents rich console, print directly
				fmt.Println(line)
				continue
			}

			switch msg.Type {
			case "log":
				if cfg.VerboseAgent {
					fmt.Printf("%s %s\n", magenta("[AGENT LOG]"), msg.Message)
				}
			case "error":
				fmt.Printf("%s %s\n", red("[AGENT ERROR]"), msg.Message)
				// Consider exiting or propagating error further
			case "plan":
				fmt.Printf("\n%s\n", yellow("OG has devised a plan (recipe) for your request:"))
				fmt.Printf("%s %s\n", blue("Request:"), msg.Request)
				fmt.Printf("%s\n", blue("Proposed Recipe Steps:"))
				for i, step := range msg.RecipeSteps {
					fmt.Printf("  %s %d. %s\n", cyan("Step"), i+1, step.Description)
					fmt.Printf("      %s: %s (%s)\n", yellow("Action"), step.ActionStr, step.Tool)
				}
				if msg.FallbackAction != nil {
					fmt.Printf("\n%s\n", yellow("If you deny the recipe, OG will attempt this fallback action:"))
					fmt.Printf("  %s: %s (%s)\n", yellow("Action"), msg.FallbackAction.ActionStr, msg.FallbackAction.Tool)
				}

				approved := promptForApproval("Do you want to proceed with this recipe?")
				resp := GoResponse{Type: "plan_response", Approved: approved}
				respBytes, _ := json.Marshal(resp)
				fmt.Fprintf(stdinPipe, "%s\n", string(respBytes))
				if !approved {
					fmt.Print(yellow("Recipe denied. OG will attempt the fallback action.\n"))
				}

			case "request_approval":
				fmt.Printf("\n%s\n", yellow("OG needs your approval for the next action."))
				fmt.Printf("  %s %s\n", cyan("Description:"), msg.Description)
				fmt.Printf("  %s %s (%s)\n", yellow("Action:"), msg.ActionStr, msg.Tool)

				approved := promptForApproval("Approve this action?")
				resp := GoResponse{Type: "approval_response", Approved: approved}
				respBytes, _ := json.Marshal(resp)
				fmt.Fprintf(stdinPipe, "%s\n", string(respBytes))
				if !approved {
					fmt.Print(red("Action denied. OG will stop.\n"))
				}

			case "result":
				fmt.Printf("\n%s\n", green("--- Action Result ---"))
				fmt.Printf("%s %s\n", blue("Status:"), msg.Status)
				fmt.Printf("%s %s\n", blue("Interpretation:"), msg.InterpretMessage)
				if msg.Output != "" {
					fmt.Printf("%s\n", green("Raw Output:"))
					fmt.Println(msg.Output)
				}
				fmt.Printf("%s\n", green("---------------------\n"))
			case "final_summary":
				if cfg.SummaryMode {
					fmt.Printf("\n%s\n", green("--- Task Summary ---"))
					fmt.Printf("%s\n", green("In a Nutshell:"))
					fmt.Printf("  %s\n", cyan(msg.Nutshell))
					fmt.Printf("%s\n", green("Detailed Summary:"))
					fmt.Printf("%s\n", msg.Summary)
					fmt.Printf("%s\n", green("---------------------\n"))
				}
			default:
				fmt.Printf(yellow("Go: Unrecognized message type from Python: %s\n"), msg.Type)
				fmt.Println(line) // Print the raw line if it's not a recognized JSON type
			}
		}
		if err := scanner.Err(); err != nil && err != io.EOF {
			fmt.Printf(red("Error reading from Python stdout: %s\n"), err)
		}
	}()

	// Goroutine to read stderr from Python (for Python's own errors)
	go func() {
		defer wg.Done()
		scanner := bufio.NewScanner(stderrPipe)
		for scanner.Scan() {
			fmt.Fprintf(os.Stderr, "%s %s\n", red("[PYTHON STDERR]"), scanner.Text())
		}
	}()

	// Wait for the Python command to finish
	err = cmd.Wait()
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			fmt.Printf(red("Python agent exited with error: %s (Code: %d)\n"), exitErr, exitErr.ExitCode())
		} else {
			fmt.Printf(red("Python agent process error: %s\n"), err)
		}
		os.Exit(1)
	}

	wg.Wait() // Wait for stdout/stderr goroutines to finish
	fmt.Printf("%s\n", blue("OG session ended."))
}
