from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field

@dataclass
class AgentConfig:
    name: str
    instructions: str
    model: str = "gpt-4o"
    tools: List[Callable] = field(default_factory=list)
    handoffs: List['BaseAgent'] = field(default_factory=list)

class BaseAgent(ABC):
    def __init__(self, name: str, instructions: str, tools=None, handoffs=None, model="gpt-4o"):
        self.name = name
        self.instructions = instructions
        self.tools = tools or []
        self.handoffs = handoffs or []
        self.model = model

    def to_openai_agent(self):
        from agents import Agent
        return Agent(
            name=self.name,
            instructions=self.instructions,
            tools=self.tools,
            handoffs=[h.to_openai_agent() if isinstance(h, BaseAgent) else h for h in self.handoffs]
        )

    @abstractmethod
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        pass
