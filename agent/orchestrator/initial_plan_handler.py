import sys
from typing import Dict, List, Optional, Tuple

from agent.agents.auditor.agent import audit_request
from agent.emitter import emit
from agent.log_levels import LogLevel
from agent.prompts import prepare_planning_prompt
from agent.session import AgentSession
from .plan_parser import parse_plan


class InitialPlanHandler:
    """Handles creation and auditing of initial plans."""

    def __init__(
        self,
        planner_agent,
        auditor_agent,
        session: AgentSession,
        python_log_level: LogLevel,
    ):
        self.planner_agent = planner_agent
        self.auditor_agent = auditor_agent
        self.session = session
        self.python_log_level = python_log_level

    def create_and_audit_plan(self, query: str) -> None:
        """Create initial plan and perform safety audit."""
        try:
            plan_str = self._generate_plan(query)
            recipe_steps, fallback_action = self._parse_plan(plan_str)
            self._validate_plan(recipe_steps, fallback_action, query)
            self._audit_initial_action(recipe_steps, fallback_action)
            self._store_and_emit_plan(recipe_steps, fallback_action, query)

        except Exception as e:
            self._handle_planning_error(e)

    def _generate_plan(self, query: str) -> str:
        """Generate plan using PlannerAgent."""
        planning_prompt = prepare_planning_prompt(query)
        plan_text_output = self.planner_agent.run(planning_prompt)

        if hasattr(plan_text_output, "content"):
            plan_str = plan_text_output.content
        else:
            plan_str = str(plan_text_output)

        emit(
            "debug_log",
            {
                "message": f"Raw plan output from PlannerAgent:\n---\n{plan_str}\n---",
                "location": "orchestrator/initial_plan_handler._generate_plan",
            },
        )

        return plan_str

    def _parse_plan(self, plan_str: str) -> Tuple[List[Dict], Optional[Dict]]:
        """Parse plan string into structured data."""
        return parse_plan(plan_str)

    def _get_first_action(
        self, recipe_steps: List[Dict], fallback_action: Optional[Dict]
    ) -> Tuple[str, str]:
        """Get the first action that would be executed."""
        if recipe_steps:
            first_step = recipe_steps[0]
            return first_step.get("action", ""), first_step.get(
                "description", "First step of recipe"
            )
        elif fallback_action:
            return fallback_action.get("action", ""), fallback_action.get(
                "description", "Fallback action"
            )
        else:
            return "", "No action available"

    def _validate_plan(
        self, recipe_steps: List[Dict], fallback_action: Optional[Dict], query: str
    ) -> None:
        """Validate that we have a workable plan."""
        if not recipe_steps and not fallback_action:
            emit(
                "error",
                {
                    "message": "Agent failed to generate a plan or fallback for initial audit.",
                    "location": "orchestrator/initial_plan_handler._validate_plan",
                },
            )
            emit(
                "unsafe",
                {
                    "reason": "Agent could not form a clear initial plan.",
                    "explanation": query,
                },
            )
            sys.exit(1)

    def _audit_initial_action(
        self, recipe_steps: List[Dict], fallback_action: Optional[Dict]
    ) -> None:
        """Audit the first action that would be taken."""
        action_to_audit, action_description = self._get_first_action(
            recipe_steps, fallback_action
        )

        emit(
            "debug_log",
            {
                "message": f"Initial action to audit: '{action_to_audit}'",
                "location": "orchestrator/initial_plan_handler._audit_initial_action",
            },
        )

        audit_result = audit_request(
            self.auditor_agent, action_to_audit, self.session.get_execution_context()
        )

        if audit_result.get("log_message"):
            emit(
                "warn_log",
                {
                    "message": audit_result["log_message"],
                    "location": "orchestrator/initial_plan_handler._audit_initial_action",
                },
            )

        if not audit_result.get("safe", False):
            emit(
                "unsafe",
                {
                    "reason": audit_result.get("reason", "Initial plan deemed unsafe"),
                    "explanation": audit_result.get(
                        "explanation",
                        f"Initial action proposed: '{action_description}' was found unsafe.",
                    ),
                },
            )
            sys.exit(0)

    def _store_and_emit_plan(
        self, recipe_steps: List[Dict], fallback_action: Optional[Dict], query: str
    ) -> None:
        """Store plan in session and emit to Go client."""
        self.session.set_plan(recipe_steps, fallback_action)
        self.session.set_original_query(query)

        emit(
            "plan",
            {
                "request": query,
                "recipe_steps": self._format_steps_for_go(recipe_steps),
                "fallback_action": self._format_fallback_for_go(fallback_action),
            },
        )

    def _format_steps_for_go(self, recipe_steps: List[Dict]) -> List[Dict]:
        """Format recipe steps for Go client."""
        return [
            {
                "description": step.get("description", ""),
                "expected_outcome": step.get("expected_outcome", ""),
                "action": step.get("action", ""),
                "tool": step.get("tool", ""),
            }
            for step in recipe_steps
        ]

    def _format_fallback_for_go(
        self, fallback_action: Optional[Dict]
    ) -> Optional[Dict]:
        """Format fallback action for Go client."""
        if not fallback_action:
            return None

        return {
            "description": fallback_action.get("description", "Fallback"),
            "expected_outcome": fallback_action.get("expected_outcome", ""),
            "action": fallback_action.get("action", ""),
            "tool": fallback_action.get("tool", ""),
        }

    def _handle_planning_error(self, error: Exception) -> None:
        """Handle planning errors."""
        import traceback

        tb = traceback.format_exc()
        emit(
            "error",
            {
                "message": f"Agent planning or initial audit failed: {error}",
                "location": "orchestrator/initial_plan_handler._handle_planning_error",
            },
        )
        if self.python_log_level <= LogLevel.WARN:
            emit(
                "warn_log",
                {
                    "message": f"Full stack trace:\n{tb}",
                    "location": "orchestrator/initial_plan_handler._handle_planning_error",
                },
            )
        sys.exit(1)
