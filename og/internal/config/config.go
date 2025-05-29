package config

import (
	"embed"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/pelletier/go-toml/v2"
	"github.com/robbiemu/original_gangster/og/internal/ui"
)

// Configuration structs
type ModelCfg struct {
	Model  string                 `toml:"model"`
	Params map[string]interface{} `toml:"model_params"`
}

type GeneralCfg struct {
	PythonAgentPath      string `toml:"python_agent_path"`
	SummaryMode          bool   `toml:"summary_mode"`
	VerbosityLevelStr    string `toml:"verbosity_level"`
	VerbosityLevel       ui.LogLevel
	SessionTimeout       int `toml:"session_timeout_minutes"`
	OutputThresholdBytes int `toml:"output_threshold_bytes"`
}

type CacheCfg struct {
	JSONLogs   bool   `toml:"json_logs"`
	Directory  string `toml:"directory"`  // Relative to data_dir, or empty for data_dir itself
	Expiration int    `toml:"expiration"` // Days, 0 means no expiration
}

type OGConfig struct {
	DefaultAgent  ModelCfg   `toml:"default_agent"`
	ExecutorAgent ModelCfg   `toml:"executor_agent"`
	PlannerAgent  ModelCfg   `toml:"planner_agent"`
	AuditorAgent  ModelCfg   `toml:"auditor_agent"`
	General       GeneralCfg `toml:"general"`
	Cache         CacheCfg   `toml:"cache"`
}

const configFileName = "og_config.toml"
const defaultPromptsFileName = "prompts.toml"

// GetDataDir returns the base data directory for OG.
func GetDataDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "share", "og"), nil
}

// GetConfigPath returns the full path to the main configuration file.
func GetConfigPath() (string, error) {
	dir, err := GetDataDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, configFileName), nil
}

// GetPromptsDir returns the full path to the prompts directory.
func GetPromptsDir() (string, error) {
	dir, err := GetDataDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "prompts"), nil
}

// SaveDefaultConfig writes a default OGConfig to the specified path and copies default prompts.
func SaveDefaultConfig(path string, embeddedPromptsFS embed.FS) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("failed to create config directory %s: %w", dir, err)
	}

	defaults := OGConfig{
		DefaultAgent: ModelCfg{
			Model: "ollama/gemma3:12b-it-qat",
			Params: map[string]interface{}{
				"base_url": "http://localhost:11434",
			},
		},
		ExecutorAgent: ModelCfg{
			// These will inherit from DefaultAgent unless specified
			// Model: "ollama/llama3:latest",
			// Params: map[string]interface{}{},
		},
		PlannerAgent: ModelCfg{
			// These will inherit from DefaultAgent unless specified
			// Model: "ollama/llama3:latest",
			// Params: map[string]interface{}{},
		},
		AuditorAgent: ModelCfg{ // We have a specific, lower temperature, default for this one
			Params: map[string]interface{}{
				"temperature": 0.2,
			},
		},
		General: GeneralCfg{
			PythonAgentPath:      "~/.local/share/og/agent.py",
			SummaryMode:          true,
			VerbosityLevelStr:    ui.LogLevelInfo.String(),
			SessionTimeout:       30,
			OutputThresholdBytes: 4096,
		},

		Cache: CacheCfg{
			JSONLogs:   true,
			Directory:  "", // Default to base data dir (~/.local/share/og/)
			Expiration: 0,  // No expiration by default
		},
	}

	b, err := toml.Marshal(defaults)
	if err != nil {
		return fmt.Errorf("failed to marshal default config: %w", err)
	}
	if err := os.WriteFile(path, b, 0o644); err != nil {
		return fmt.Errorf("failed to write default config to %s: %w", path, err)
	}

	promptsDir, err := GetPromptsDir()
	if err != nil {
		return fmt.Errorf("failed to get prompts directory: %w", err)
	}
	if err := os.MkdirAll(promptsDir, 0o755); err != nil {
		return fmt.Errorf("failed to create prompts directory %s: %w", promptsDir, err)
	}

	sourcePromptsContent, err := embeddedPromptsFS.ReadFile("prompts/" + defaultPromptsFileName)
	if err != nil {
		return fmt.Errorf("failed to read embedded prompts file: %w", err)
	}

	destinationPromptsPath := filepath.Join(promptsDir, defaultPromptsFileName)

	if err := os.WriteFile(destinationPromptsPath, sourcePromptsContent, 0o644); err != nil {
		return fmt.Errorf("failed to write prompts file to %s: %w", destinationPromptsPath, err)
	}

	return nil
}

