# Feature: Project descriptions to guide the AI's project choice

Status: draft

Planned against: commit `1fc581b`
Depends on: `08-project-context` — implement 08 first, then revise this plan (see "Revision needed after 08"). (`04-moc-property` is done.)

## Revision needed after 08
`08-project-context` adds a separate classification call (`identify_project()`, prompt in `prompts/identify_project.md`) that decides the project — and with it `MOC:` and tags — **whenever any project has a `context` file**. That changes this plan:
- The descriptions belong in the classifier prompt (`identify_project.md`, rendered via 08's `_project_names()` helper or a new `{PROJECT_LIST}` placeholder there), not in the note templates. Open question 1 below (placeholder in `templates/*.md`) then mostly disappears — no template change, `TEMPLATE_MARKERS` untouched.
- New open question: the classifier only runs when some project has context. Should it also run when any project has a `description` (recommended: yes — "run the classifier if any project has context or a description"), or should descriptions additionally go into the templates for the no-classifier case?
- Re-check `_load_projects()`, `build_messages()` and the `Content` dataclass against the code after 08 before finalizing the steps.

## Resume here
This plan is a **draft**: resolve the open questions in a short planning session (`/plan-feature` or Plan Mode, referring to this file), set Status to `ready`, then:
1. Start a fresh Claude Code session and run `/implement-step 05-project-descriptions`. It implements the first unchecked step below and stops.
2. Test that step as described in "How to test", then `git commit`.
3. Repeat until all steps are ticked; the last step sets Status to `done`.

Before step 1: check `git diff 1fc581b -- meeting_processor/` and make sure "Approach" still matches the code (especially if 04 was implemented in the meantime). If it doesn't, update this plan before implementing.

## Goal
Today the AI picks the project from a bare comma-separated list of names (`Halvorsen, Atlas, Blackwyse, Medivia, Clarion, Vantix, Internal, Unknown`) injected via `{PROJECT_NAMES}` into one frontmatter line of each template. It has no idea what "Internal" or "Atlas" means, so overlapping choices (Blackwyse vs Internal, Halvorsen-only meetings vs Internal) are picked inconsistently. With an optional `description` per project in `projects.toml`, the AI gets a short explanation of each project and picks the same project for the same kind of meeting.

## Out of scope
- Deterministic (non-AI) project detection, e.g. keyword rules, participant lists or filename hints.
- Changing how a chosen project is resolved to `MOC:` and tags (that's `04-moc-property`).
- Merging or removing projects (e.g. dropping Internal) — data decision for the user, not code.
- Template-specific project lists.

## Approach
(Preliminary — confirm in the open questions.)

`projects.toml` gets an optional `description` per project:
```toml
[projects.Internal]
wikilink    = "Blackwyse MOC"
tags        = ["internal"]
description = "Blackwyse-internal meetings without client participants"
```

`processor.py`:
- `_load_projects()` accepts an optional `description` (non-empty string if present).
- `build_messages()` renders a project list including descriptions, e.g.
  ```
  - Halvorsen: Halvorsen group projects, client meetings with Halvorsen employees
  - Internal: Blackwyse-internal meetings without client participants
  - Unknown: none of the above
  ```
  and injects it into the template via a new placeholder (see open question 1).

Templates (`templates/summary.md`, `templates/mom.md`): add the project list in a short instruction block outside the frontmatter, e.g. "Choose `project` from this list: {PROJECT_LIST}". Both templates must change in sync.

LLM output contract: the AI still answers in `project: "<name>"` with a key from `projects.toml`; only the guidance changes. If a new placeholder becomes mandatory, add it to `TEMPLATE_MARKERS`.

## Steps
(Preliminary — finalize once the open questions are resolved.)
- [ ] 1. Add `description` to `projects.toml` for each project (user writes the texts) and accept it in `_load_projects()`. Not used yet.
- [ ] 2. Render the project list with descriptions in `build_messages()` and inject it via the chosen placeholder; update both templates.
- [ ] 3. Update README (projects section: what a good description looks like) and CLAUDE.md.

## Acceptance criteria
- A project with a `description` appears with that description in the prompt sent to the AI; one without appears with its name only.
- Running the same Blackwyse-internal sample several times picks the same project each time.
- A Halvorsen-only internal meeting is classified as Halvorsen, not Internal (given suitable descriptions).
- Missing descriptions don't break anything; an empty `description = ""` gives a one-line config error.

## How to test
- Log or print the rendered system prompt for one sample and check the project list.
- Run the Internal, Blackwyse and Halvorsen samples from `04-moc-property` 2–3 times each and compare the chosen project (log line "Project resolved").

## Open questions
1. **Placeholder:** keep `{PROJECT_NAMES}` in the frontmatter line (names only) and add a new `{PROJECT_LIST}` placeholder in an instruction block with descriptions (recommended) — or render descriptions directly into `{PROJECT_NAMES}` (no template change, but a long, messy frontmatter hint line)?
2. **Mandatory?** Should `description` be required for every project, or optional (recommended: optional)?
3. **Unknown guidance:** should the list include an explicit line for `Unknown` ("none of the above — do not guess")?
4. **Texts:** the user writes the descriptions for Halvorsen, Atlas, Blackwyse, Medivia, Clarion, Vantix, Internal before step 1.
