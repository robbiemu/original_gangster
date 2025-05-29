import h5py
import json
from pathlib import Path
import time
from typing import Dict, List, Optional

from .emitter import _EmitterCallable


def check_session_exists_in_h5(session_hash: str) -> bool:
    """Checks if a given session_hash exists as a group in the HDF5 state file."""
    base_dir = Path.home() / ".local" / "share" / "og"
    hdf5_path = base_dir / "agent_states.h5"

    if not hdf5_path.exists():
        return False
    
    try:
        with h5py.File(hdf5_path, "r") as h5f:
            return session_hash in h5f
    except Exception: # Catch any HDF5 file access errors
        return False


class AgentSession:
    """Manages session memory and stores current recipe (JSON + optional HDF5)."""

    def __init__(self, session_hash: str, emit: _EmitterCallable):
        self.session_hash = session_hash
        self._emit = emit # dependency injection

        base_dir = Path.home() / ".local" / "share" / "og"
        base_dir.mkdir(parents=True, exist_ok=True)

        self.json_path = base_dir / f"{session_hash}.json"
        self.hdf5_path = base_dir / "agent_states.h5"

        self.conversation_history: List[Dict[str, str]] = []
        self.current_recipe: Optional[List[Dict[str, str]]] = None # Stores list of step dictionaries
        self.fallback_action: Optional[Dict[str, str]] = None
        self.executed_actions: List[Dict[str, str]] = []
        self.original_query: Optional[str] = None

        # State for recipe approval and progress tracking
        self.is_single_step_plan: bool = False # Is this initial plan a single-step one?
        self.recipe_preapproved: bool = False # Was the overall recipe pre-approved by Go?
        self.next_expected_recipe_step_idx: int = 0 # Index of the next step in current_recipe
        self.next_expected_subcommand_idx: int = 0 # Index within the current recipe step's action (for multi-line commands)
        self.deviation_occurred: bool = False # Flag to track if agent deviated from pre-approved recipe

        self._load_session()

    # Internal helpers for HDF5 I/O
    def _h5_write_json(self, group, key: str, obj):
        payload_bytes = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        if key in group:
            del group[key]
        group.create_dataset(key, data=[payload_bytes], dtype=h5py.vlen_dtype(bytes), compression="gzip")

    def _h5_load_json(self, group, key: str):
        if key not in group:
            return None
        try:
            loaded_bytes = group[key][0]
            return json.loads(loaded_bytes.decode("utf-8"))
        except Exception as e:
            self._emit("error", {"message": f"Corrupt HDF5 dataset '{key}': {e}"})
            return None

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
                        self.fallback_action = self._h5_load_json(grp, "fallback")
                        self.executed_actions = self._h5_load_json(grp, "executed") or []
                        self.original_query = self._h5_load_json(grp, "original_query")

                        # Load state variables
                        self.is_single_step_plan = grp.attrs.get("is_single_step_plan", False)
                        self.recipe_preapproved = grp.attrs.get("recipe_preapproved", False)
                        self.next_expected_recipe_step_idx = grp.attrs.get("next_expected_recipe_step_idx", 0)
                        self.next_expected_subcommand_idx = grp.attrs.get("next_expected_subcommand_idx", 0)
                        self.deviation_occurred = grp.attrs.get("deviation_occurred", False)

                        self._emit("log", {"message": f"Loaded session '{self.session_hash}' from HDF5."})
                        return # success -> skip JSON path
            except Exception as e: # pragma: no cover – catch-all
                self._emit("error", {"message": f"Failed HDF5 load: {e}"})

        # --- Fallback: JSON file ---
        if not self.json_path.exists():
            return
        try:
            data = json.loads(self.json_path.read_text())
            self.conversation_history = data.get("conversation_history", [])
            self.current_recipe = data.get("current_recipe")
            self.fallback_action = data.get("fallback_action")
            self.executed_actions = data.get("executed_actions", [])
            self.original_query = data.get("original_query")

            # Load state variables from JSON (if present, else defaults)
            self.is_single_step_plan = data.get("is_single_step_plan", False)
            self.recipe_preapproved = data.get("recipe_preapproved", False)
            self.next_expected_recipe_step_idx = data.get("next_expected_recipe_step_idx", 0)
            self.next_expected_subcommand_idx = data.get("next_expected_subcommand_idx", 0)
            self.deviation_occurred = data.get("deviation_occurred", False)

            self._emit("log", {"message": f"Loaded session '{self.session_hash}' from JSON."})
        except Exception as e:
            self._emit("error", {"message": f"Failed to load JSON session: {e}"})

    # end Load / Save session ----------------------------------------------
    def _save_session(self):
        """Persist to JSON, then (optionally) HDF5."""
        payload = {
            "conversation_history": self.conversation_history,
            "current_recipe": self.current_recipe,
            "fallback_action": self.fallback_action,
            "executed_actions": self.executed_actions,
            "original_query": self.original_query,
            # Save state variables to JSON
            "is_single_step_plan": self.is_single_step_plan,
            "recipe_preapproved": self.recipe_preapproved,
            "next_expected_recipe_step_idx": self.next_expected_recipe_step_idx,
            "next_expected_subcommand_idx": self.next_expected_subcommand_idx,
            "deviation_occurred": self.deviation_occurred,
        }
        # --- JSON backup ---
        try:
            self.json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        except Exception as e:
            self._emit("error", {"message": f"Failed to save JSON session: {e}"})

        # --- HDF5 snapshot ---
        try:
            with h5py.File(self.hdf5_path, "a") as h5f:
                grp = h5f.require_group(self.session_hash)
                grp.attrs["timestamp"] = time.time()

                # Save state variables as HDF5 attributes for quick access
                grp.attrs["is_single_step_plan"] = self.is_single_step_plan
                grp.attrs["recipe_preapproved"] = self.recipe_preapproved
                grp.attrs["next_expected_recipe_step_idx"] = self.next_expected_recipe_step_idx
                grp.attrs["next_expected_subcommand_idx"] = self.next_expected_subcommand_idx
                grp.attrs["deviation_occurred"] = self.deviation_occurred

                self._h5_write_json(grp, "memory", self.conversation_history)
                self._h5_write_json(grp, "recipe", self.current_recipe)
                self._h5_write_json(grp, "fallback", self.fallback_action)
                self._h5_write_json(grp, "executed", self.executed_actions)
                self._h5_write_json(grp, "original_query", self.original_query)
        except Exception as e: # pragma: no cover – disk full, permission, etc.
            self._emit("error", {"message": f"Failed to save HDF5 session: {e}"})

    # Public mutators – call _save_session after changes -------------------
    def add_to_history(self, role: str, content: str):
        self.conversation_history.append({"role": role, "content": content})
        self._save_session()

    def add_executed_action(self, tool_name: str, action: str, result: str):
        self.executed_actions.append({
            "tool": tool_name,
            "action": action,
            "result": result,
            "timestamp": str(time.time()),
        })
        # Note: Index increments for recipe/subcommand are handled by ProxyTool's success path,
        # not here, because `add_executed_action` could be called for any executed action,
        # including deviations or fallbacks, where the index wouldn't necessarily increment linearly.
        self._save_session()

    def set_plan(self, recipe_steps: List[Dict[str, str]], fallback_action: Optional[Dict[str, str]]):
        self.current_recipe = recipe_steps
        self.fallback_action = fallback_action
        
        # Determine if this is a single-step plan
        self.is_single_step_plan = len(recipe_steps) == 1 and fallback_action is None

        # Reset approval state for a new plan
        self.recipe_preapproved = False
        self.next_expected_recipe_step_idx = 0
        self.next_expected_subcommand_idx = 0
        self.deviation_occurred = False
        self._save_session()
    
    def set_original_query(self, query: str):
        self.original_query = query
        self._save_session()

    # setters for session state
    def set_recipe_preapproved(self, status: bool):
        self.recipe_preapproved = status
        self._save_session()

    def set_single_step_plan_status(self, status: bool):
        self.is_single_step_plan = status
        self._save_session()

    def set_deviation_occurred(self, status: bool):
        self.deviation_occurred = status
        self._save_session()

    def increment_recipe_step(self):
        """Increments the main recipe step index and resets subcommand index."""
        self.next_expected_recipe_step_idx += 1
        self.next_expected_subcommand_idx = 0
        self._save_session()

    def increment_subcommand_idx(self):
        """Increments the subcommand index within the current recipe step."""
        self.next_expected_subcommand_idx += 1
        self._save_session()

    def get_expected_recipe_step(self) -> Optional[Dict[str, str]]:
        """Returns the currently expected recipe step dictionary."""
        if self.current_recipe and self.next_expected_recipe_step_idx < len(self.current_recipe):
            return self.current_recipe[self.next_expected_recipe_step_idx]
        return None
        
    def get_expected_subcommand(self) -> Optional[str]:
        """
        Returns the expected subcommand string based on current step and subcommand index.
        Assumes action string is newline-separated.
        """
        expected_step = self.get_expected_recipe_step()
        if expected_step and expected_step.get('tool') == 'shell_tool':
            planned_commands = expected_step.get('action', '').strip().split('\n')
            if self.next_expected_subcommand_idx < len(planned_commands):
                return planned_commands[self.next_expected_subcommand_idx].strip()
        return None

    def get_execution_context(self) -> str:
        """Generate a context string showing completed actions and the initial recipe."""
        context_parts: List[str] = []

        # Always include the original request at the top if it exists
        if self.original_query: 
            context_parts.append(f"Original Request: {self.original_query}") 

        if self.executed_actions:
            # Only add "Actions completed so far:" if there are actions, after the original request
            if self.original_query: 
                context_parts.append("") 
                
            context_parts.append("Actions completed so far:")
            for i, action in enumerate(self.executed_actions, 1):
                context_parts.append(f"  {i}. {action['tool']}: {action['action']}")
                if action.get("result"):
                    result = action["result"]
                    if len(result) > 200:
                        result = result[:200] + "…"
                    context_parts.append(f"     Result: {result}")

        # Add the original recipe only if it exists and hasn't been fully executed or deviated from
        if self.current_recipe and not self.deviation_occurred:
            context_parts.append("\nInitial recipe/plan provided to user:")
            for i, step in enumerate(self.current_recipe, 1):
                prefix = "  ✅" if i <= self.next_expected_recipe_step_idx else "  " # Mark completed main steps
                
                # If current step and it's a shell_tool, show subcommand progress
                if i == (self.next_expected_recipe_step_idx + 1) and step.get('tool') == 'shell_tool':
                    planned_commands = step.get('action', '').strip().split('\n')
                    step_status = "  ▶️" # In progress
                    if self.next_expected_subcommand_idx > 0:
                        step_status = f"  {self.next_expected_subcommand_idx}/{len(planned_commands)} " # Show progress for current step
                    
                    context_parts.append(f"{step_status} {i}. {step.get('description', 'No description')}:")
                    for sub_idx, cmd_line in enumerate(planned_commands):
                        sub_prefix = "    ✅" if sub_idx < self.next_expected_subcommand_idx else "    "
                        context_parts.append(f"{sub_prefix} {cmd_line}")
                    context_parts.append(f" ({step.get('tool', 'N/A')})")
                else:
                    context_parts.append(f"{prefix} {i}. {step.get('description', 'No description')}: {step.get('action', 'N/A')} ({step.get('tool', 'N/A')})")
            if self.fallback_action:
                context_parts.append(f"\nInitial fallback action provided to user: {self.fallback_action.get('action', 'N/A')} ({self.fallback_action.get('tool', 'N/A')})")
        elif self.deviation_occurred:
            context_parts.append("\nNote: Agent deviated from the initial pre-approved recipe. All future actions require individual approval.")

        return "\n".join(context_parts) if context_parts else "No prior actions or initial recipe available"
