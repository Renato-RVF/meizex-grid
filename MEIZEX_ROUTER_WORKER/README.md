# MEIZEX Router Worker (MRW)

A local-first request router and execution worker for LLM agents: it
decides, deterministically wherever possible, what a request actually
needs — a calculation, a tool, a specific model tier, a remote machine —
before ever calling an LLM, and it never lets the model be the only
thing standing between a wrong answer and the user.

Built solo, for real daily use (not a demo), across roughly 20+ working
sessions of documented findings (`Achado N` — see the module docstrings
that cite specific ones). Every design decision below traces to an
observed failure, not a hypothetical.

## Why this exists

Most agent stacks route every request straight to an LLM and hope. MRW
starts from a different premise: **a request should never reach a model
for something a machine can compute exactly.**

```
request
  │
  ▼
CLASSIFY  ── deterministic, no LLM, no embeddings, no network
  │           (arithmetic, date math, unit conversion, text counting,
  │            sequence analysis, boolean logic, code trace, ...)
  ▼
PROFILE → MODEL ROUTE → TOOL ROUTE → CONTEXT BUILD
  │
  ▼
INFER  ── the LLM call, only for what's actually left to reason about
  │
  ▼
TOOL CALLS  ── policy-gated: only read-only, offered tools auto-approve;
  │            everything else needs explicit human approval
  ▼
VERIFY  ── deterministic gate, no second inference: catches empty
  │         answers, raw tool dumps passed off as answers, claims of
  │         evidence no tool actually produced, truncation
  ▼
result (or an explicit RETRY / escalation — never a silent loop)
```

`request_policy/classifier.py`'s precedence list exists because a real
stress test caught a local model answering "quantas letras tem X" wrong
— a question with exactly one mechanically correct answer that has no
business going through an LLM at all. Each classification category in
that file was added the same way: a real wrong answer, then a
deterministic fix that makes the whole category of mistake structurally
impossible, not just less likely.

## The Capability Router: routing across machines, not just models

Newer than the classifier: `capabilities/router.py` extends routing from
"which model" to "which **resource**" — local, Grid (a private SSH mesh
across the author's own machines, see
[`MEIZEX_GRID`](../MEIZEX_GRID/README.md)), or cloud, ranked and filtered
with an auditable reason for every exclusion (incompatible capability,
policy, cloud forbidden, provider unavailable). `ExecutionProfile.
grid_allowed` makes Grid participation an explicit opt-in per profile,
not a silent default — a resource never gets used just because it
exists.

## STATEWATCH: catching failure before the kill, not after

The newest piece. Inspired by
[VoLo](https://arxiv.org/abs/2606.07723) (NVIDIA/Michigan, CoRL 2026) —
a robotics paper about a "physical orchestrator" that continuously
compares a robot's *expected* state against its *observed* state and
halts/recovers on divergence, instead of waiting for an outright
failure.

`statewatch/residual.py` applies the same pattern to a remote job
handle: an executor's own background monitor computes what phase a job
*should* be in (from its declared `timeout_s`) versus what phase it
*actually* reports, and flags a divergence once real elapsed time
crosses a configurable warning threshold — **before** the process
boundary's real kill, not at the same instant. `RemoteSSHExecutor.
execute()` runs this for real: a `StepDivergence` event lands in the
append-only event log with genuine lead time, giving a monitor (human or
automated) an actual window to notice trouble instead of learning about
it exactly when it's too late to matter.

## Escalation without contamination

`escalation.py` encodes a specific, citable finding (the 2026-09-05
ecosystem sweep on InflationAgent): handing a stronger model the failed
*reasoning chain* of a weaker one can make its answer **worse**, not
better. MRW's fresh-escalation path sends a stronger model only the
original request, the weak model's answer, the objective mechanical
reason it's doubted, and real tool evidence — never the chain of
thought that produced the wrong answer in the first place.

## Everything is observed

Every step — classification, profile selection, model route, tool
calls, verification, escalation, Grid dispatch, STATEWATCH divergence —
emits a typed, append-only event (`events/models.py`). Nothing is
inferred after the fact from logs; the event stream *is* the record.

## Run it

```bash
pip install -e .
mrw run "what's 17% of 340?"          # routes to ARITHMETIC, no LLM call
mrw route "summarize this file"        # shows the routing decision only
mrw status                             # provider health + profiles
mrw tools                              # real tools discovered via MCP
```

Machine-first contract: stdout carries the result (`--json` for a single
document), stderr carries diagnostics, exit codes are explicit
(0 completed, 1 failed, 2 invalid input, 3 provider unavailable,
4 policy blocked).

## Relationship to the rest of MEIZEX

- [`MEIZEX_GRID`](../MEIZEX_GRID/README.md) is the transport MRW's
  Capability Router can opt into — see the Grid repo's own manual for
  the full glossary and the "is MRW just a CLI?" question answered
  directly.
- [`meizex-a2a`](https://github.com/Renato-RVF/meizex-a2a) is a separate,
  much smaller project: agent-to-agent discovery and task exchange, not
  routing within one agent.

## License

MIT — see [LICENSE](../LICENSE).
