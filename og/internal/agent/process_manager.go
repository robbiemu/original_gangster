package agent

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
	"time"

	"github.com/robbiemu/original_gangster/og/internal/config"
	"github.com/robbiemu/original_gangster/og/internal/ui"
)

// AgentProcessManager manages the Python agent's process.
type ProcessManager struct {
	cmd           *exec.Cmd
	stdinPipe     io.WriteCloser
	stdoutScanner *bufio.Scanner
	stderrScanner *bufio.Scanner
	mu            sync.Mutex
	ui            ui.UI // Dependency injection for UI
	minGoLogLevel ui.LogLevel
}

// NewProcessManager creates a new ProcessManager.
func NewProcessManager(ui ui.UI, minGoLogLevel ui.LogLevel) *ProcessManager {
	return &ProcessManager{ui: ui, minGoLogLevel: minGoLogLevel}
}

// Start initiates the Python agent process.
func (pm *ProcessManager) Start(cfg *config.OGConfig, sessionHash, query, workdir string, jsonLogsEnabled bool, cacheDirPath string) error {
	pm.mu.Lock()
	defer pm.mu.Unlock()

	// Marshal parameters for each agent
	executorParams, _ := json.Marshal(cfg.ExecutorAgent.Params)
	plannerParams, _ := json.Marshal(cfg.PlannerAgent.Params)
	auditorParams, _ := json.Marshal(cfg.AuditorAgent.Params)

	pythonAgentFilePath := cfg.General.PythonAgentPath

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
		"--session-hash", sessionHash,
		"--query", query,
		"--workdir", workdir,
		// Pass models and params for each agent
		"--executor-model", cfg.ExecutorAgent.Model,
		"--executor-params", string(executorParams),
		"--planner-model", cfg.PlannerAgent.Model,
		"--planner-params", string(plannerParams),
		"--auditor-model", cfg.AuditorAgent.Model,
		"--auditor-params", string(auditorParams),
		"--output-threshold-bytes", fmt.Sprintf("%d", cfg.General.OutputThresholdBytes),
		"--json-logs-enabled", fmt.Sprintf("%t", jsonLogsEnabled),
		"--cache-directory", cacheDirPath,
	}

	cmdArgs = append(cmdArgs, "--verbosity", cfg.General.VerbosityLevel.String())

	if cfg.General.SummaryMode {
		cmdArgs = append(cmdArgs, "--summary-mode")
	}

	pm.cmd = exec.Command(cmdArgs[0], cmdArgs[1:]...)

	env := os.Environ()
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
	pm.cmd.Env = append(env, "PYTHONPATH="+newPythonPathValue)

	stdin, err := pm.cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("failed to create stdin pipe: %w", err)
	}
	pm.stdinPipe = stdin

	stdout, err := pm.cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("failed to create stdout pipe: %w", err)
	}
	// Increase the buffer size for stdout scanner to handle potentially large JSON lines.
	const maxScanTokenSize = 1024 * 1024 // 1 MB
	buf := make([]byte, 0, maxScanTokenSize)
	pm.stdoutScanner = bufio.NewScanner(stdout)
	pm.stdoutScanner.Buffer(buf, maxScanTokenSize)

	stderr, err := pm.cmd.StderrPipe()
	if err != nil {
		return fmt.Errorf("failed to create stderr pipe: %w", err)
	}
	pm.stderrScanner = bufio.NewScanner(stderr)
	go func() {
		for pm.stderrScanner.Scan() {
			pm.ui.PrintStderr(pm.stderrScanner.Text(), pm.minGoLogLevel)
		}
	}()

	if err := pm.cmd.Start(); err != nil {
		return fmt.Errorf("failed to start python agent command: %w", err)
	}
	return nil
}

// Stop cleans up the Python agent process.
func (pm *ProcessManager) Stop() {
	pm.mu.Lock()
	defer pm.mu.Unlock()
	if pm.stdinPipe != nil {
		pm.stdinPipe.Close()
	}
	if pm.cmd != nil && pm.cmd.Process != nil {
		done := make(chan struct{})
		go func() {
			pm.cmd.Wait()
			close(done)
		}()
		select {
		case <-done:
			// Python exited cleanly
		case <-time.After(5 * time.Second):
			// Timeout, force kill
			pm.ui.PrintColored(pm.ui.Yellow, "Python agent did not exit gracefully, forcing kill.\n")
			pm.cmd.Process.Kill()
		}
	}
}

// SendCommand marshals and sends a generic command to Python.
func (pm *ProcessManager) SendCommand(cmdType string, data map[string]interface{}) error {
	pm.mu.Lock()
	defer pm.mu.Unlock()

	payload := map[string]interface{}{"type": cmdType}
	for k, v := range data {
		payload[k] = v
	}

	b, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("failed to marshal command payload: %w", err)
	}
	if _, err := fmt.Fprintf(pm.stdinPipe, "%s\n", string(b)); err != nil {
		return fmt.Errorf("failed to write command to python stdin: %w", err)
	}
	return nil
}

// StdoutScanner returns the scanner for Python's stdout.
func (pm *ProcessManager) StdoutScanner() *bufio.Scanner {
	return pm.stdoutScanner
}
