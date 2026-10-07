"""Operator runtime: the phase state machine, task contracts and checkpoints.

Phases: Goal -> Understand -> Plan -> Execute -> Observe -> Adapt -> Verify -> Complete.
"""

from company_operator.runtime.brain import Brain, BrainContext, NextAction, UnderstandDecision, Verifier
from company_operator.runtime.engine import Operator
from company_operator.runtime.models import (
    Criterion,
    CriterionResult,
    Plan,
    PlanStep,
    RunState,
    TaskContract,
    TaskRequest,
    ToolCall,
    Verification,
)
from company_operator.runtime.store import RunStore

__all__ = [
    "Brain",
    "BrainContext",
    "Criterion",
    "CriterionResult",
    "NextAction",
    "Operator",
    "Plan",
    "PlanStep",
    "RunState",
    "RunStore",
    "TaskContract",
    "TaskRequest",
    "ToolCall",
    "UnderstandDecision",
    "Verification",
    "Verifier",
]
