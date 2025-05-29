# agent/agents/executor/create_audited_sessioned_proxy.py

import json
import sys
from pathlib import Path
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
    emit: _EmitterCallable,
    output_threshold_bytes: int 
) -> ProxyTool:
    """
    Factory function to create a ProxyTool instance configured with agent
    session and auditing and user approval aspects.
    """

    # Helper function to derive a descriptive string for the action.
    def _get_action_string(*args, **kwargs) -> str:
        # Assuming 'command' is the primary argument for shell_tool
        # and 'path' for file_tool. Generalize if other tools have different primary args.
        return (
            kwargs.get("command")
            or kwargs.get("path")
            or kwargs.get("code") # For a potential 'code_tool'
            or (str(args[0]) if args else "")
            or "an unknown action"
        )

    def _around_hook(proxy_instance: ProxyTool, proceed_callable: Callable, *args, **kwargs) -> Any:
        """
        [Around Cut] - This hook encapsulates all the logic for auditing, user approval, execution.
        """
        action_str = _get_action_string(*args, **kwargs)
        context = session.get_execution_context()

        # 1. Always perform a security audit using the Auditor Agent
        audit_res = audit_request(auditor, action_str, context)

        if audit_res.get("log_message"):
            emit("log", {"message": audit_res["log_message"]})

        if not audit_res.get("safe", False):
            # If deemed unsafe, always signal termination and mark deviation if not already.
            if not session.deviation_occurred:
                session.set_deviation_occurred(True)
            emit("unsafe", {
                "reason": audit_res.get("reason", "Action deemed unsafe by auditor"),
                "explanation": audit_res.get("explanation", context or action_str),
            })
            emit("deny_current_action", {"message": "Action was deemed unsafe by auditor."}) # Signal to Go to terminate
            return None # Stop execution flow

        # 2. Determine if user approval is required for this specific action
        should_request_approval = True
        is_current_action_expected_by_recipe = False # Flag for matching recipe parts

        expected_step = session.get_expected_recipe_step()

        if expected_step: # We have a current recipe step to compare against
            # Check if tool name matches
            if proxy_instance.name == expected_step.get('tool', ''):
                planned_action_content = expected_step.get('action', '').strip()
                # Split planned action into subcommands by newline
                planned_subcommands = planned_action_content.split('\n')
                
                # Compare the *current* action_str to the *expected subcommand*
                expected_subcommand = session.get_expected_subcommand()

                if expected_subcommand is not None and action_str.strip() == expected_subcommand:
                    is_current_action_expected_by_recipe = True
                    # Check if all *previous* subcommands for this step were also executed correctly
                    all_previous_subcommands_matched = True
                    for i in range(session.next_expected_subcommand_idx):
                        # This part assumes add_executed_action stores enough info,
                        # or we would need a more complex history validation here.
                        # For simplicity, we trust session.next_expected_subcommand_idx.
                        pass # If we reached here, previous increments imply matches.

                    if all_previous_subcommands_matched: # This is the auto-approval condition
                        # Auto-approval conditions:
                        # 1. It's a multi-step recipe AND the recipe was pre-approved.
                        # 2. It's a single-step plan AND it's the very first action (already initially approved by Go).
                        if (session.recipe_preapproved and not session.deviation_occurred) or \
                           (session.is_single_step_plan and session.next_expected_recipe_step_idx == 0 and session.next_expected_subcommand_idx == 0):
                            
                            emit("log", {"message": f"[AGENT] Auto-approving expected recipe step {session.next_expected_recipe_step_idx + 1}, subcommand {session.next_expected_subcommand_idx + 1}: '{action_str}' ({proxy_instance.name})"})
                            should_request_approval = False # Skip user approval
                        else:
                            # This case should ideally not be hit if conditions above are exhaustive.
                            # It implies a pre-approved recipe got deviated or a single-step plan
                            # is attempting a non-first action without prior approval.
                            emit("log", {"message": f"[AGENT] Auto-approval condition not met for '{action_str}'. Requesting individual approval."})
                            session.set_deviation_occurred(True) # Mark deviation if auto-approval failed unexpectedly
                            should_request_approval = True

                else:
                    # Current action_str does not match the expected subcommand OR expected_subcommand is None
                    # This indicates a deviation from the plan (either incorrect subcommand or too many subcommands for a step)
                    emit("log", {"message": f"[AGENT] Deviation detected! Planned step {session.next_expected_recipe_step_idx + 1} expected '{expected_subcommand}', got '{action_str}'. Requesting approval."})
                    session.set_deviation_occurred(True) # Set deviation flag
                    should_request_approval = True # Request approval for this deviated step
            else:
                # Tool name mismatch -> deviation
                emit("log", {"message": f"[AGENT] Deviation detected! Expected tool '{expected_step.get('tool')}', got '{proxy_instance.name}'. Requesting approval."})
                session.set_deviation_occurred(True)
                should_request_approval = True
        else:
            # No expected step, likely means agent is taking action outside of initial plan or after completion.
            # This is implicitly a deviation if the session was pre-approved.
            if session.recipe_preapproved and not session.deviation_occurred:
                emit("log", {"message": f"[AGENT] Agent attempting action '{action_str}' ({proxy_instance.name}) beyond pre-approved recipe. Requesting approval."})
                session.set_deviation_occurred(True)
            should_request_approval = True # Always request approval if no explicit matching step

        # --- If approval is still required, interact with user ---
        if should_request_approval:
            desc = f"{proxy_instance.name} -> {action_str}"
            session.add_to_history("assistant", desc) # Log before requesting approval
            emit(
                "request_approval",
                {"description": desc, "action": action_str, "tool": proxy_instance.name},
            )

            resp = {}
            try:
                resp_line = sys.stdin.readline()
                if not resp_line:
                    # This is a critical state, likely Go client crashed or closed pipe.
                    emit("error", {"message": "Received EOF or empty line from stdin during approval. Go client might have terminated unexpectedly."})
                    emit("deny_current_action", {"message": "Go client communication failed."}) # Signal to Go to terminate
                    return None # Stop execution flow
                resp = json.loads(resp_line)
            except json.JSONDecodeError:
                emit("error", {"message": f"Failed to parse approval response from stdin: '{resp_line.strip()}'"})
                emit("deny_current_action", {"message": "Invalid approval response received."}) # Signal to Go to terminate
                return None # Stop execution flow
            except Exception as e:
                emit("error", {"message": f"Failed to read approval response: {e}"})
                emit("deny_current_action", {"message": f"Error reading approval: {e}"}) # Signal to Go to terminate
                return None # Stop execution flow

            if not resp.get("approved", False):
                emit("result", {"status": "cancelled", "interpret_message": "User denied execution"})
                emit("deny_current_action", {"message": "User explicitly denied the action."}) # Signal to Go to terminate
                return None # Stop execution flow

        # 3. Execute Underlying Tool and Handle Outcome (only if approved or auto-approved)
        try:
            res = proceed_callable(*args, **kwargs)
            
            result_str = str(res) if res is not None else "completed"
            
            # Only apply this logic for tools that output strings (like shell_tool, file_content_tool)
            # and if the output is not empty/trivial.
            if isinstance(res, str) and res.strip():
                output_bytes = res.encode('utf-8')
                # Use the passed output_threshold_bytes
                if output_threshold_bytes > 0 and len(output_bytes) > output_threshold_bytes:
                    temp_dir_path = Path("/tmp") / "og" / session.session_hash
                    temp_dir_path.mkdir(parents=True, exist_ok=True) # Ensure directory exists
                    
                    # Use a unique file name based on tool name and an index
                    # session.executed_actions provides a good proxy for an incrementing index
                    turn_index = len(session.executed_actions) # This is before adding current action
                    file_name = f"{turn_index+1}_{proxy_instance.name.replace(' ', '_')}.txt"
                    temp_file_path = temp_dir_path / file_name
                    
                    try:
                        temp_file_path.write_bytes(output_bytes)
                        result_str = (
                            f"-- out saved to {temp_file_path} because at "
                            f"{(len(output_bytes) / 1024):.2f} KB, it is too long to include. "
                            f"Use tools (for example perhaps `grep` or `cat {temp_file_path}`) to find the details that you require --"
                        )
                        emit("log", {"message": f"Tool output saved to temporary file: {temp_file_path}"})
                    except Exception as file_e:
                        emit("error", {"message": f"Failed to save large tool output to {temp_file_path}: {file_e}. Returning full output."})
                        result_str = str(res) # Fallback to returning full output if file write fails

            # Add to executed actions history (this also adds to next_expected_recipe_step_idx if pre-approved)
            session.add_executed_action(proxy_instance.name, action_str, result_str)

            # Update session progress only if it was an expected action
            if is_current_action_expected_by_recipe:
                session.increment_subcommand_idx() # Increment subcommand index

                # Check if all subcommands for the current recipe step are done
                expected_step_after_increment = session.get_expected_recipe_step()
                if expected_step_after_increment:
                    planned_commands = expected_step_after_increment.get('action', '').strip().split('\n')
                    if session.next_expected_subcommand_idx >= len(planned_commands):
                        # All subcommands for this step are complete, move to next main step
                        session.increment_recipe_step() # This will reset subcommand_idx to 0

            emit("result", {"status": "success", "interpret_message": f"Executed {proxy_instance.name}", "output": result_str})
            return res # Return the original result, not the string, for potential chaining within agent (though unlikely for shell output)
        except Exception as e:
            error_msg = f"Tool execution failed: {e}"
            emit("error", {"message": error_msg})
            session.add_executed_action(proxy_instance.name, action_str, f"ERROR: {error_msg}")
            emit("result", {"status": "failure", "interpret_message": error_msg, "output": ""})
            # Mark deviation if an error occurred during execution (regardless of pre-approval)
            session.set_deviation_occurred(True)
            # Do NOT emit deny_current_action here automatically,
            # as a failure doesn't always mean termination unless user explicitly denies.
            # The agent might try to recover or retry.
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
