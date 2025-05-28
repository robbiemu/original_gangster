from typing import Dict
from smolagents import LiteLLMModel, ToolCallingAgent, CodeAgent

from agent.common_tools.tools import get_common_tools
from agent.emitter import emit
from agent.session import AgentSession
from .create_audited_sessioned_proxy import create_audited_sessioned_proxy
from .tools import shell_tool, file_content_tool


def factory_executor_agent(model_id: str, model_params: Dict, session: AgentSession, auditor: ToolCallingAgent) -> CodeAgent:
    main_model = LiteLLMModel(model_id=model_id, **model_params)
    tools = [
        create_audited_sessioned_proxy(
            name="shell_tool", 
            tool=shell_tool, 
            session=session, 
            auditor=auditor, 
            emit=emit
        ),
        create_audited_sessioned_proxy(
            name="file_content_tool", 
            tool=file_content_tool, 
            session=session, 
            auditor=auditor, 
            emit=emit
        ),
    ]
    tools += get_common_tools()

    agent = CodeAgent(model=main_model, tools=tools)

    return agent
