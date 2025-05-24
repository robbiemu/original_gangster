#!/usr/bin/env python3
"""
OG Agent – multi‑agent orchestration with request‑ and action‑level audits
plus **HDF5‑backed session snapshots**.
"""
import argparse
import asyncio
import h5py
import json
from pathlib import Path
from smolagents import CodeAgent, ToolCallingAgent, LiteLLMModel
from smolagents.tools import ( ShellTool, FileReadTool, 
                              PythonInterpreterTool, Tool)
import sys
from typing import List, Dict, Optional


# --- NDJSON emitter for IPC ---------------------------------------------------
def emit(msg_type: str, data: dict):
    payload = {"type": msg_type, **data}
    print(json.dumps(payload), flush=True)


# --- Session management with optional HDF5 snapshots --------------------------
class AgentSession:
    """Manages session memory and stores current recipe (JSON + optional HDF5)."""

    def __init__(self, session_hash: str):
        self.session_hash = session_hash
        base_dir = Path.home() / ".local" / "share" / "og_sessions"
        base_dir.mkdir(parents=True, exist_ok=True)

        # Legacy JSON path (for human inspection)
        self.json_path = base_dir / f"{session_hash}.json"
        # Central HDF5 snapshot file (one per user)
        self.hdf5_path = base_dir / "agent_states.h5"

        self.conversation_history: List[Dict[str, str]] = []
        self.current_recipe: Optional[str] = None
        self.executed_actions: List[Dict[str, str]] = []

        # Load previous state, if any
        self._load_session()

    # Internal helpers for HDF5 I/O
    def _h5_load_json(self, group, key: str):
        if key not in group:
            return None
        try:
            return json.loads(group[key][()].decode("utf-8"))
        except Exception as e:  # pragma: no cover – corrupted data
            emit("error", {"message": f"Corrupt HDF5 dataset '{key}': {e}"})
            return None

    def _h5_write_json(self, group, key: str, obj):
        # Overwrite or create dataset with gzip compression
        payload = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        if key in group:
            del group[key]
        group.create_dataset(key, data=payload, compression="gzip")

    # Load / Save session --------------------------------------------------
    def _load_session(self):
        """Attempt HDF5 restore, fall back to JSON file."""
        if self.hdf5_path.exists():
            try:
                with h5py.File(self.hdf5_path, "r") as h5f:
                    if self.session_hash in h5f:
                        grp = h5f[self.session_hash]
                        self.conversation_history = self._h5_load_json(grp, "memory") or []
                        self.current_recipe = self._h5_load_json(grp, "recipe")
                        self.executed_actions = self._h5_load_json(grp, "executed") or []
                        emit("log", {"message": f"Loaded session '{self.session_hash}' from HDF5."})
                        return  # success -> skip JSON path
            except Exception as e:  # pragma: no cover – catch-all
                emit("error", {"message": f"Failed HDF5 load: {e}"})

        # --- Fallback: JSON file ---
        if not self.json_path.exists():
            return
        try:
            data = json.loads(self.json_path.read_text())
            self.conversation_history = data.get("conversation_history", [])
            self.current_recipe = data.get("current_recipe")
            self.executed_actions = data.get("executed_actions", [])
            emit("log", {"message": f"Loaded session '{self.session_hash}' from JSON."})
        except Exception as e:
            emit("error", {"message": f"Failed to load JSON session: {e}"})

    # end Load / Save session ----------------------------------------------
    def _save_session(self):
        """Persist to JSON, then (optionally) HDF5."""
        payload = {
            "conversation_history": self.conversation_history,
            "current_recipe": self.current_recipe,
            "executed_actions": self.executed_actions,
        }
        # --- JSON backup ---
        try:
            self.json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        except Exception as e:
            emit("error", {"message": f"Failed to save JSON session: {e}"})

        # --- HDF5 snapshot ---
        try:
            with h5py.File(self.hdf5_path, "a") as h5f:
                grp = h5f.require_group(self.session_hash)
                # Timestamp attribute helps with trace/debug
                grp.attrs["timestamp"] = asyncio.get_event_loop().time()

                self._h5_write_json(grp, "memory", self.conversation_history)
                self._h5_write_json(grp, "recipe", self.current_recipe)
                self._h5_write_json(grp, "executed", self.executed_actions)
        except Exception as e:  # pragma: no cover – disk full, permission, etc.
            emit("error", {"message": f"Failed to save HDF5 session: {e}"})

    # Public mutators – call _save_session after changes -------------------
    def add_to_history(self, role: str, content: str):
        self.conversation_history.append({"role": role, "content": content})
        self._save_session()

    def add_executed_action(self, tool_name: str, action: str, result: str):
        self.executed_actions.append({
            "tool": tool_name,
            "action": action,
            "result": result,
            "timestamp": str(asyncio.get_event_loop().time()),
        })
        self._save_session()

    def set_recipe(self, recipe: str):
        self.current_recipe = recipe
        self._save_session()

    # end Public mutators --------------------------------------------------
    def get_execution_context(self) -> str:
        """Generate a context string showing completed actions and recipe."""
        context_parts: List[str] = []

        if self.executed_actions:
            context_parts.append("Actions completed so far:")
            for i, action in enumerate(self.executed_actions, 1):
                context_parts.append(f"  {i}. {action['tool']}: {action['action']}")
                if action.get("result"):
                    result = action["result"]
                    if len(result) > 200:
                        result = result[:200] + "…"
                    context_parts.append(f"     Result: {result}")

        if self.current_recipe:
            context_parts.append("\nCurrent recipe/plan:")
            context_parts.append(self.current_recipe)

        return "\n".join(context_parts) if context_parts else "No prior actions or recipe available"


