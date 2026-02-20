"""
Base Agent Module for SEOCHO Agent-Driven Development

This module provides the foundational classes for creating custom agents.
All agents should inherit from BaseAgent to ensure consistent behavior.

Example:
    >>> from extraction.agent_base.base import BaseAgent
    >>> class MyAgent(BaseAgent):
    ...     def __init__(self):
    ...         super().__init__(
    ...             name="MyAgent",
    ...             instructions="You are a helpful assistant."
    ...         )
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
import os


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    name: str
    instructions: str
    model: str = "gpt-4o"
    tools: List[Callable] = field(default_factory=list)
    handoffs: List['BaseAgent'] = field(default_factory=list)
    max_retries: int = 3
    timeout_seconds: int = 30


class BaseAgent(ABC):
    """
    Abstract base class for all SEOCHO agents.
    
    Provides common functionality for:
    - Tool registration
    - Handoff management
    - Execution tracing
    - Error handling
    
    Attributes:
        name: Human-readable agent identifier
        instructions: System prompt for the agent
        tools: List of callable tools the agent can use
        handoffs: List of agents this agent can delegate to
    """
    
    def __init__(
        self,
        name: str,
        instructions: str,
        tools: Optional[List[Callable]] = None,
        handoffs: Optional[List['BaseAgent']] = None,
        model: str = "gpt-4o"
    ):
        self.name = name
        self.instructions = instructions
        self.tools = tools or []
        self.handoffs = handoffs or []
        self.model = model
        self._trace_enabled = True
        
    def register_tool(self, tool: Callable) -> None:
        """Register a new tool for this agent."""
        if tool not in self.tools:
            self.tools.append(tool)
            
    def register_handoff(self, agent: 'BaseAgent') -> None:
        """Register an agent for handoff delegation."""
        if agent not in self.handoffs:
            self.handoffs.append(agent)
    
    def get_config(self) -> AgentConfig:
        """Return agent configuration as a dataclass."""
        return AgentConfig(
            name=self.name,
            instructions=self.instructions,
            model=self.model,
            tools=self.tools,
            handoffs=self.handoffs
        )
    
    def to_openai_agent(self):
        """
        Convert to OpenAI Agents SDK Agent object.
        
        Returns:
            Agent: OpenAI Agent SDK compatible agent
        """
        from agents import Agent
        return Agent(
            name=self.name,
            instructions=self.instructions,
            tools=self.tools,
            handoffs=[h.to_openai_agent() if isinstance(h, BaseAgent) else h for h in self.handoffs]
        )
    
    @abstractmethod
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """
        Validate input data before processing.
        
        Args:
            input_data: Dictionary of input parameters
            
        Returns:
            bool: True if valid, raises ValueError otherwise
        """
        pass
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', tools={len(self.tools)}, handoffs={len(self.handoffs)})"


class ToolRegistry:
    """
    Central registry for agent tools.
    
    Allows tools to be registered globally and shared across agents.
    """
    
    _instance = None
    _tools: Dict[str, Callable] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._tools = {}
        return cls._instance
    
    @classmethod
    def register(cls, name: str, tool: Callable) -> None:
        """Register a tool by name."""
        cls._tools[name] = tool
        
    @classmethod
    def get(cls, name: str) -> Optional[Callable]:
        """Retrieve a tool by name."""
        return cls._tools.get(name)
    
    @classmethod
    def list_tools(cls) -> List[str]:
        """List all registered tool names."""
        return list(cls._tools.keys())


# Decorator for registering tools
def register_tool(name: Optional[str] = None):
    """
    Decorator to register a function as a global tool.
    
    Args:
        name: Optional name override (defaults to function name)
        
    Example:
        >>> @register_tool("my_search")
        ... def search_database(query: str) -> str:
        ...     return f"Results for {query}"
    """
    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        ToolRegistry.register(tool_name, func)
        return func
    return decorator
