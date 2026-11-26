from __future__ import annotations

from dataclasses import dataclass, field

from ..llm import LLMResult


@dataclass
class AgentResult:
    agent: str
    reply: str
    citations: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)       # trace entries: {"type": "tool"|"retrieval"|..., ...}
    llm_calls: list[LLMResult] = field(default_factory=list)
    confidence: float = 0.8
    handoffs: list[dict] = field(default_factory=list)    # escalation requests raised by this agent
