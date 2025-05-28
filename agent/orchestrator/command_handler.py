import sys
from typing import Dict

from agent.emitter import emit
from agent.prompts import prepare_fallback_continuation_query, prepare_recipe_continuation_query
from agent.session import AgentSession


class CommandHandler:
    """Handles incoming commands from Go client."""
    
    def __init__(self, executor_agent, session: AgentSession, verbose: bool):
        self.executor_agent = executor_agent
        self.session = session
        self.verbose = verbose
    
    def handle_command(self, command: Dict) -> bool:
        """Handle a single command. Returns True if should continue, False if should exit."""
        cmd_type = command.get("type")
        
        if self.verbose:
            print(f"[agent/verbose] Received command: {cmd_type}", file=sys.stderr)
        
        handlers = {
            "execute_recipe": self._handle_execute_recipe,
            "execute_single_action": self._handle_execute_single_action,
            "execute_fallback": self._handle_execute_fallback,
            "user_approval_response": self._handle_user_approval, # This should not cause an exit by itself
            "deny_current_action": self._handle_deny_current_action, # NEW: for user denials from ProxyTool
        }
        
        handler = handlers.get(cmd_type)
        if handler:
            return handler(command)
        else:
            emit("error", {"message": f"Python agent received unhandled command type: {cmd_type}"})
            return False
    
    def _handle_execute_recipe(self, command: Dict) -> bool:
        """Handle execute_recipe command: user approved multi-step recipe."""
        self.session.set_single_step_plan_status(False) # Not a single-step plan
        self.session.set_recipe_preapproved(True) # Mark entire recipe as pre-approved
        self.session.increment_recipe_step() # Move to first step (index 0)
        self.session.set_deviation_occurred(False) # Reset deviation flag
        
        emit("log", {"message": f"User approved recipe. Continuing session '{self.session.session_hash}' to execute pre-approved recipe steps."})
        
        # Executor agent runs the main loop, ProxyTool will handle conditional approval
        continuation_query = prepare_recipe_continuation_query(self.session)
        self._execute_and_emit_finale(continuation_query, "recipe execution")
        return False  # Agent will emit final_summary when done.

    def _handle_execute_single_action(self, command: Dict) -> bool:
        """Handle execute_single_action command: Go frontend decided to auto-proceed to individual step approval."""
        self.session.set_single_step_plan_status(True) # Mark as single-step plan
        self.session.set_recipe_preapproved(False) # No overall recipe pre-approval
        self.session.increment_recipe_step() # Move to first step (index 0)
        self.session.set_deviation_occurred(False) # Reset deviation flag
        
        emit("log", {"message": f"Proceeding with single action. Session '{self.session.session_hash}' will request individual approval for the first action."})
        
        # Executor agent runs the main loop, ProxyTool will trigger individual approval for the first action
        continuation_query = prepare_recipe_continuation_query(self.session) # Reuse this for now
        self._execute_and_emit_finale(continuation_query, "single action execution")
        return False # Agent will emit final_summary when done.

    def _handle_execute_fallback(self, command: Dict) -> bool:
        """Handle execute_fallback command."""
        self.session.set_single_step_plan_status(False) # Fallback is its own flow
        self.session.set_recipe_preapproved(False) # Fallback is never pre-approved
        self.session.increment_recipe_step() # Initialize to step 0 for fallback if needed
        self.session.set_deviation_occurred(True) # Fallback implies a deviation from main recipe
        emit("log", {"message": f"Continuing session '{self.session.session_hash}' by executing fallback."})
        continuation_query = prepare_fallback_continuation_query(self.session)
        self._execute_and_emit_finale(continuation_query, "fallback continuation")
        return False  # Agent will emit final_summary when done.

    def _handle_user_approval(self, command: Dict) -> bool:
        """Handle user_approval_response command: This is consumed by the ProxyTool."""
        # This command is primarily consumed by the ProxyTool's internal logic
        # via sys.stdin.readline(). It means Go has sent back the user's 'y/N' response
        # to a request_approval. It does NOT signal termination.
        if self.verbose:
            print(f"[agent/verbose] Received user_approval_response. This response is usually handled by ProxyTool: {command.get('approved')}", file=sys.stderr)
        return True # Continue processing, as the ProxyTool will consume this.

    def _handle_deny_current_action(self, command: Dict) -> bool:
        """NEW: Handle denial of an individual action during execution."""
        emit("log", {"message": f"User denied execution of current action. Providing summary and ending session."})
        # The agent will now exit, so provide a summary of what happened so far.
        self._emit_final_summary_on_denial("User denied the proposed action.")
        return False # Signal to exit the Python process

    def _emit_final_summary_on_denial(self, reason: str) -> None:
        """Helper to emit a final summary upon explicit denial."""
        summary = f"Session terminated by user denial. {reason}"
        nutshell = f"Session cancelled: {reason}"
        if self.session.executed_actions:
            nutshell += f" Last action: {self.session.executed_actions[-1]['action']}"
        emit("final_summary", {
            "summary": summary,
            "nutshell": nutshell,
            "reason": reason,
            "status": "cancelled", # Mark as cancelled
        })

    def _execute_and_emit_finale(self, continuation_query: str, execution_type: str) -> None:
        """Execute query and emit final summary when the agent finishes."""
        try:
            finale = self.executor_agent.run(continuation_query)
            emit("final_summary", {
                "summary": finale,
                "nutshell": finale.splitlines()[0] if finale else "",
                "status": "success", # Assume success if agent returns final_answer
            })
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            emit("error", {"message": f"Agent execution failed during {execution_type}: {e}"})
            if self.verbose:
                print("[agent/verbose] Full stack trace:", file=sys.stderr)
                print(tb, file=sys.stderr)
            self._emit_final_summary_on_denial(f"Agent experienced an unrecoverable error: {e}")
            sys.exit(1)
