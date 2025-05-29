# OG Configuration File (`og_config.toml`)

The `og_config.toml` file allows you to customize the behavior of the OG agent, including the models used, their parameters, and general application settings.

## Location

The configuration file is located in your user's local data directory:
`~/.local/share/og/og_config.toml`

If this file does not exist, you can generate a default configuration by running:
`og init`

## Structure

The configuration is organized into several sections:

*   `[default_agent]`: Defines a fallback model and parameters for agents that don't have specific configurations.
*   `[executor_agent]`: Configures the model and parameters for the agent responsible for executing actions.
*   `[planner_agent]`: Configures the model and parameters for the agent responsible for generating plans.
*   `[auditor_agent]`: Configures the model and parameters for the agent responsible for security auditing.
*   `[general]`: Contains general application settings for the Go CLI and Python agent.
*   `[cache]`: Contains settings for managing session JSON logs.

## Sections

### `[default_agent]`

This section defines the default Large Language Model (LLM) and its parameters that will be used by other agents (Executor, Planner, Auditor) if they do not explicitly specify their own `model` or `model_params`.

*   `model` (string): The identifier for the default LLM.
    *   Example: `"ollama/llama3:latest"`, `"openai/gpt-4o"`
*   `model_params` (table): A TOML table (which maps to a JSON object/dictionary) of parameters specific to the chosen `model`. These parameters are passed directly to the LLM provider.
    *   Example: `base_url = "http://localhost:11435"`, `temperature = 0.7`

**Inheritance and Merging Logic:**
If an agent-specific section (e.g., `[executor_agent]`) is missing its `model` field, the value from `default_agent.model` will be used. If it provides its own `model`, that will override the default.

For `model_params`:
*   If an agent-specific section is missing its `model_params` table (or it's explicitly empty `model_params = {}`), the *entire* `default_agent.model_params` map will be copied.
*   If an agent-specific section *does* provide `model_params` (even if it contains only one key), its parameters will be **merged** with the `default_agent`'s `model_params`. In case of a key conflict (e.g., both define `temperature`), the agent-specific parameter takes precedence.

### `[executor_agent]`

Configures the LLM used by the Executor Agent. This agent is responsible for taking the planned steps and executing them, often interacting with tools like the shell or file system.

*   `model` (string, optional): The model ID for the Executor Agent. If omitted, `default_agent.model` will be used.
*   `model_params` (table, optional): Parameters for the Executor Agent's model. If omitted, `default_agent.model_params` will be used (merged with any provided parameters).

### `[planner_agent]`

Configures the LLM used by the Planner Agent. This agent is responsible for generating the initial high-level plan or "recipe" to address the user's query.

*   `model` (string, optional): The model ID for the Planner Agent. If omitted, `default_agent.model` will be used.
*   `model_params` (table, optional): Parameters for the Planner Agent's model. If omitted, `default_agent.model_params` will be used (merged with any provided parameters).

### `[auditor_agent]`

Configures the LLM used by the Auditor Agent. This agent performs security and safety checks on proposed actions and plans.

*   `model` (string, optional): The model ID for the Auditor Agent. If omitted, `default_agent.model` will be used.
*   `model_params` (table, optional): Parameters for the Auditor Agent's model. If omitted, `default_agent.model_params` will be used (merged with any provided parameters).

### `[general]`

Contains general application settings for the OG CLI and the Python agent.

*   `python_agent_path` (string): The file path to the main Python agent script (`agent/main.py`). This path supports `~/` for the user's home directory.
    *   Example: `"~/.local/share/og/agent/main.py"`
*   `summary_mode` (boolean): If `true`, enables a "summary mode" where the agent provides a final summary report to the user upon completion. This is independent of logging verbosity.
*   `verbosity_level` (string): Sets the minimum logging verbosity level for both the Go client and the Python agent's internal logs. Messages at or above this level will be displayed.
    *   Valid values: `"debug"`, `"info"`, `"warn"`, `"none"`.
    *   Default: `"info"`
*   `session_timeout_minutes` (integer): The duration in minutes after which a session might be considered timed out. (Currently used for Go-side tracking, not active timeout enforcement in the provided code).
*   `output_threshold_bytes` (integer): The maximum size (in bytes) of tool output that will be printed directly to the console. If a tool's output exceeds this threshold, it will be saved to a temporary file, and a message indicating the file path will be printed instead.
    *   Default: `131072` (128KB)
    *   Example: `16768` (approx. 16KB)

### `[cache]`

Contains settings for managing session JSON logs, which store conversation history and session state.

*   `json_logs` (boolean): If `true`, session state will be saved to JSON files in the specified `directory`. If `false`, JSON logging is disabled (HDF5 session persistence will still be active).
    *   Default: `true`
*   `directory` (string, optional): A path for storing JSON session files.
    *   If a relative path (e.g., `"my_logs"`), it's treated as a subdirectory within `~/.local/share/og/`.
    *   If empty (`directory = ""`), files are stored directly in `~/.local/share/og/`.
    *   Supports `~/` for user home directory.
    *   Default: `""` (empty, resolves to `~/.local/share/og/`)
*   `expiration` (integer, optional): The number of days after which session JSON files (in the `directory`) are considered expired and will be automatically deleted by the Go CLI at the start of a new session.
    *   Set to `0` (default) for no expiration/automatic deletion.
    *   Example: `expiration = 7` to delete files older than 7 days.

## Example `og_config.toml`

```toml
# Default settings for agents that don't specify their own
[default_agent]
model = "ollama/llama3:latest"
model_params = { base_url = "http://localhost:11435", temperature = 0.7 }

# Configuration for the Executor Agent
# Inherits model and base_url from [default_agent]
[executor_agent]
# model = "ollama/llama3:latest" # Can be specified to override default_agent
model_params = { temperature = 0.7, num_ctx = 32768 } # These params will merge with (and override) default_agent's params

# Configuration for the Planner Agent
# Inherits model and base_url from [default_agent]
[planner_agent]
model_params = { temperature = 0.5, top_p = 0.9 } # Higher temperature for more creative planning

# Configuration for the Auditor Agent
# Explicitly sets a different model, overriding default_agent's model
[auditor_agent]
model = "ollama/gemma3:27b-it"
model_params = { base_url = "http://localhost:11435", temperature = 0.2 } # Specific params for auditing

# General application settings
[general]
python_agent_path = "~/.local/share/og/agent/main.py"
summary_mode = true
verbosity_level = "info"
session_timeout_minutes = 30
output_threshold_bytes = 131072 # Default to 128KB

# Cache settings for session JSON logs
[cache]
json_logs = true    # Enable saving of JSON session files
directory = ""      # Store JSON files directly in ~/.local/share/og/
expiration = 0      # No automatic expiration