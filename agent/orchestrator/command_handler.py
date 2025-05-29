import sys
from typing import Dict

from agent.emitter import emit
from agent.log_levels import LogLevel
from agent.prompts import (
    prepare_fallback_continuation_query,
    prepare_recipe_continuation_query,
)
from agent.session import AgentSession


class CommandHandler:
    """Handles incoming commands from Go client."""

    def __init__(
        self, executor_agent, session: AgentSession, python_log_level: LogLevel
    ):
        self.executor_agent = executor_agent
        self.session = session
        self.python_log_level = python_log_level

    def handle_command(self, command: Dict) -> bool:
        """Handle a single command. Returns True if should continue, False if should exit."""
        cmd_type = command.get("type")

        emit(
            "debug_log",
            {
                "message": f"Received command: {cmd_type}",
                "location": "orchestrator/command_handler.handle_command",
            },
        )

        handlers = {
            "execute_recipe": self._handle_execute_recipe,
            "execute_single_action": self._handle_execute_single_action,
            "execute_fallback": self._handle_execute_fallback,
            "user_approval_response": self._handle_user_approval,
            "deny_current_action": self._handle_deny_current_action,
        }

        handler = handlers.get(cmd_type)
        if handler:
            return handler(command)
        else:
            emit(
                "error",
                {
                    "message": f"Python agent received unhandled command type: {cmd_type}",
                    "location": "orchestrator/command_handler.handle_command",
                },
            )
            return False

    def _handle_execute_recipe(self, command: Dict) -> bool:
        """Handle execute_recipe command: user approved multi-step recipe."""
        self.session.set_single_step_plan_status(False)
        self.session.set_recipe_preapproved(True)
        self.session.increment_recipe_step()
        self.session.set_deviation_occurred(False)

        emit(
            "info_log",
            {
                "message": f"User approved recipe. Continuing session '{self.session.session_hash}' to execute pre-approved recipe steps.",
                "location": "orchestrator/command_handler._handle_execute_recipe",
            },
        )

        continuation_query = prepare_recipe_continuation_query(self.session)
        self._execute_and_emit_finale(continuation_query, "recipe execution")
        return False

    def _handle_execute_single_action(self, command: Dict) -> bool:
        """Handle execute_single_action command: Go frontend decided to auto-proceed to individual step approval."""
        self.session.set_single_step_plan_status(True)
        self.session.set_recipe_preapproved(False)
        self.session.increment_recipe_step()
        self.session.set_deviation_occurred(False)

        emit(
            "info_log",
            {
                "message": f"Proceeding with single action. Session '{self.session.session_hash}' will request individual approval for the first action.",
                "location": "orchestrator/command_handler._handle_execute_single_action",
            },
        )

        continuation_query = prepare_recipe_continuation_query(self.session)
        self._execute_and_emit_finale(continuation_query, "single action execution")
        return False

    def _handle_execute_fallback(self, command: Dict) -> bool:
        """Handle execute_fallback command."""
        self.session.set_single_step_plan_status(False)
        self.session.set_recipe_preapproved(False)
        self.session.increment_recipe_step()
        self.session.set_deviation_occurred(True)
        emit(
            "info_log",
            {
                "message": f"Continuing session '{self.session.session_hash}' by executing fallback.",
                "location": "orchestrator/command_handler._handle_execute_fallback",
            },
        )
        continuation_query = prepare_fallback_continuation_query(self.session)
        self._execute_and_emit_finale(continuation_query, "fallback continuation")
        return False

    def _handle_user_approval(self, command: Dict) -> bool:
        """Handle user_approval_response command: This is consumed by the ProxyTool."""
        emit(
            "debug_log",
            {
                "message": f"Received user_approval_response. This response is usually handled by ProxyTool: {command.get('approved')}",
                "location": "orchestrator/command_handler._handle_user_approval",
            },
        )
        return True

    def _handle_deny_current_action(self, command: Dict) -> bool:
        """Handle denial of an individual action during execution."""
        emit(
            "info_log",
            {
                "message": f"User denied execution of current action. Providing summary and ending session.",
                "location": "orchestrator/command_handler._handle_deny_current_action",
            },
        )
        self._emit_final_summary_on_denial("User denied the proposed action.")
        return False

    def _emit_final_summary_on_denial(self, reason: str) -> None:
        """Helper to emit a final summary upon explicit denial."""
        summary = f"Session terminated by user denial. {reason}"
        nutshell = f"Session cancelled: {reason}"
        if self.session.executed_actions:
            nutshell += f" Last action: {self.session.executed_actions[-1]['action']}"
        emit(
            "final_summary",
            {
                "summary": summary,
                "nutshell": nutshell,
                "reason": reason,
                "status": "cancelled",
            },
        )

    def _execute_and_emit_finale(
        self, continuation_query: str, execution_type: str
    ) -> None:
        """Execute query and emit final summary when the agent finishes."""
        try:
            finale = self.executor_agent.run(continuation_query)
            lines = finale.splitlines() if finale else []
            emit(
                "final_summary",
                {
                    "summary": finale,
                    "nutshell": lines[0] if len(lines) > 1 else "",
                    "status": "success",
                },
            )
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            emit(
                "error",
                {
                    "message": f"Agent execution failed during {execution_type}: {e}",
                    "location": "orchestrator/command_handler._execute_and_emit_finale",
                },
            )
            if self.python_log_level <= LogLevel.WARN:
                emit(
                    "warn_log",
                    {
                        "message": f"Full stack trace:\n{tb}",
                        "location": "orchestrator/command_handler._execute_and_emit_finale",
                    },
                )
            self._emit_final_summary_on_denial(
                f"Agent experienced an unrecoverable error: {e}"
            )
            sys.exit(1)
