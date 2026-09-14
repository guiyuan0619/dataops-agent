# DataOps Agent Project Skills

These project-level Skill files package repeatable DataOps workflows for Agent clients.
They are workflow assets, not production plugins or direct release permissions.
Run `python -m dataops_agent.skill_registry` to export the reusable workflow
registry and boundary self-check report.

## Available Skills

| Skill | Purpose | Boundary |
|---|---|---|
| `table-quality-check` | Inspect CSV profile, deterministic quality issues, and repair preview before a governed release. | Read and preview only; publishing still requires the governance approval flow. |
| `hospital-knowledge-release` | Prepare DataOps-governed hospital knowledge candidates and downstream regression steps. | No patient-sensitive data; no active version claim before publish and regression pass. |

## Operating Rules

- Read `AGENTS.md`, `docs/agent_rules.md`, and `docs/project_background.md` before using these Skills.
- MCP is a governed DataOps control plane: dataset discovery, contracts, diagnostics, repair preview, approval-pending governance start, status, version status, and trace lookup.
- Qwen live usage requires explicit user notice before any call.
- MCP must not approve, publish, rollback, execute shell commands, or write active versions.
- Active-version write operations must go through FastAPI governance start/resume and human approval.
- Skill registry checks must pass before describing these workflows as reusable project assets.
