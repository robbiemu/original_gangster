package main

import (
	"embed"
	"flag"
	"os"
	"path/filepath"
	"strings"

	"github.com/robbiemu/original_gangster/og/internal/config"
	"github.com/robbiemu/original_gangster/og/internal/session"
	"github.com/robbiemu/original_gangster/og/internal/ui"
)

//go:embed prompts/prompts.toml
var embeddedPromptsFS embed.FS

func main() {
	// Create a UI instance early to handle all console output
	consoleUI := ui.NewConsoleUI()

	helpFlag := flag.Bool("help", false, "show help message")
	hFlag := flag.Bool("h", false, "show help message (shorthand)")
	verboseFlag := flag.Bool("verbose", false, "run in verbose mode")

	// Set the custom help function to use the UI component
	flag.Usage = consoleUI.PrintHelp
	flag.Parse()

	// If help is requested, show help and exit
	if *helpFlag || *hFlag {
		consoleUI.PrintHelp()
		return
	}

	args := flag.Args() // Everything after flags

	// Handle "og init" command
	if len(args) >= 1 && args[0] == "init" {
		if path, err := config.GetConfigPath(); err == nil {
			if err := config.SaveDefaultConfig(path, embeddedPromptsFS); err != nil {
				consoleUI.PrintColored(consoleUI.Red, "Failed to write default config: %v\n", err)
				os.Exit(1)
			}
			consoleUI.PrintColored(consoleUI.Green, "✨ A starter config has been written to: %s\n", consoleUI.Cyan(path))
			consoleUI.PrintColored(consoleUI.Yellow, "Please update 'python_agent_path' to point to your agent script.\n")

			// Successfully saved default prompts is also reported by SaveDefaultConfig, but let's confirm the path
			promptsDir, _ := config.GetPromptsDir() // Error handled inside SaveDefaultConfig
			consoleUI.PrintColored(consoleUI.Green, "✨ Default prompts have been copied to: %s\n", consoleUI.Cyan(filepath.Join(promptsDir, "prompts.toml")))
		} else {
			consoleUI.PrintColored(consoleUI.Red, "Failed to determine config path: %v\n", err)
			os.Exit(1)
		}
		return
	}

	// Load configuration
	cfg, err := config.LoadConfig()
	if err != nil {
		consoleUI.PrintColored(consoleUI.Red, "Failed to load config: %v\n", err)
		consoleUI.PrintColored(consoleUI.Yellow, "Run `og init` first to create a default configuration.\n")
		os.Exit(1)
	}

	// Override config verbose setting if CLI flag is present
	if *verboseFlag {
		cfg.General.VerboseAgent = true
	}

	// Check if a query was provided
	if len(args) < 1 {
		consoleUI.PrintColored(consoleUI.Yellow, "Usage: og <prompt>\n")
		os.Exit(1)
	}

	query := strings.Join(args, " ")

	// Create and run the session
	s := session.NewSession(cfg, consoleUI)
	if err := s.Run(query); err != nil {
		consoleUI.PrintColored(consoleUI.Red, "OG session failed: %v\n", err)
		os.Exit(1)
	}
}
