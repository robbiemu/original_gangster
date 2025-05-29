from smolagents import CodeAgent, LiteLLMModel
from smolagents.monitoring import LogLevel as SmolAgentLogLevel
from typing import Dict

from agent.common_tools.tools import get_common_tools
from agent.log_levels import LogLevel


def factory_planner_agent(
    model_id: str, model_params: Dict, python_log_level: LogLevel
) -> CodeAgent:
    planner_model = LiteLLMModel(model_id=model_id, **model_params)

    # Configure smolagents' internal logging
    smolagents_verbosity_level = (
        SmolAgentLogLevel.DEBUG
        if python_log_level == LogLevel.DEBUG
        else SmolAgentLogLevel.OFF
    )

    tools = get_common_tools()

    planner_agent = CodeAgent(
        model=planner_model, tools=tools, verbosity_level=smolagents_verbosity_level
    )

    return planner_agent