# --- Auditor tools and audit helpers -----------------------------------------
@Tool
async def count_files(path: str) -> int:
    """Return count of *path* itself and all nested files/directories."""
    p = Path(path)
    if not p.exists():
        return -1
    if p.is_file():
        return 1
    try:
        return 1 + sum(1 for _ in p.rglob("*"))
    except Exception:
        return -1


def build_audit_query(request: str, context: str = "") -> str:
    """ Audit system """
    base_query = f"""
Determine if executing the following command or set of commands would result in unsafe behavior.

Unsafe behaviors include:
- Working outside the current working directory
- Unauthorized file access/modification
- Privilege escalation (e.g. sudo, su)
- Killing or altering processes owned by other users
- Deleting or modifying logs, Git metadata, or shell history
- Removing system- or project-critical directories (e.g., /usr, /etc, .git)
- Executing arbitrary or obfuscated code (e.g., REPL injection, shell history clearing)
- Affecting more than 1000 files or directories
- Performing network operations without user confirmation
- Installing, updating, or modifying system packages

Respond ONLY with JSON in one of the following formats:

  {{"safe": true}}

  {{"safe": false, "reason": "<brief reason>"}}

  {{"safe": false, "reason": "<brief reason>", "explanation": "<summarize current step, prior actions, and remaining plan>"}}

Use the third form only when a Context section is provided. In that case, your explanation must:
- Identify the specific action that appears unsafe
- If prior actions have been taken, summarize them briefly
- Describe the current action being audited
- If a multi-step plan is in progress, summarize the remaining steps

Do NOT invent or infer steps. Only reflect what is visible in the Context section below.

---

Request to evaluate:
{request}"""

    if context.strip():
        base_query += f"""

---

Context:
{context}"""

    return base_query.strip()


async def audit_request(auditor: ToolCallingAgent, request: str, context: str) -> dict:
    """Audit a user request or action using the auditor agent."""
    prompt = build_audit_query(request, context)
    try:
        result = await auditor.run(prompt)
        result_str = str(result).strip()
        lines = result_str.splitlines()
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        return json.loads(result_str)  # fallback
    except Exception as e:
        emit("log", {"message": f"Audit parse failure: {e}, result was: {result_str}"})
        return {"safe": False, "reason": "Audit evaluation failed"}


