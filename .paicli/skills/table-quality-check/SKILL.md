---
name: table-quality-check
description: Use for DataOps table quality inspection, dirty-data diagnosis, repair preview, and quality report generation.
---

# Table Quality Check

## Goal

Inspect CSV data quality with deterministic rules before any LLM planning or write operation.

## Required Steps

1. Read `AGENTS.md`, `docs/agent_rules.md`, and `docs/project_background.md`.
2. Load the target CSV through project-safe paths only.
3. Run data profile and deterministic issue detection.
4. Build a repair preview only; do not publish or mutate data.
5. Report issue counts by type, sample rows, and whether the quality gate passes.
6. If a fix is needed, start the governance workflow and wait for explicit approval.

## Boundaries

- Do not call Qwen for ordinary missing, duplicate, enum, or stale-record checks.
- Do not expose Shell, arbitrary file write, or direct publish tools.
- Do not claim production data quality metrics without a saved report and data-source boundary.
