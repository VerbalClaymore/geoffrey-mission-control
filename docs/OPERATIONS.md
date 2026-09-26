# Geoffrey Mission Control operations

Use `python -m geoffrey_mission_control --state-dir ABSOLUTE_PATH`.

The state directory is private (0700/0600) and must not be inside the source repository. `init` installs a validated registry; `scan` gathers read-only evidence; `brief` renders the derived report; `checkpoint` ingests owner-reported state; `request` writes a validated packet only. The CLI never dispatches work or resumes editors. Native Hermes Kanban creation is an operator action; `receipt` performs a pinned version/list/show read-back and records identity verification while explicitly leaving the idempotency key unverified because Hermes JSON does not expose it.

Use `scan --quiet-unchanged` from a read-only scheduler. Semantic changes and all errors remain visible; a failed required source returns exit status 1. Checkpoint and request timestamps are evaluated against one command clock. Do not treat owner checkpoints or Kanban `done` status as independent acceptance evidence.
