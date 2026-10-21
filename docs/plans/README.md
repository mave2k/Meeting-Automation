# Feature plans

New features are planned here before they are implemented. Planning and implementation happen in **separate Claude Code sessions**; the plan file carries the context between them, not the chat history.

## Files

- `_template.md` — template for a new plan
- `NN-<feature>.md` — one plan per feature, numbered in order (`01-…`, `02-…`)

Each plan has a `Status` line: `draft` → `ready` → `in progress` → `done`.

## Plan index

Update this table when a plan is added or its status changes. To continue with a plan, open it and follow its "Resume here" section.

| Plan | Status | Depends on |
|---|---|---|
| [01-config-file](01-config-file.md) | done | – |
| [02-content-files](02-content-files.md) | ready | 01 |
| [03-transcript-formats](03-transcript-formats.md) | ready | 02 |

## Workflow

### 1. Plan (no code changes)

Start a new conversation in the Claude Code panel, then either:

- run `/plan-feature <rough idea>`, or
- switch to **Plan Mode** (mode selector at the prompt box, or `Shift+Tab` in the CLI) and describe the feature. Plan Mode is read-only, so once the plan is good, leave Plan Mode and say "write it to docs/plans".

Let Claude ask questions and challenge the approach. Keep steps small enough that each one can be tested on its own.

**Then review and edit the plan file yourself** — it is the contract for the implementation.

### 2. Implement (fresh context)

Start a new conversation (or `/clear`), then:

```
/implement-step 01-<feature>        # next unchecked step
/implement-step 01-<feature> 3      # a specific step
```

Claude implements exactly one step, ticks its checkbox, tells you how to test it, and stops. Then:

1. Test it (see the plan's "How to test" section).
2. `git commit`.
3. Run `/implement-step` again for the next step.

### 3. When reality differs from the plan

If a step turns out wrong or the scope grows, don't improvise in the code session. Update the plan file first — by hand or in a short planning session — then continue implementing.

## Where the rules live

- Rules Claude follows in every session: [CLAUDE.md](../../CLAUDE.md), section "Workflow: plan first, then implement"
- The slash commands: [.claude/commands/](../../.claude/commands/)
