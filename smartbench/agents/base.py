"""
Base Agent

Base class for all SmartBench agents.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum


class AgentStatus(Enum):
    """Agent execution status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class AgentResult:
    """
    Result returned by an agent execution.
    """
    agent_name: str
    status: AgentStatus
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_success(self) -> bool:
        return self.status == AgentStatus.SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "status": self.status.value,
            "data": self.data,
            "error": self.error,
            "duration": self.duration,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class BaseAgent(ABC):
    """
    Abstract base class for all SmartBench agents.

    Each agent has:
    - name: Unique identifier
    - description: What the agent does
    - execute(): Main execution method
    - validate(): Input validation
    """

    def __init__(
        self,
        name: str,
        description: str,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.description = description
        self.config = config or {}

    @abstractmethod
    def execute(self, context: Dict[str, Any]) -> AgentResult:
        """
        Execute the agent with given context.

        Args:
            context: Execution context containing inputs and previous results

        Returns:
            AgentResult: Execution result
        """
        pass

    def validate(self, context: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate input context.

        Args:
            context: Input context to validate

        Returns:
            (is_valid, error_message)
        """
        return True, None

    def get_schema(self) -> Dict[str, Any]:
        """
        Get input/output schema for this agent.

        Returns:
            JSON schema dictionary
        """
        return {
            "name": self.name,
            "description": self.description,
            "config": self.config,
        }


@dataclass
class AgentMessage:
    """
    Message passed between agents.
    """
    sender: str
    receiver: str
    content: Dict[str, Any]
    message_type: str = "request"
    timestamp: datetime = field(default_factory=datetime.now)
    correlation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "content": self.content,
            "type": self.message_type,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
        }


@dataclass
class AgentPipeline:
    """
    Pipeline of agents to execute in sequence or parallel.
    """
    name: str
    agents: List[BaseAgent]
    mode: str = "sequential"  # sequential or parallel
    timeout: int = 600  # seconds

    def execute(self, context: Dict[str, Any]) -> List[AgentResult]:
        """
        Execute the pipeline.

        Args:
            context: Initial context

        Returns:
            List of AgentResult from each agent
        """
        results = []
        current_context = context.copy()

        for agent in self.agents:
            result = agent.execute(current_context)
            results.append(result)

            if result.is_success():
                current_context[f"{agent.name}_result"] = result.data
            else:
                if self.mode == "sequential":
                    break

        return results