// LoadConfig loads the OGConfig from the default path.
func LoadConfig() (*OGConfig, error) {
	path, err := GetConfigPath()
	if err != nil {
		return nil, fmt.Errorf("failed to get config path: %w", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read config file %s: %w", path, err)
	}
	var cfg OGConfig
	if err := toml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("failed to unmarshal config: %w", err)
	}

	// Apply defaults where specific agent configs are missing
	applyDefaultModelConfig(&cfg.ExecutorAgent, cfg.DefaultAgent)
	applyDefaultModelConfig(&cfg.PlannerAgent, cfg.DefaultAgent)
	applyDefaultModelConfig(&cfg.AuditorAgent, cfg.DefaultAgent)

	expandPath := func(p string) string {
		if strings.HasPrefix(p, "~/") {
			home, _ := os.UserHomeDir()
			return filepath.Join(home, p[2:])
		}
		return p
	}
	cfg.General.PythonAgentPath = expandPath(cfg.General.PythonAgentPath)

	// Set a default for OutputThresholdBytes if not present in config (for older configs)
	if cfg.General.OutputThresholdBytes == 0 {
		cfg.General.OutputThresholdBytes = 131072 // 128KB
	}

	// Parse VerbosityLevel from string after unmarshaling
	parsedLevel, err := ui.ParseLogLevel(cfg.General.VerbosityLevelStr)
	if err != nil {
		// If parsing fails (e.g., invalid string in TOML or empty string if not present),
		// log a warning and default to LogLevelInfo.
		// This covers cases where 'verbosity_level' is missing or malformed in the TOML.
		fmt.Fprintf(os.Stderr, "Warning: %v. Defaulting verbosity to 'info'.\n", err)
		cfg.General.VerbosityLevel = ui.LogLevelInfo
	} else {
		cfg.General.VerbosityLevel = parsedLevel
	}

	// Apply defaults and resolve path for CacheCfg
	// If Cache.Directory is empty in TOML, it defaults to "" by unmarshaling.
	// In this case, we want it to resolve to the base data directory.
	// Otherwise, it's a subdirectory relative to the base data directory.
	baseDataDir, err := GetDataDir()
	if err != nil {
		return nil, fmt.Errorf("failed to get base data directory for cache path resolution: %w", err)
	}

	if cfg.Cache.Directory != "" {
		cfg.Cache.Directory = expandPath(cfg.Cache.Directory) // Expand potential ~/
		cfg.Cache.Directory = filepath.Join(baseDataDir, cfg.Cache.Directory)
	} else {
		cfg.Cache.Directory = baseDataDir // If unset, default to base data dir
	}

	return &cfg, nil
}

// applyDefaultModelConfig applies default model and params if target is missing them.
// If target params exist, they are merged with defaults, with target params taking precedence.
func applyDefaultModelConfig(target *ModelCfg, defaults ModelCfg) {
	if target.Model == "" {
		target.Model = defaults.Model
	}
	if len(target.Params) == 0 {
		target.Params = defaults.Params
	} else {
		// Merge params: target params override default params
		mergedParams := make(map[string]interface{})
		for k, v := range defaults.Params {
			mergedParams[k] = v
		}
		for k, v := range target.Params {
			mergedParams[k] = v
		}
		target.Params = mergedParams
	}
}
