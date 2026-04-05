from typing import Any
from agents import Agent

def create_supervisor_agent() -> Any:
    return Agent(
        name="Supervisor",
        instructions="You are the Supervisor. Your goal is to collect the results from the active agents, summarize them, and present the final answer to the user. Do not call any tools. Just synthesize and complete."
    )
