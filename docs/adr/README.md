# Architectural Decision Records

These ADRs record the architectural contracts for bpp's load-bearing
surfaces. A PR that changes one of these surfaces should reference the
relevant ADR; the ADR is updated only if the change materially alters the
contract.

| ADR | Title | Status |
|---|---|---|
| 0001 | [Subprocess runner](0001-subprocess-runner.md) | Accepted |
| 0002 | [Cancellation contract](0002-cancellation-contract.md) | Accepted |
| 0003 | [Plugin protocol](0003-plugin-protocol.md) | Accepted (partially implemented) |
| 0004 | [Error & result types](0004-error-hierarchy.md) | Accepted (implemented with a revised class set) |
| 0005 | [Legal posture and the model registry](0005-legal-posture-and-model-registry.md) | Accepted |

## Why ADRs

Contracts drafted from memory drift from the code. ADRs keep the
contract written down next to the code: every contributor who touches a
load-bearing surface (subprocess, cancellation, plugin, error) reads the
ADR before changing it.

A change to one of these ADRs is itself a PR — discussion, review, sign-off.
A regression that violates an ADR fails the gate tests that landed with
its implementation.

## Format

Each ADR is 1–2 pages. Sections:

- **Status** — Proposed / Accepted / Superseded.
- **Context** — what's wrong today; cite file:line.
- **Decision** — the chosen contract.
- **Consequences** — what we gain, what we give up, what tests enforce it.
- **Out of scope** — what this ADR explicitly does NOT cover.

Keep them short. If an ADR runs past 2 pages, the decision isn't sharp
enough yet.
