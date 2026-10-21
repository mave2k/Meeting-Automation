# Feature: <name>

Status: draft | ready | in progress | done

Planned against: commit `<short hash>`
Depends on: <other plans that must be done first, or "none">

## Resume here
1. Start a fresh Claude Code session and run `/implement-step NN-<feature>`. It implements the first unchecked step below and stops.
2. Test that step as described in "How to test", then `git commit`.
3. Repeat until all steps are ticked; the last step sets Status to `done`.

Before step 1: if the code has changed since the planning commit (`git diff <short hash> -- meeting_processor/src`), check that "Approach" still matches the code. If it doesn't, update this plan before implementing.

## Goal
What should be possible afterwards? (2-3 sentences)

## Out of scope
- ...

## Approach
Which files/functions change, which new ones are added.
Note any impact on the LLM output contract (frontmatter fields, `{TITLE}`, `project:`, `extra_tags:`) — both `SYSTEM_PROMPT` and `SYSTEM_PROMPT_MOM` and their regexes must stay in sync.

## Steps
- [ ] 1. ...
- [ ] 2. ...

## Acceptance criteria
- Given <input>, the output is <...>

## How to test
Which export to run (`meeting-processor "../MacWhisper-Exports/<export>.json"`) and what to check in the Obsidian note.

## Open questions
- ...
