package config

import (
	"embed"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/pelletier/go-toml/v2"
)

// Configuration structs
type ModelCfg struct {
	Model  string                 `toml:"model"`
	Params map[string]interface{} `toml:"model_params"`
}

type GeneralCfg struct {
	PythonAgentPath      string `toml:"python_agent_path"`
	SummaryMode          bool   `toml:"summary_mode"`
	VerboseAgent         bool   `toml:"verbose_agent"`
	SessionTimeout       int    `toml:"session_timeout_minutes"`
	OutputThresholdBytes int    `toml:"output_threshold_bytes"`
}

type OGConfig struct {
	ManagedAgent ModelCfg   `toml:"managed_agent"`
	AuditorAgent ModelCfg   `toml:"auditor_agent"`
	General      GeneralCfg `toml:"general"`
}

const configFileName = "og_config.toml"
const defaultPromptsFileName = "prompts.toml"

// getDataDir returns the base data directory for OG.
func getDataDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "share", "og"), nil
}

// GetConfigPath returns the full path to the main configuration file.
func GetConfigPath() (string, error) {
	dir, err := getDataDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, configFileName), nil
}

// GetPromptsDir returns the full path to the prompts directory.
func GetPromptsDir() (string, error) {
	dir, err := getDataDir()
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
			PythonAgentPath:      "~/.local/share/og/agent.py",
			SummaryMode:          true,
			VerboseAgent:         false,
			SessionTimeout:       30,
			OutputThresholdBytes: 16768, // 16 * 1024 bytes
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

	expandPath := func(p string) string {
		if strings.HasPrefix(p, "~/") {
			home, _ := os.UserHomeDir() // Error ignored as this is a utility func, main will catch if critical
			return filepath.Join(home, p[2:])
		}
		return p
	}
	cfg.General.PythonAgentPath = expandPath(cfg.General.PythonAgentPath)

	// Set a default for OutputThresholdBytes if not present in config (for older configs)
	if cfg.General.OutputThresholdBytes == 0 {
		cfg.General.OutputThresholdBytes = 131072 // 128KB
	}

	return &cfg, nil
}
