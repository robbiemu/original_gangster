# OG
<small>the original gangster</small>

**OG** is a command-line AI agent that pairs a fast Go CLI with a Python-based [smolagents](https://github.com/huggingface/smolagents), letting you collaborate with an LLM to:

* Understand source files and repo history
* Get help with shell commands
* Review diffs and plan safe actions
* Walk through multi-step "recipes" with approval gating


## 🔍 Example Uses

### 🧠 Understand What a Script Does

```bash
og "what does the script bin/git-rewind do?"
```

> OG reads and summarizes the script, explaining its behavior in plain language.

---

### 🧾 Review Diffs from Commit History

```bash
og "show me the diff for bin/script.py at the sixth commit"
```

> OG checks out the repo history, finds the specified commit, and displays a formatted diff with commentary.

---

### 🛠 Get Help Building a Shell Command

```bash
og "how do I use ffmpeg to compress a 4k video down to 1080p for web?"
```

> OG proposes a command with flags explained.

---

### 🧪 Plan a Multi-step Recipe with Approval

```bash
og "remove all .DS_Store files, then zip each top-level folder in this directory"
```

> OG:

* Breaks the task into a step-by-step recipe
* Prompts you to approve or deny it
* Falls back to a one-liner if you deny the plan

---

### 🔐 Safety-First CLI Automation


```bash
og "change the password for macdev"
```

> OG:

* Will not attempt to run commands you would need sudo access for. (intended to be configurable behavior)

```bash
og "delete all branches except main and staging"
```

> OG:

* Confirms which branches would be deleted
* Asks you to approve each destructive action
* Explains the result and any errors


## ⚙️ Features

* 🧩 Modular Go + Python architecture
* 🧠 Smolagent-based planning and command execution
* 🛠️ Fallback action if you reject the proposed plan
* ✅ Human approval required before any action is run
* 📜 Summary and interpretation of results
* 🗂 Uses your local Ollama model via LiteLLM

## 🚀 Quickstart

### 1. Clone and build

```bash
git clone https://github.com/yourusername/original_gangster.git
cd original_gangster
make build         # builds ./build/og
uv venv            # sets up Python venv
source .venv/bin/activate
uv pip install -e .  # installs agent CLI
```

### 2. Create and configure `og_config.json`

```bash
og init
```

Then edit:

```json
{
  "python_agent_path": "/absolute/path/to/.venv/bin/agent",
  "ollama_model": "llama3",
  "ollama_host": "http://localhost:11434",
  "summary_mode": true,
  "verbose_agent": true,
  "default_history_path": "~/.local/share/og_history.json"
}
```

### 3. Run your first query

```bash
og "show me the diff for bin/script.py at the sixth commit"
```


## 📂 Config Reference (`~/.local/share/og_config.json`)

| Key                    | Description                                                 |
| ---------------------- | ----------------------------------------------------------- |
| `python_agent_path`    | Path to the Python agent binary (usually `.venv/bin/agent`) |
| `ollama_model`         | LLM model name (e.g. `llama3`)                              |
| `ollama_host`          | Base URL for Ollama's API                                   |
| `summary_mode`         | Whether to show final summaries                             |
| `verbose_agent`        | Enables detailed logs from the agent                        |
| `default_history_path` | Reserved for future features                                |


## 🛠 Development Tips

* Use `make go-run` to run the Go CLI
* Use `agent --help` to test the Python agent independently
* NDJSON is used between Go and Python for structured IPC


## License

Licensed under the LPGL (see the [LICENSE](LICENSE) file) for a freer tomorrow.