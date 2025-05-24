# --## og/main.go ##

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


# --## agent/agent.py ##

import sys
import json
import argparse
from rich.console import Console
from rich.syntax import Syntax
from rich.markdown import Markdown
from smolagents.tool_code_agent import CodeAgent
from smolagents.llm.litellm import LiteLLMModel # Using LiteLLMModel for Ollama


console = Console()

# --- Helper Functions for Communication ---

def send_message(message_type: str, data: dict):
    """Sends a JSON message to stdout for the Go wrapper to consume."""
    message = {"type": message_type, **data}
    json_message = json.dumps(message)
    console.print(json_message, highlight=False, style="black on black") # Print raw JSON to stdout
    sys.stdout.flush() # Ensure it's sent immediately


def read_response():
    """Reads a JSON response from stdin (from the Go wrapper)."""
    try:
        line = sys.stdin.readline()
        if not line:
            return None # EOF
        return json.loads(line.strip())
    except json.JSONDecodeError:
        send_message("error", {"message": f"Agent: Could not decode JSON from Go: {line}"})
        return None
    except Exception as e:
        send_message("error", {"message": f"Agent: Error reading from stdin: {e}"})
        return None


def display_content(content: str, is_code: bool = False, lang: str = "text"):
    """Displays content using rich, trying to infer markdown or code."""
    if is_code:
        console.print(Syntax(content, lang, theme="monokai", line_numbers=True, word_wrap=True))
    elif any(line.strip().startswith(('#', '*', '-', '>', '[', '`')) for line in content.splitlines()):
        console.print(Markdown(content, code_theme="monokai"))
    else:
        console.print(content)


# --- Agent System Prompt ---
# This is crucial for guiding the agent's behavior and output format.
SYSTEM_PROMPT = """
You are "Original Gangster" (OG), a command-line assistant. Your primary goal is to help the user with their requests by executing commands or reading files on their system. You have access to `shell_tool` for executing commands and `file_tool` for reading file content.

**IMPORTANT RULES:**

1.  **Safety First:** Before suggesting *any* action, carefully evaluate the request for potential danger. If the request is inherently dangerous, destructive, or ambiguous in a way that could lead to harm (e.g., `rm -rf /`, formatting a disk, modifying critical system files without specific instructions), you MUST state that you cannot fulfill it due to safety concerns.
2.  **JSON Communication:** Your responses to the Go wrapper MUST be in specific JSON formats. Do NOT output anything else to stdout unless explicitly instructed (e.g., for raw tool output after approval).
    *   For thoughts or verbose logging (if `--verbose` is on): `{"type": "log", "message": "Your thought process or log message"}`
    *   For errors: `{"type": "error", "message": "Error description"}`
    *   To propose a recipe: `{"type": "plan", "request": "User's original request", "recipe_steps": [{"description": "What this step does", "action_str": "Command or file path", "tool": "shell_tool" or "file_tool"}], "fallback_action": {"description": "Fallback if recipe denied", "action_str": "Command or file path", "tool": "shell_tool" or "file_tool"}}`
    *   To request approval for an action: `{"type": "request_approval", "description": "What this action will do", "action_str": "The command or file path to be executed", "tool": "shell_tool" or "file_tool"}`
    *   To provide interpreted results after an action: `{"type": "result", "output": "Raw tool output", "status": "success" or "failure", "interpret_message": "Your interpretation of the output"}`
    *   To provide a final summary: `{"type": "final_summary", "summary": "Detailed task summary", "nutshell": "Brief, isolated summary (the 'in a nutshell' part)"}`
3.  **Recipe vs. Simple Action:**
    *   If a request is complex and requires multiple steps or conditional logic, devise a "recipe" (a sequence of actions).
    *   For recipes, also provide a single "fallback action" that is most likely to succeed if the user rejects the recipe.
    *   If simple, just propose a single action.
4.  **Human Approval:** ALWAYS wait for human approval before executing any `shell_tool` or `file_tool` action. You will receive an `{"type": "approval_response", "approved": true/false}` JSON from the Go wrapper via stdin.
5.  **Iterative Recipes:** When executing a recipe, after each step's output is received and interpreted, re-evaluate the overall plan. You can decide to continue, modify the remaining steps, or abandon the recipe if something went wrong or the goal is achieved. If you modify or abandon, communicate this clearly.
6.  **Context:** Maintain context of the original query, previous actions, and their results.

**Workflow Outline for OG's Thinking Process:**

1.  **Analyze Request:** What does the user want? Is it dangerous?
    *   IF dangerous: Respond `{"type": "error", "message": "Cannot fulfill due to safety."}` and exit.
    *   ELSE IF complex and requires multiple steps:
        *   Generate a `recipe_steps` array (each step with `description`, `action_str`, `tool`).
        *   Generate a `fallback_action` (single step with `description`, `action_str`, `tool`).
        *   Send `{"type": "plan", ...}`.
        *   Wait for `{"type": "plan_response", "approved": true/false}` from Go.
        *   IF approved: Execute recipe. ELSE: Execute fallback.
    *   ELSE (simple, single action):
        *   Generate `action_str` and `tool`.
        *   Send `{"type": "request_approval", ...}`.
        *   Wait for `{"type": "approval_response", "approved": true/false}` from Go.
        *   IF approved: Execute action. ELSE: Report cancellation.
2.  **Execute Action(s) (after approval):**
    *   Call `smolagents.tools.call_tool()` for `shell_tool` or `file_tool`.
    *   Capture output.
    *   Interpret the output using your LLM capabilities.
    *   Send `{"type": "result", ...}`.
3.  **Recipe Re-evaluation (if applicable):** After each step in a recipe, review the output. Decide if the next step makes sense, if the plan needs modification, or if the task is complete/needs to be abandoned.
4.  **Completion Summary:** At the end, if `summary_mode` is enabled, send `{"type": "final_summary", ...}`.

Let's begin. What is the user's request?
"""


