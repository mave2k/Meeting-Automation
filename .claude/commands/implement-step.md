---
description: Implement the next open step of a plan in docs/plans/
argument-hint: <plan file, e.g. 01-speaker-mapping> [step number]
---

Plan: $ARGUMENTS

1. Read the plan file in `docs/plans/` (match the name given above). If a step number is given, implement that step; otherwise implement the first unchecked step.
2. Implement **only that one step**. Respect "Out of scope".
3. If the step turns out to be wrong, unclear, or bigger than planned: stop and propose a change to the plan file instead of improvising.
4. When done: tick the step's checkbox, set Status to `in progress` (or `done` if it was the last step), and tell me briefly what changed and how to test it (see "How to test"). Then stop — do not start the next step and do not commit.
