# Architecture Documentation Boundary Matrix

Purpose: make the active-vs-archived architecture document boundary explicit and stable.

Last verified: 2026-07-22

## Criteria

Promotion criteria (archive -> active docs):
1. Needed for current design, implementation, or operator decisions.
2. Contains unique technical context not already captured in active docs/issues.
3. Can be maintained against current repository state.

Archive criteria (active -> debris or remain archived):
1. Historical/superseded context only.
2. Content duplicated by current canonical docs or active issues.
3. Snapshot/reference value is useful, but it should not drive current decisions.

## Boundary Matrix

| Path | Ownership | Status | Canonical Authority | Rationale |
|---|---|---|---|---|
| `docs/architecture_overview.md` | workspace/docs | active | yes | Primary high-level architecture map for current repo docs. |
| `docs/hardware_architecture.md` | workspace/docs | active | yes | Canonical hardware architecture narrative for active work. |
| `docs/apollo_samd11_mcu/apollo_watchdog_architecture.md` | workspace/docs/apollo_samd11_mcu | active | yes | Canonical Apollo watchdog architecture context for current decisions. |
| `docs/architecture/cyn_entrypoint_architecture.md` | workspace/docs | active (promoted) | yes | Promoted from debris because it documents current `cyn` entrypoint architecture. |
| `docs/apollo_samd11_mcu/cynthion_architecture_scan_2026_05_22.md` | workspace/docs | active (research) | scoped | Active research snapshot retained as reference for architecture decisions. |
| `docs/apollo_samd11_mcu/apollo_serial_architecture_redesign_plan.md` | workspace/docs | active (plan) | scoped | Active implementation plan linked to ongoing architecture work. |
| `docs/design_history/serial_communication_redesign_decisions.md` | workspace/docs | active (history) | scoped | Canonical decision history for redesign context. |
| `debris/architecture.md` | archive/debris | archived | no | Historical snapshot retained for provenance only; replaced by active docs and issue tracking. |
| `debris/code/cynthion-workspace-prototype/docs/architecture.md` | archive/debris-prototype | archived | no | Prototype snapshot reference; not authoritative for current workspace behavior. |

## Current Promotion/Archive Decision Summary

Docs that were intentionally promoted and should remain active:
1. `docs/architecture/cyn_entrypoint_architecture.md`
2. `docs/apollo_samd11_mcu/cynthion_architecture_scan_2026_05_22.md`
3. `docs/apollo_samd11_mcu/apollo_serial_architecture_redesign_plan.md`
4. `docs/design_history/serial_communication_redesign_decisions.md`

Docs that should remain archived:
1. `debris/architecture.md`
2. `debris/code/cynthion-workspace-prototype/docs/architecture.md`

No additional architecture docs are currently identified for promotion or demotion.

## Anti-Ambiguity Rule

If a topic appears in both active docs and archived docs, active docs are authoritative.
Archived docs are reference/provenance only and must not be used as decision authority.