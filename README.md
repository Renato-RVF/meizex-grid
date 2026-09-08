# MEIZEX Grid

A small, self-hosted distributed AI infrastructure, built and run solo:
three home-network Windows machines dispatching real work over SSH,
routed there by a local-first AI request router that decides
deterministically, wherever possible, before ever calling an LLM.

Three parts, each real and independently documented:

- **[`MEIZEX_ROUTER_WORKER`](MEIZEX_ROUTER_WORKER/README.md)** — the
  router and execution worker (MRW). Deterministic classification
  before any model call, a capability router that picks local/Grid/
  cloud resources with an auditable reason for every exclusion, and
  STATEWATCH: an early-warning signal for a job going wrong, inspired
  by a CoRL 2026 robotics paper, applied to a remote job handle.
- **[`MEIZEX_GRID`](MEIZEX_GRID/README.md)** — the distributed
  execution layer itself: real async SSH dispatch, cancellation, and
  per-node concurrency limits across three real machines. Includes a
  reference manual (glossary + architecture + the four questions that
  shaped it) and a dated decision record (`DOT.md`/`NEXT.md`/`LAB.md`).
- **[`MEIZEX_AIR`](MEIZEX_AIR/README.md)** — the adaptive runtime that
  decides whether a task should go deterministic or through a model,
  based on real machine capability and tool availability, not a guess.

A related, separate project — smaller, and about a different problem —
is [`meizex-a2a`](https://github.com/Renato-RVF/meizex-a2a): agent-to-
agent discovery and task exchange, with an explicit sync/async
distinction per skill. The Grid answers "how do I reach a machine";
A2A answers "how do two agents decide what to do with each other." One
does not depend on the other.

## License

MIT — see [LICENSE](LICENSE).
