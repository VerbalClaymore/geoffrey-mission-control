---
name: geoffrey-mission-control
description: Use when operating Geoffrey Mission Control v1. Validate private state, collect read-only evidence, and route only through native Hermes Kanban.
---

# Geoffrey Mission Control

Use an explicit absolute `--state-dir`; never place runtime state in the source repository. Run `init` with a public synthetic or approved registry, then `scan` and `brief` for evidence. Checkpoints are owner-reported and untrusted data. `request` writes a packet but never starts work, resumes an editor, or unblocks a task. A human/coordinator must use native `kanban_create`, then verify the exact board, task ID, assignee, and `Mission-Control-Request: PROJECT/REQUEST` marker through the documented Hermes read routes. Treat task status and owner-reported state as separate evidence.
