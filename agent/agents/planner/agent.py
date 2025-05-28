from smolagents import CodeAgent, LiteLLMModel
from typing import Dict

from agent.common_tools.tools import get_common_tools
#from agent.memory_managed_code_agent import MemoryManagedCodeAgent


def factory_planner_agent(model_id: str, model_params: Dict) -> CodeAgent:
    planner_model = LiteLLMModel(model_id=model_id, **model_params)

    tools = get_common_tools()  

    planner_agent = CodeAgent(model=planner_model, tools=tools)

    return planner_agent