class ProxyTool(Tool):
    """ProxyTool with per-action auditing and user approval."""

    def __init__(self, name: str, underlying: Tool, session: AgentSession, auditor: ToolCallingAgent):
        super().__init__(name=name, description=f"Proxy for {name} requiring audit & approval")
        self.underlying = underlying
        self.session = session
        self.auditor = auditor

    async def run(self, *args, **kwargs):
        # Determine a string describing the action for the audit prompt
        action_str = (
            kwargs.get("command")
            or kwargs.get("path")
            or kwargs.get("code")
            or (str(args[0]) if args else "")
        )

        context = self.session.get_execution_context()
        audit_res = await audit_request(self.auditor, action_str, context)
        if not audit_res.get("safe", False):
            emit(
                "unsafe",
                {
                    "reason": audit_res.get("reason", "Action deemed unsafe"),
                    "explanation": audit_res.get("explanation", context or action_str),
                },
            )
            return None

        desc = f"{self.name} -> {action_str}"
        self.session.add_to_history("assistant", desc)
        emit(
            "request_approval",
            {"description": desc, "action": action_str, "tool": self.name},
        )

        try:
            resp = json.loads(sys.stdin.readline())
        except Exception:
            emit("error", {"message": "Failed to read approval response"})
            return None

        if not resp.get("approved", False):
            emit("result", {"status": "cancelled", "interpret_message": "User denied execution"})
            return None

        try:
            if asyncio.iscoroutinefunction(self.underlying.run):
                res = await self.underlying.run(*args, **kwargs)
            else:
                res = self.underlying.run(*args, **kwargs)
            result_str = str(res) if res is not None else "completed"
            self.session.add_executed_action(self.name, action_str, result_str)
            self.session.add_to_history("assistant", f"executed {self.name}: {result_str}")
            return res
        except Exception as e:
            error_msg = f"Tool execution failed: {e}"
            emit("error", {"message": error_msg})
            self.session.add_executed_action(self.name, action_str, f"ERROR: {error_msg}")
            return None


class RecipeAwareCodeAgent(CodeAgent):
    """CodeAgent variant that tracks plan/recipe in AgentSession."""

    def __init__(self, *args, session: AgentSession, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session

    def plan(self, query: str) -> str:
        plan = super().plan(query)
        self.session.set_recipe(plan)
        return plan

    async def plan_without_executing(self, query: str) -> str:
        planning_prompt = f"""
Generate a detailed step-by-step plan for accomplishing this request, but do not execute any actions.
Just describe what tools would be used and what actions would be taken.

Request: {query}
"""
        result = await super().run(planning_prompt, execute_plan=False)
        plan_str = str(result)
        self.session.set_recipe(plan_str)
        return plan_str


async def run_agent(
    query: str,
    model_id: str,
    model_params: dict,
    verbose: bool,
    summary: bool,
    session_hash: str,
):
    """ --- Main Orchestration --- """
    session = AgentSession(session_hash)

    auditor_model = LiteLLMModel(model_id=model_id, **model_params)
    auditor_agent = ToolCallingAgent(model=auditor_model, tools=[count_files])

    main_model = LiteLLMModel(model_id=model_id, **model_params)
    tools = [
        ProxyTool("shell_tool", ShellTool(), session, auditor_agent),
        ProxyTool("file_tool", FileReadTool(), session, auditor_agent),
        ProxyTool("python_tool", PythonInterpreterTool(), session, auditor_agent),
    ]
    agent = RecipeAwareCodeAgent(model=main_model, tools=tools, session=session)

    # ----------------------------------------------------------------- 1. Audit
    context = session.get_execution_context()
    initial_audit = await audit_request(auditor_agent, query, context)
    if not initial_audit.get("safe", False):
        try:
            plan_explanation = await agent.plan_without_executing(query)
        except Exception:
            plan_explanation = f"Would have attempted to: {query}"
        emit(
            "unsafe",
            {
                "reason": initial_audit.get("reason", "Request deemed unsafe"),
                "explanation": plan_explanation,
            },
        )
        return

    # ----------------------------------------------------------------- 2. Plan & exec
    try:
        plan = agent.plan(query)
        emit("log", {"message": f"Generated execution plan: {plan[:200]}…"})

        result = await agent.run(query)
        finale = str(result)
        emit(
            "final_summary",
            {
                "summary": finale,
                "nutshell": finale.splitlines()[0] if finale else "",
            },
        )
    except Exception as e:
        emit("error", {"message": f"Agent execution failed: {e}"})


def main():
    """ --- CLI entry point --- """
    parser = argparse.ArgumentParser(description="OG CLI – multi-agent v6")
    parser.add_argument("--query", required=True)
    parser.add_argument("--model", default="llama3")
    parser.add_argument("--model-params", default="{}", help="JSON for model parameters")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--session-hash", required=True)
    args = parser.parse_args()

    try:
        params = json.loads(args.model_params)
        if not isinstance(params, dict):
            raise ValueError("model-params must be a JSON object")
    except Exception as e:
        emit("error", {"message": f"Invalid model-params: {e}"})
        sys.exit(1)

    try:
        asyncio.run(
            run_agent(
                query=args.query,
                model_id=args.model,
                model_params=params,
                verbose=args.verbose,
                summary=args.summary,
                session_hash=args.session_hash,
            )
        )
    except Exception as e:
        emit("error", {"message": f"Agent execution failed: {e}"})
        sys.exit(1)


if __name__ == "__main__":
    main()
