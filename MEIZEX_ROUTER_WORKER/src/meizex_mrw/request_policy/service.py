"""Public seam. Callers (CLI, router, worker) should call only evaluate() --
mirrors MOL's "GUI != POLICY" seam (meizex_orchestrator_lite/request_policy/
service.py), here it is simply CALLER != POLICY."""

from __future__ import annotations

from meizex_mrw.request_policy.models import RequestPolicyDecision
from meizex_mrw.request_policy.policy import decide_policy


def evaluate(text: str) -> RequestPolicyDecision:
    return decide_policy(text)
