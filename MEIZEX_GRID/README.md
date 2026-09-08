# MEIZEX Grid

A small, real, working distributed execution layer: three home-network
Windows machines, reachable over SSH, dispatching real work from the
[MEIZEX Router Worker](../MEIZEX_ROUTER_WORKER/README.md)'s Capability
Router — asynchronously, cancellably, concurrency-limited per node, and
now monitored for early failure signs (see STATEWATCH in the MRW README).

Not a framework, not a product — infrastructure built solo to actually
use, documented as it was built.

## What's real today

- **Real SSH dispatch.** `RemoteSSHExecutor` runs a capability on a
  named Grid node over SSH, same JSON-on-stdin/stdout transport as
  local execution — only the spawned command differs.
- **Async, not just blocking.** `execute_async()` hands back a
  `RemoteJobHandle` immediately: `done()`/`poll()` (non-blocking),
  `wait(timeout)` (blocking), `cancel()` (kills the in-flight ssh
  process for real).
- **Per-node concurrency limits.** Each `RemoteSSHExecutor` gates how
  many jobs actually run against its node at once; extras queue without
  ever blocking the caller.
- **The router chooses the Grid on its own — when told it's allowed
  to.** `ExecutionProfile.grid_allowed` is an explicit opt-in; nothing
  gets dispatched to a remote node just because it exists.
- **Early failure warning, not just eventual timeout.** STATEWATCH
  compares a job's expected phase against its observed phase and flags
  divergence before the real kill — see the MRW README for the full
  story.

All of it verified against real machines on the author's own network,
not mocked — see [`NEXT.md`](NEXT.md) for the dated, evidence-linked
record of each milestone (SSH pilot setup, async dispatch, cancellation,
concurrency limits, auto-selection, the Windows-specific operational
gotchas that came with running sshd as a service).

## How the pieces fit

```
MRW Capability Router
   │  (ExecutionProfile.grid_allowed = true)
   ▼
RemoteSSHExecutor  ──ssh──▶  a Grid node
   │
   ├─ execute_async() → RemoteJobHandle (poll / wait / cancel)
   └─ STATEWATCH polls the handle's residual while waiting,
      emits StepDivergence with real lead time before any kill
```

## Reference manual

[`manual_grid_mrw.html`](manual_grid_mrw.html) — glossary, architecture,
and direct answers to the four questions that came up building this:
is the Grid autonomous or an MRW-only feature, could MRW be a
general-purpose CLI, could the Grid be independent, and whether an
A2A-style exchange space between orchestrators would make sense (it
led to [`meizex-a2a`](https://github.com/Renato-RVF/meizex-a2a)).

## Decision record

This project follows a DOT/NEXT/LAB discipline: [`DOT.md`](DOT.md) is
accepted architectural decisions, [`NEXT.md`](NEXT.md) is authorized
work and its real results, [`LAB.md`](LAB.md) is open hypotheses. They
are the actual history — narrower summaries above should defer to them.

## License

MIT — see [LICENSE](../LICENSE).
