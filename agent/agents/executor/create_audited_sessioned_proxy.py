import json
import sys
from typing import Any, Callable
from smolagents import ToolCallingAgent
from smolagents.tools import Tool

from agent.agents.auditor.agent import audit_request
from agent.emitter import _EmitterCallable
from agent.session import AgentSession
from agent.proxy_tool import ProxyTool


def create_audited_sessioned_proxy(
    name: str,
    tool: Tool,
    session: AgentSession,
    auditor: ToolCallingAgent,
    emit: _EmitterCallable
) -> ProxyTool:
    """
    Factory function to create a ProxyTool instance configured with agent
    session and auditing and user approval aspects.
    """

    # Helper function to derive a descriptive string for the action.
    def _get_action_string(*args, **kwargs) -> str:
        return (
            kwargs.get("command")
            or kwargs.get("path")
            or kwargs.get("code")
            or (str(args[0]) if args else "")
            or "an unknown action"
        )

    def _around_hook(proxy_instance: ProxyTool, proceed_callable: Callable, *args, **kwargs) -> Any:
        """
        [Around Cut] - This hook encapsulates all the logic from the original
        proxy_tool.py's `run` method: auditing, user approval, execution,
        and final status emitting.
        """
        action_str = _get_action_string(*args, **kwargs)
        context = session.get_execution_context()

        # --- 1. Audit Logic (originally in _before_execute) ---
        audit_res = audit_request(auditor, action_str, context)

        if audit_res.get("log_message"):
            emit("log", {"message": audit_res["log_message"]})

        if not audit_res.get("safe", False):
            emit(
                "unsafe",
                {
                    "reason": audit_res.get("reason", "Action deemed unsafe"),
                    "explanation": audit_res.get("explanation", context or action_str),
                },
            )
            # If deemed unsafe, mark deviation if not already, and short-circuit execution.
            if not session.deviation_occurred:
                session.set_deviation_occurred(True)
            return None


        # --- 2. User Approval Logic (conditional based on recipe status) ---
        should_request_approval = True # Default to requesting approval

        if session.recipe_preapproved and not session.deviation_occurred:
            # Check if the current action matches the next expected step in the pre-approved recipe
            expected_step_idx = session.next_expected_recipe_step_idx
            if expected_step_idx < len(session.current_recipe):
                expected_step = session.current_recipe[expected_step_idx]
                
                # Check for exact match of action and tool name
                # Trim whitespaces for robust comparison
                if (action_str.strip() == expected_step.get('action', '').strip() and
                    proxy_instance.name == expected_step.get('tool', '')):
                    
                    emit("log", {"message": f"[AGENT] Auto-approving pre-approved recipe step {expected_step_idx + 1}: '{action_str}' ({proxy_instance.name})"})
                    should_request_approval = False # Skip user approval
                else:
                    # Deviation detected!
                    emit("log", {"message": f"[AGENT] Deviation detected! Expected '{expected_step.get('action')}' ({expected_step.get('tool')}), got '{action_str}' ({proxy_instance.name}). Requesting approval."})
                    session.set_deviation_occurred(True) # Set deviation flag
                    should_request_approval = True # Request approval for this deviated step
            else:
                # No more steps in the pre-approved recipe, but agent is trying to act.
                # This also constitutes a deviation or continuation beyond the plan.
                emit("log", {"message": f"[AGENT] Agent attempting action '{action_str}' ({proxy_instance.name}) beyond pre-approved recipe. Requesting approval."})
                session.set_deviation_occurred(True)
                should_request_approval = True


        if should_request_approval:
            desc = f"{proxy_instance.name} -> {action_str}"
            session.add_to_history("assistant", desc)
            emit(
                "request_approval",
                {"description": desc, "action": action_str, "tool": proxy_instance.name},
            )

            resp = {}
            try:
                # Read response from Go client
                resp_line = sys.stdin.readline()
                if not resp_line:
                    emit("error", {"message": "Received EOF or empty line from stdin. Go client might have terminated unexpectedly."})
                    return None
                resp = json.loads(resp_line)
            except json.JSONDecodeError:
                emit("error", {"message": f"Failed to parse approval response from stdin: '{resp_line.strip()}'"})
                return None
            except Exception as e:
                emit("error", {"message": f"Failed to read approval response: {e}"})
                return None

            if not resp.get("approved", False):
                emit("result", {"status": "cancelled", "interpret_message": "User denied execution"})
                return None

        # --- 3. Execute Underlying Tool and Handle Outcome ---
        try:
            # The proceed_callable executes the actual tool (e.g., shell_tool, file_tool)
            res = proceed_callable(*args, **kwargs)
            result_str = str(res) if res is not None else "completed"

            # Add to executed actions history (this will also increment next_expected_recipe_step_idx if pre-approved)
            session.add_executed_action(proxy_instance.name, action_str, result_str)

            emit("result", {"status": "success", "interpret_message": f"Executed {proxy_instance.name}", "output": result_str})
            return res
        except Exception as e:
            error_msg = f"Tool execution failed: {e}"
            emit("error", {"message": error_msg})
            session.add_executed_action(proxy_instance.name, action_str, f"ERROR: {error_msg}")
            emit("result", {"status": "failure", "interpret_message": error_msg, "output": ""})
            # Mark deviation if an error occurred during execution of a pre-approved step
            if session.recipe_preapproved and not session.deviation_occurred:
                session.set_deviation_occurred(True)
            return None

    # Determine proxy description (remains the same)
    underlying_description = getattr(tool, "description", None)
    if not underlying_description:
        doc = getattr(tool, "__doc__", "")
        underlying_description = doc.strip().split("\n")[0] if doc else "an unspecified action"
    
    proxy_description = f"Ask user approval for: {underlying_description}"

    # Instantiate ProxyTool, only providing the comprehensive_around_hook
    return ProxyTool(
        name=name,
        underlying=tool,
        description=proxy_description,
        around_hook=_around_hook,
    )
