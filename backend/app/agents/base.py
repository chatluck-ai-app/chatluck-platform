"""Agent contracts. Every specialized agent declares its tools, permissions
and runs against an AgentContext; the master agent plans and dispatches."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..providers.router import ProviderRouter


@dataclass
class AgentContext:
    user_id: str
    router: ProviderRouter
    approved: bool = False  # user approved sensitive actions for this run
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    ok: bool
    output: str
    data: dict[str, Any] = field(default_factory=dict)
    needs_approval: bool = False


ToolFn = Callable[[AgentContext, str], Awaitable[AgentResult]]


class AgentSpec(abc.ABC):
    name: str = "agent"
    description: str = ""
    tools: list[str] = []
    sensitive: bool = False  # True -> requires ctx.approved

    @abc.abstractmethod
    async def run(self, ctx: AgentContext, instruction: str) -> AgentResult:
        ...