def main():
    parser = argparse.ArgumentParser(description="Original Gangster (OG) Agent powered by smolagents.")
    parser.add_argument("--query", type=str, required=True, help="The query for the agent.")
    parser.add_argument("--model", type=str, default="llama3", help="Ollama model to use.")
    parser.add_argument("--host", type=str, default="http://localhost:11434", help="Ollama host URL.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging from the agent.")
    parser.add_argument("--summary-mode", action="store_true", help="Enable final summary output.")

    args = parser.parse_args()

    # --- Initialize LiteLLMModel for Ollama ---
    try:
        llm = LiteLLMModel(model_name=args.model, api_base=args.host)
        if args.verbose:
            send_message("log", {"message": f"Agent: Connected to Ollama at {args.host} using model '{args.model}'"})
    except Exception as e:
        send_message("error", {"message": f"Agent: Error connecting to Ollama: {e}. Ensure Ollama is running (`ollama serve`) and the model is pulled (`ollama pull {args.model}`)."})
        sys.exit(1)

    # --- Initialize CodeAgent ---
    # CodeAgent comes with file_tool and shell_tool built-in.
    agent = CodeAgent(llm=llm, verbose=args.verbose)
    tools = agent.tools # Access the underlying tools manager

    # --- Agent Interaction Loop ---
    conversation_history = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"User's request: {args.query}"}
    ]
    
    current_plan = None
    execution_mode = "simple" # "simple" or "recipe" or "fallback"
    
    # Initial LLM call to decide on safety, recipe, or simple action
    try:
        response_json_str = llm.generate_response(conversation_history)
        if args.verbose:
            send_message("log", {"message": f"Agent: Initial LLM response: {response_json_str}"})
        
        try:
            initial_decision = json.loads(response_json_str)
        except json.JSONDecodeError:
            send_message("error", {"message": f"Agent: LLM returned unparsable JSON for initial decision: {response_json_str}"})
            sys.exit(1)

        if initial_decision.get("type") == "error":
            send_message("error", {"message": initial_decision.get("message", "Unknown error from LLM during initial decision.")})
            sys.exit(1)
            
        elif initial_decision.get("type") == "plan":
            send_message("plan", initial_decision)
            plan_response = read_response()
            if plan_response and plan_response.get("type") == "plan_response" and plan_response.get("approved"):
                current_plan = initial_decision["recipe_steps"]
                execution_mode = "recipe"
                if args.verbose:
                    send_message("log", {"message": "Agent: Recipe plan approved. Starting execution."})
            else:
                current_plan = [initial_decision["fallback_action"]]
                execution_mode = "fallback"
                if args.verbose:
                    send_message("log", {"message": "Agent: Recipe plan denied or invalid response. Executing fallback."})

        elif initial_decision.get("type") == "request_approval":
            # Simple action requested directly
            current_plan = [
                {"description": initial_decision["description"], 
                 "action_str": initial_decision["action_str"], 
                 "tool": initial_decision["tool"]}
            ]
            execution_mode = "simple"
            if args.verbose:
                    send_message("log", {"message": "Agent: Single action requested directly."})
        else:
            send_message("error", {"message": f"Agent: Unexpected initial decision type from LLM: {initial_decision.get('type')}. Raw: {response_json_str}"})
            sys.exit(1)

    except Exception as e:
        send_message("error", {"message": f"Agent: Error during initial agent planning: {e}"})
        sys.exit(1)

    # --- Execute Actions (Recipe or Fallback or Simple) ---
    if not current_plan:
        send_message("error", {"message": "Agent: No plan or action generated. Exiting."})
        sys.exit(1)

    executed_actions_info = [] # To keep track for final summary
    
    # This loop handles recipe steps, fallback, or a single action
    for i, action_info in enumerate(current_plan):
        if execution_mode == "recipe":
            send_message("log", {"message": f"Agent: Executing recipe step {i+1}/{len(current_plan)}: {action_info['description']}"})
        
        # Request approval for the current action
        send_message("request_approval", {
            "description": action_info["description"],
            "action_str": action_info["action_str"],
            "tool": action_info["tool"]
        })
        
        approval_response = read_response()
        
        if not approval_response or approval_response.get("type") != "approval_response" or not approval_response.get("approved"):
            send_message("result", {"output": "Action denied by user.", "status": "cancelled", "interpret_message": "User declined to execute this action."})
            send_message("log", {"message": "Agent: Action denied by user. Aborting plan."})
            break # Stop if user denies action

        # Execute the approved action
        tool_output = ""
        action_status = "success"
        interpretation = "Action executed successfully."
        
        try:
            if action_info["tool"] == "shell_tool":
                if args.verbose:
                    send_message("log", {"message": f"Agent: Calling shell_tool with: '{action_info['action_str']}'"})
                tool_output = tools.call_tool("shell_tool", {"command": action_info["action_str"]})
            elif action_info["tool"] == "file_tool":
                if args.verbose:
                    send_message("log", {"message": f"Agent: Calling file_tool with: '{action_info['action_str']}'"})
                tool_output = tools.call_tool("file_tool", {"path": action_info["action_str"]})
            else:
                raise ValueError(f"Unknown tool: {action_info['tool']}")

            # Send tool output back to LLM for interpretation
            conversation_history.append({"role": "assistant", "content": json.dumps({"type": "executed_action", "action": action_info, "output": tool_output, "status": "success"})})
            
            # Ask LLM to interpret the result and decide next step
            # This is where the agent self-corrects or decides to continue/abandon
            interpretation_prompt = f"The previous action was executed. Command/Path: '{action_info['action_str']}', Tool: '{action_info['tool']}', Output:\n```\n{tool_output}\n```\n\nBased on the original request and current progress, provide an interpretation of this output. If this is part of a recipe, decide what to do next: continue the recipe, modify the remaining steps, or conclude the task. If concluding, state 'TASK_COMPLETE'. If modifying, provide the new 'recipe_steps' array. If abandoning, state 'ABANDON_TASK'. Otherwise, just provide the interpretation and be ready for the next step."
            
            conversation_history.append({"role": "user", "content": interpretation_prompt})
            
            interpretation_response_str = llm.generate_response(conversation_history)
            
            try:
                interpretation_obj = json.loads(interpretation_response_str)
                interpretation = interpretation_obj.get("interpretation", "No specific interpretation provided by agent.")
                
                if interpretation_obj.get("status") == "TASK_COMPLETE":
                    if args.verbose:
                        send_message("log", {"message": "Agent: LLM marked task as complete."})
                    action_status = "complete"
                    break # Task complete
                elif interpretation_obj.get("status") == "ABANDON_TASK":
                    if args.verbose:
                        send_message("log", {"message": "Agent: LLM decided to abandon task."})
                    action_status = "abandoned"
                    break # Abandon task
                elif interpretation_obj.get("status") == "MODIFY_RECIPE" and execution_mode == "recipe":
                    if args.verbose:
                        send_message("log", {"message": "Agent: LLM decided to modify recipe."})
                    # This is tricky: we'd need to replace `current_plan` from the *current* point onward.
                    # For simplicity in this example, we'll just break and assume modification
                    # means the current plan is done, and a new query would start fresh.
                    # In a more advanced system, you'd insert/replace here.
                    send_message("log", {"message": "Agent: Recipe modification requested, but not fully implemented for dynamic insertion. Treating as completion for this run."})
                    action_status = "modified_and_completed"
                    break
                
            except json.JSONDecodeError:
                interpretation = f"Agent: LLM output was not valid JSON for interpretation. Raw: {interpretation_response_str}"
                send_message("log", {"message": interpretation})
                
            conversation_history.append({"role": "assistant", "content": json.dumps({"type": "interpreted_output", "interpretation": interpretation})})

        except Exception as e:
            action_status = "failure"
            interpretation = f"An error occurred during tool execution: {e}"
            tool_output = str(e)
            send_message("log", {"message": f"Agent: Tool execution failed: {e}"})

        executed_actions_info.append({
            "description": action_info["description"],
            "command_or_path": action_info["action_str"],
            "output": tool_output,
            "status": action_status,
            "interpretation": interpretation
        })
        
        send_message("result", {
            "output": tool_output,
            "status": action_status,
            "interpret_message": interpretation
        })
        
        if execution_mode == "simple" or execution_mode == "fallback":
            break # Only one action in simple/fallback mode

    # --- Final Summary ---
    if args.summary_mode:
        summary_prompt = "Based on the original request and the executed actions:\n"
        for act in executed_actions_info:
            summary_prompt += f"- Action: '{act['description']}' ({act['command_or_path']})\n  Status: {act['status']}\n  Interpretation: {act['interpretation']}\n"
        summary_prompt += "\nPlease provide a detailed 'summary' of what was done and the outcome. Also, provide a concise, isolated 'nutshell' statement that summarizes the key takeaway. Output in JSON format: {\"summary\": \"...\", \"nutshell\": \"...\"}"

        conversation_history.append({"role": "user", "content": summary_prompt})
        
        try:
            summary_response_str = llm.generate_response(conversation_history)
            summary_data = json.loads(summary_response_str)
            send_message("final_summary", {
                "summary": summary_data.get("summary", "No detailed summary provided."),
                "nutshell": summary_data.get("nutshell", "No nutshell summary provided.")
            })
        except Exception as e:
            send_message("error", {"message": f"Agent: Error generating final summary: {e}"})
            send_message("final_summary", {
                "summary": "Summary generation failed.",
                "nutshell": "Task completed (summary failed)."
            })

    sys.exit(0) 


if __name__ == "__main__":
    main()

