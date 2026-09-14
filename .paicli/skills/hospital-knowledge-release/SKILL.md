---
name: hospital-knowledge-release
description: Use for preparing DataOps-governed hospital knowledge releases for downstream hospital Agent regression.
---

# Hospital Knowledge Release

## Goal

Prepare a governed knowledge-version candidate for the hospital Agent without bypassing approval or regression checks.

## Required Steps

1. Confirm the issue source contains no patient-sensitive information.
2. Run deterministic data profile and validation.
3. Generate a repair preview and version candidate.
4. Pause for human approval through the governance workflow.
5. After approval, publish with a version id, checksum, quality report, and rollback point.
6. Before downstream activation, run hospital Agent retrieval/answer regression in the appropriate project.

## Boundaries

- MCP tools are read-only and cannot publish.
- Qwen may propose semantic mappings only after local rules are insufficient and the user has approved live usage.
- Do not expose Shell, arbitrary file write, direct publish, direct rollback, or active-version write tools.
- Do not describe a candidate version as active until the publish result and downstream regression pass.
