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
            "execute_recipe": self._handle_execute_recipe,             # New behavior for multi-step pre-approval
            "execute_single_action": self._handle_execute_single_action, # New handler for single actions
            "execute_fallback": self._handle_execute_fallback,         # Remains the same
            "user_approval_response": self._handle_user_approval,     # Remains the same (handled by proxy tool)
        }
        
        handler = handlers.get(cmd_type)
        if handler:
            return handler(command)
        else:
            emit("error", {"message": f"Python agent received unhandled command type: {cmd_type}"})
            return False
    
    def _handle_execute_recipe(self, command: Dict) -> bool:
        """Handle execute_recipe command: user approved multi-step recipe."""
        self.session.set_recipe_preapproved(True) # Mark entire recipe as pre-approved
        self.session.reset_next_expected_step_idx() # Ensure index starts from 0 for recipe execution
        self.session.set_deviation_occurred(False) # Reset deviation flag
        
        emit("log", {"message": f"User approved recipe. Continuing session '{self.session.session_hash}' to execute pre-approved recipe steps."})
        
        # Executor agent runs the main loop, ProxyTool will handle conditional approval
        continuation_query = prepare_recipe_continuation_query(self.session)
        self._execute_and_emit_finale(continuation_query, "recipe execution")
        return False  # Exit after execution (or when agent calls final_answer)
    
    def _handle_execute_single_action(self, command: Dict) -> bool:
        """Handle execute_single_action command: user approved single initial action."""
        self.session.set_recipe_preapproved(False) # No pre-approval for single actions
        self.session.reset_next_expected_step_idx() # Ensure index is 0 for single action
        self.session.set_deviation_occurred(False) # Reset deviation flag
        
        emit("log", {"message": f"User approved single action. Continuing session '{self.session.session_hash}' to execute this action."})
        
        # Executor agent runs the main loop, ProxyTool will trigger individual approval
        # For a single action, the prompt might be simpler or directly imply the action.
        # We'll just pass a generic continuation query to let the agent decide what to do next.
        continuation_query = prepare_recipe_continuation_query(self.session) # Reuse this for now
        self._execute_and_emit_finale(continuation_query, "single action execution")
        return False # Exit after execution (or when agent calls final_answer)

    def _handle_execute_fallback(self, command: Dict) -> bool:
        """Handle execute_fallback command."""
        self.session.set_recipe_preapproved(False) # Fallback is never pre-approved
        self.session.reset_next_expected_step_idx() # Reset index
        self.session.set_deviation_occurred(True) # Fallback is a form of deviation from main recipe
        emit("log", {"message": f"Continuing session '{self.session.session_hash}' by executing fallback."})
        continuation_query = prepare_fallback_continuation_query(self.session)
        self._execute_and_emit_finale(continuation_query, "fallback continuation")
        return False  # Exit after execution
    
    def _handle_user_approval(self, command: Dict) -> bool:
        """Handle user_approval_response command."""
        # This command is primarily consumed by the ProxyTool's internal logic.
        # It means Go has sent back the user's 'y/N' response to a request_approval.
        if self.verbose:
            print(f"[agent/verbose] Received user_approval_response in main loop. This response is usually handled by ProxyTool: {command.get('approved')}", file=sys.stderr)
        return True  # Continue processing, as the ProxyTool will consume this.
    
    def _execute_and_emit_finale(self, continuation_query: str, execution_type: str) -> None:
        """Execute query and emit final summary."""
        try:
            finale = self.executor_agent.run(continuation_query)
            emit("final_summary", {
                "summary": finale,
                "nutshell": finale.splitlines()[0] if finale else "",
            })
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            emit("error", {"message": f"Agent execution failed during {execution_type}: {e}"})
            if self.verbose:
                print("[agent/verbose] Full stack trace:", file=sys.stderr)
                print(tb, file=sys.stderr)
            sys.exit(1)
