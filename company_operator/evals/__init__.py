"""Reliability and evals: run the operator on known scenarios, under injected faults, and score it.

Scoring never asks the model whether it did well. It reads the sandbox's own
database before and after each run (the ground truth) and checks what actually
changed against what QuickBite's policy says should happen.
"""

from company_operator.evals.cases import CASES, EvalCase
from company_operator.evals.profiles import PROFILES, SUITES, FaultProfile

__all__ = ["CASES", "PROFILES", "SUITES", "EvalCase", "FaultProfile"]
