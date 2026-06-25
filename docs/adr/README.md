# Architectural Decision Records

These ADRs lock in the architectural patterns from `refactor-plan.md`. Each
phase's PR should reference the relevant ADR; the ADR is updated only if a
later phase materially changes the contract.

| ADR | Title | Phase | Status |
|---|---|---|---|
| 0001 | [Subprocess runner](0001-subprocess-runner.md) | P2 | Proposed |
| 0002 | [Cancellation contract](0002-cancellation-contract.md) | P1 | Proposed |
| 0003 | [Plugin protocol](0003-plugin-protocol.md) | P5 | Proposed |
| 0004 | [Error & result types](0004-error-hierarchy.md) | P7 | Proposed |
| 0005 | [Legal posture and the model registry](0005-legal-posture-and-model-registry.md) | — | Accepted |

## Why ADRs

The audit found the original 9-phase plan was ~70% right and ~30% wrong
because it was drafted from memory after a long line-count refactor.
ADRs prevent the same problem recurring: every contributor who touches a
load-bearing surface (subprocess, cancellation, plugin, error) reads the
ADR before changing it.

A change to one of these ADRs is itself a PR — discussion, review, sign-off.
A regression that violates an ADR fails the gate tests added by the phase
that landed it.

## Format

Each ADR is 1–2 pages. Sections:

- **Status** — Proposed / Accepted / Superseded.
- **Context** — what's wrong today; cite file:line.
- **Decision** — the chosen contract.
- **Consequences** — what we gain, what we give up, what tests enforce it.
- **Out of scope** — what this ADR explicitly does NOT cover.

Keep them short. If an ADR runs past 2 pages, the decision isn't sharp
enough yet.
