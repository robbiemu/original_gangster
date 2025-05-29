from typing import Dict
from smolagents import LiteLLMModel, ToolCallingAgent, CodeAgent
from smolagents.monitoring import LogLevel as SmolAgentLogLevel

from agent.common_tools.tools import get_common_tools
from agent.emitter import emit
from agent.log_levels import LogLevel
from agent.session import AgentSession
from .create_audited_sessioned_proxy import create_audited_sessioned_proxy
from .tools import shell_tool, file_content_tool


def factory_executor_agent(
    model_id: str,
    model_params: Dict,
    session: AgentSession,
    auditor: ToolCallingAgent,
    output_threshold_bytes: int,
    summary_mode: bool,
    python_log_level: LogLevel,
) -> CodeAgent:
    main_model = LiteLLMModel(model_id=model_id, **model_params)

    # Configure smolagents' internal logging and summary generation
    smolagents_verbosity_level = (
        SmolAgentLogLevel.DEBUG
        if python_log_level == LogLevel.DEBUG
        else SmolAgentLogLevel.OFF
    )

    tools = [
        create_audited_sessioned_proxy(
            name="shell_tool",
            tool=shell_tool,
            session=session,
            auditor=auditor,
            emit=emit,
            output_threshold_bytes=output_threshold_bytes,
        ),
        create_audited_sessioned_proxy(
            name="file_content_tool",
            tool=file_content_tool,
            session=session,
            auditor=auditor,
            emit=emit,
            output_threshold_bytes=output_threshold_bytes,
        ),
    ]
    tools += get_common_tools()

    agent = CodeAgent(
        model=main_model,
        tools=tools,
        verbosity_level=smolagents_verbosity_level,
        provide_run_summary=summary_mode,  # Controls final summary generation
    )

    return agent
