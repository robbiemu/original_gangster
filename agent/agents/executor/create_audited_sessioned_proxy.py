import json
import sys
from pathlib import Path
import re
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
    output_threshold_bytes: int,
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
            or (str(args[0]) if args else "")
            or "an unknown action"
        )

    def _around_hook(
        proxy_instance: ProxyTool, proceed_callable: Callable, *args, **kwargs
    ) -> Any:
        """
        [Around Cut] - This hook encapsulates all the logic for auditing, user approval, execution.
        """
        action_str = _get_action_string(*args, **kwargs)
        context = session.get_execution_context()

        # 1. Always perform a security audit using the Auditor Agent
        audit_res = audit_request(auditor, action_str, context)

        if audit_res.get("log_message"):
            emit(
                "warn_log",
                {
                    "message": audit_res["log_message"],
                    "location": "executor/create_audited_sessioned_proxy._around_hook",
                },
            )

        if not audit_res.get("safe", False):
            if not session.deviation_occurred:
                session.set_deviation_occurred(True)
            emit(
                "unsafe",
                {
                    "reason": audit_res.get(
                        "reason", "Action deemed unsafe by auditor"
                    ),
                    "explanation": audit_res.get("explanation", context or action_str),
                },
            )
            emit(
                "deny_current_action",
                {"message": "Action was deemed unsafe by auditor."},
            )
            return None

        # 2. Determine if user approval is required for this specific action
        should_request_approval = True
        is_current_action_expected_by_recipe = False

        expected_step = session.get_expected_recipe_step()

        if expected_step:
            if proxy_instance.name == expected_step.get("tool", ""):
                expected_subcommand = session.get_expected_subcommand()

                if (
                    expected_subcommand is not None
                    and action_str.strip() == expected_subcommand
                ):
                    is_current_action_expected_by_recipe = True

                    if (
                        session.recipe_preapproved and not session.deviation_occurred
                    ) or (
                        session.is_single_step_plan
                        and session.next_expected_recipe_step_idx == 0
                        and session.next_expected_subcommand_idx == 0
                    ):
                        emit(
                            "info_log",
                            {
                                "message": f"Auto-approving expected recipe step {session.next_expected_recipe_step_idx + 1}, subcommand {session.next_expected_subcommand_idx + 1}: '{action_str}' ({proxy_instance.name})",
                                "location": "executor/create_audited_sessioned_proxy._around_hook",
                            },
                        )
                        should_request_approval = False
                    else:
                        emit(
                            "warn_log",
                            {
                                "message": f"Auto-approval condition not met for '{action_str}'. Requesting individual approval.",
                                "location": "executor/create_audited_sessioned_proxy._around_hook",
                            },
                        )
                        session.set_deviation_occurred(True)
                        should_request_approval = True

                else:
                    emit(
                        "warn_log",
                        {
                            "message": f"Deviation detected! Planned step {session.next_expected_recipe_step_idx + 1} expected '{expected_subcommand}', got '{action_str}'. Requesting approval.",
                            "location": "executor/create_audited_sessioned_proxy._around_hook",
                        },
                    )
                    session.set_deviation_occurred(True)
                    should_request_approval = True
            else:
                emit(
                    "warn_log",
                    {
                        "message": f"Deviation detected! Expected tool '{expected_step.get('tool')}', got '{proxy_instance.name}'. Requesting approval.",
                        "location": "executor/create_audited_sessioned_proxy._around_hook",
                    },
                )
                session.set_deviation_occurred(True)
                should_request_approval = True
        else:
            if session.recipe_preapproved and not session.deviation_occurred:
                emit(
                    "warn_log",
                    {
                        "message": f"Agent attempting action '{action_str}' ({proxy_instance.name}) beyond pre-approved recipe. Requesting approval.",
                        "location": "executor/create_audited_sessioned_proxy._around_hook",
                    },
                )
                session.set_deviation_occurred(True)
            should_request_approval = True

        # --- If approval is still required, interact with user ---
        if should_request_approval:
            desc = f"{proxy_instance.name} -> {action_str}"
            session.add_to_history("assistant", desc)
            emit(
                "request_approval",
                {
                    "description": desc,
                    "action": action_str,
                    "tool": proxy_instance.name,
                },
            )

            resp = {}
            try:
                resp_line = sys.stdin.readline()
                if not resp_line:
                    emit(
                        "error",
                        {
                            "message": "Received EOF or empty line from stdin during approval. Go client might have terminated unexpectedly.",
                            "location": "executor/create_audited_sessioned_proxy._around_hook",
                        },
                    )
                    emit(
                        "deny_current_action",
                        {"message": "Go client communication failed."},
                    )
                    return None
                resp = json.loads(resp_line)
            except json.JSONDecodeError:
                emit(
                    "error",
                    {
                        "message": f"Failed to parse approval response from stdin: '{resp_line.strip()}'",
                        "location": "executor/create_audited_sessioned_proxy._around_hook",
                    },
                )
                emit(
                    "deny_current_action",
                    {"message": "Invalid approval response received."},
                )
                return None
            except Exception as e:
                emit(
                    "error",
                    {
                        "message": f"Failed to read approval response: {e}",
                        "location": "executor/create_audited_sessioned_proxy._around_hook",
                    },
                )
                emit("deny_current_action", {"message": f"Error reading approval: {e}"})
                return None

            if not resp.get("approved", False):
                emit(
                    "result",
                    {
                        "status": "cancelled",
                        "interpret_message": "User denied execution",
                    },
                )
                emit(
                    "deny_current_action",
                    {"message": "User explicitly denied the action."},
                )
                return None

        # 3. Execute Underlying Tool and Handle Outcome (only if approved or auto-approved)
        try:
            res = proceed_callable(*args, **kwargs)

            interpret_message = f"Executed {proxy_instance.name}"
            status = "success"

            if proxy_instance.name == "shell_tool" and isinstance(res, str):
                stdout_match = re.search(
                    r"--- STDOUT ---\n(.*?)(?=\n--- STDERR ---|\n--- Command exited|\Z)",
                    res,
                    re.DOTALL,
                )
                stderr_match = re.search(
                    r"--- STDERR ---\n(.*?)(?=\n--- Command exited|\Z)", res, re.DOTALL
                )
                exit_code_match = re.search(
                    r"--- Command exited with status: (\d+) ---", res
                )

                stdout_content = stdout_match.group(1).strip() if stdout_match else None
                stderr_content = stderr_match.group(1).strip() if stderr_match else None
                exit_code = int(exit_code_match.group(1)) if exit_code_match else 0

                if stdout_content and stderr_content:
                    interpret_message = (
                        f"Executed {proxy_instance.name} with stdout and stderr"
                    )
                elif stdout_content:
                    interpret_message = f"Executed {proxy_instance.name} with stdout"
                elif stderr_content:
                    interpret_message = f"Executed {proxy_instance.name} with stderr"
                else:
                    interpret_message = f"Executed {proxy_instance.name}"

                if exit_code != 0:
                    status = "failure"
                    interpret_message += f" (Exit code: {exit_code})"

                if res.strip() == "[Command executed with no output]":
                    interpret_message += " (no output)"
                    status = "success"

            result_str = str(res) if res is not None else "completed"

            if (
                isinstance(res, str)
                and res.strip()
                and res.strip() != "[Command executed with no output]"
            ):
                output_bytes = res.encode("utf-8")
                if (
                    output_threshold_bytes > 0
                    and len(output_bytes) > output_threshold_bytes
                ):
                    temp_dir_path = Path("/tmp") / "og" / session.session_hash
                    temp_dir_path.mkdir(parents=True, exist_ok=True)

                    turn_index = len(session.executed_actions)
                    file_name = (
                        f"{turn_index + 1}_{proxy_instance.name.replace(' ', '_')}.txt"
                    )
                    temp_file_path = temp_dir_path / file_name

                    try:
                        temp_file_path.write_bytes(output_bytes)
                        result_str = (
                            f"-- out saved to {temp_file_path} because at "
                            f"{(len(output_bytes) / 1024):.2f} KB, it is too long to include. "
                            f"Use tools (for example perhaps `grep` or `cat {temp_file_path}`) to find the details that you require --"
                        )
                        emit(
                            "info_log",
                            {
                                "message": f"Tool output saved to temporary file: {temp_file_path}",
                                "location": "executor/create_audited_sessioned_proxy._around_hook",
                            },
                        )
                    except Exception as file_e:
                        emit(
                            "error",
                            {
                                "message": f"Failed to save large tool output to {temp_file_path}: {file_e}. Returning full output.",
                                "location": "executor/create_audited_sessioned_proxy._around_hook",
                            },
                        )
                        result_str = str(res)

            session.add_executed_action(proxy_instance.name, action_str, result_str)

            if is_current_action_expected_by_recipe:
                session.increment_subcommand_idx()

                expected_step_after_increment = session.get_expected_recipe_step()
                if expected_step_after_increment:
                    planned_commands = (
                        expected_step_after_increment.get("action", "")
                        .strip()
                        .split("\n")
                    )
                    if session.next_expected_subcommand_idx >= len(planned_commands):
                        session.increment_recipe_step()

            emit(
                "result",
                {
                    "status": status,
                    "interpret_message": interpret_message,
                    "output": result_str,
                },
            )
            return res

        except Exception as e:
            error_msg = f"Tool execution failed: {type(e).__name__}: {e}"
            emit(
                "error",
                {
                    "message": error_msg,
                    "location": "executor/create_audited_sessioned_proxy._around_hook",
                },
            )
            session.add_executed_action(
                proxy_instance.name, action_str, f"ERROR: {error_msg}"
            )
            emit(
                "result",
                {"status": "failure", "interpret_message": error_msg, "output": ""},
            )
            session.set_deviation_occurred(True)
            return None

    underlying_description = getattr(tool, "description", None)
    if not underlying_description:
        doc = getattr(tool, "__doc__", "")
        underlying_description = (
            doc.strip().split("\n")[0] if doc else "an unspecified action"
        )

    proxy_description = f"Ask user approval for: {underlying_description}"

    return ProxyTool(
        name=name,
        underlying=tool,
        description=proxy_description,
        around_hook=_around_hook,
    )
