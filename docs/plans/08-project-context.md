# Feature: Project context files (classify the project first, then summarize with its context)

Status: done

Planned against: commit `57c1a65`
Depends on: none. Interacts with `06-ai-call-resilience` (both touch `call_ai()` / `process_meeting()`) and `07-watch-folder-config` (`--dry-run`); `05-project-descriptions` builds on this plan — see "Interactions with other plans".

## Resume here
1. Start a fresh Claude Code session and run `/implement-step 08-project-context`. It implements the first unchecked step below and stops.
2. Test that step as described in "How to test", then `git commit`.
3. Repeat until all steps are ticked; the last step sets Status to `done`.

Before step 1: if the code has changed since the planning commit (`git diff 57c1a65 -- meeting_processor/src`), check that "Approach" still matches the code. If it doesn't, update this plan before implementing. In particular, if 06 or 07 were implemented first, see "Interactions with other plans".

**Never put real client names into tracked files** (this plan, `projects.example.toml`, the prompt file, README). Examples below use the made-up projects from `projects.example.toml`.

## Goal
User story: *As user, I want to attach one or more Markdown context files to a project in `projects.toml`, so that the AI knows the project's background (people and roles, name spellings, terms, goals) when it summarizes a meeting of that project.*

Since the project must be known before the note is generated, a short **classification call** identifies the project first. If that project has context files, their text is sent along with the transcript in the main call. The classified project is authoritative: it also sets `MOC:` and the project tags, so a note can never be filed under one project but written with another project's context.

## Out of scope
- Deterministic project detection (filename hint like `Call.halvorsen.vtt`, keyword rules, participant lists).
- A separate / cheaper model for the classification call — it uses the configured provider + model.
- Project descriptions to improve classification — that's `05-project-descriptions` (revised to target the classifier prompt).
- A global context file sent for every meeting, context for `Unknown`, context per template.
- Reading context from Obsidian notes (e.g. the MOC note) via the REST API.
- Size limits for context files; truncating the transcript for the classifier (full transcript is sent; the log shows the context size).
- Changes to templates, `TEMPLATE_MARKERS`, frontmatter shape or the post-processing regexes.
- New dependencies.

## Approach

### Configuration (`projects.toml`, `project_context/`)
```toml
[projects.Halvorsen]
wikilink = "Halvorsen MOC"
tags     = ["halvorsen"]
context  = ["halvorsen_context.md"]          # optional; files in meeting_processor/project_context/

# Several projects may share a file, and a project may list several files
[projects.Clarion]
wikilink = "Medivia MOC"
tags     = ["medivia", "clarion", "vantix"]
context  = ["medivia_programme.md", "clarion_team.md"]
```
- New folder `meeting_processor/project_context/` — **git-ignored** (holds real client information). Add `meeting_processor/project_context/` to the root `.gitignore`.
- `projects.example.toml` documents `context` **in a comment only** (so copying the example doesn't produce a config error for a missing file).

### `processor.py` — loading
- New path constants: `PROJECT_CONTEXT_DIR = PROJECT_DIR / "project_context"`, `PROMPTS_DIR = PROJECT_DIR / "prompts"`, `IDENTIFY_PROMPT_FILE = PROMPTS_DIR / "identify_project.md"`.
- `_load_projects()`: optional `context`. If present it must be a list of strings (`ConfigError` otherwise, e.g. `[projects.X] context must be a list of .md filenames, e.g. ["x_context.md"]`). An empty list means "no context".
- New `_load_project_context(projects, context_dir) -> dict[str, str]` (project name → combined context text, only for projects with a non-empty `context`). Per file, `ConfigError` (one line, naming project + file) if:
  - it doesn't end in `.md`,
  - `(context_dir / name).resolve()` is not inside `context_dir.resolve()` (`Path.is_relative_to`; rejects `..` and absolute paths; sub-folders are fine),
  - it doesn't exist (if the folder itself is missing: `project_context/ not found — create it next to projects.toml`),
  - it is empty / whitespace-only,
  - it can't be decoded as `utf-8-sig`.
  Several files are joined in the listed order, each preceded by a line `[File: <name>]`, separated by a blank line.
- New `_load_identify_prompt(path) -> str`: file must exist and contain `{PROJECT_NAMES}` (`ConfigError` otherwise).
- `Content` gets two fields: `project_context: dict[str, str]` and `identify_prompt: str`. `load_content()` gets `context_dir` / `identify_prompt_file` parameters with the constants as defaults and fills them.

### `prompts/identify_project.md` (new, tracked)
Not in `templates/` (that folder is loaded as note templates and would fail marker validation). Draft text:
```
You identify which project a meeting transcript belongs to.

Answer with exactly one name from this list and nothing else — no explanation,
no quotes, no punctuation:
{PROJECT_NAMES}

Answer Unknown if the meeting matches none of these projects. Do not guess.
```

### `processor.py` — classification
- Extract two small helpers used by both the classifier and the existing code:
  - `_project_names(content) -> str` — `", ".join([*content.projects, RESERVED_PROJECT])` (currently inline in `build_messages()`).
  - `_lookup_project(answer, content) -> str | None` — case-insensitive, whitespace-trimmed lookup (currently inline in `resolve_project_and_tags()`); returns the key as written in `projects.toml`, or `None`.
- Extract `_user_message(parsed, context: str | None = None) -> str` so the classifier and the main call send the same date + transcript (step 2 uses it without context).
- New `identify_project(parsed, config, content) -> str`:
  - system = `content.identify_prompt` with `{PROJECT_NAMES}` replaced (`str.replace`); user = `_user_message(parsed)`.
  - `call_ai(system, user, config)`; take the first non-empty line, strip whitespace and the characters `"'`*.` from both ends.
  - `_lookup_project()` → project key; no match → `log.warning("  Classifier answer '<answer>' matches no project — using Unknown")` and return `RESERVED_PROJECT`.
- `process_meeting()`: after `parse_transcript`, **only if `content.project_context` is non-empty** (at least one project has context):
  - `log.info(f"  Identifying project via {config.provider} ({config.model})...")`
  - `project = identify_project(...)`
  - log `  Project identified: Halvorsen (context: halvorsen_context.md, 4,213 chars)` or `  Project identified: Atlas (no context)` / `  Project identified: Unknown (no context)`.
  Otherwise `project = None` and everything behaves exactly as today (one AI call).

### `processor.py` — using the result (step 3)
- `build_messages(parsed, config, content, template, project: str | None = None)`: if `project` has an entry in `content.project_context`, the user message is
  ```
  Meeting date: <date>

  <PROJECT_CONTEXT_HEADER>
  --- begin project background ---
  <context text>
  --- end project background ---

  Transcript:
  <transcript>
  ```
  `PROJECT_CONTEXT_HEADER` is a constant in code:
  > Project background — reference material for this project, NOT part of the meeting. Use it to understand names, roles, terms and goals, and to spell them correctly. Do not report anything from it as discussed, decided or assigned in this meeting.

  Without context the user message is unchanged.
- `resolve_project_and_tags(markdown, content, extra_fixed_tags=None, project: str | None = None)`:
  - `project is None` → today's behaviour (AI's `project:` answer decides).
  - otherwise the given project decides (`RESERVED_PROJECT` → `MOC: ""`, no project tags). The AI's `project:` answer is still parsed; if `_lookup_project(ai_answer)` differs from the given project, `log.info("  Note call answered project '<ai>' — using identified project '<project>'")`.
  - The `project:` line is still replaced with `MOC:` in place, `extra_tags:` handling unchanged.
- `process_meeting()` passes `project` to both.

LLM output contract: unchanged. Templates keep asking for `project:`; when the classifier ran, Python ignores that answer except for the disagreement log.

Cost / latency: with context configured, every meeting costs one extra call (transcript sent twice, tiny output), roughly +5–15 s.

### Interactions with other plans
- **06-ai-call-resilience:** the classification call goes through `call_ai()`, so 06's timeout + retries apply to it automatically — no extra work in either order. If 06 is done first, the "Sending to …" log line has a `timeout …, max … attempts` suffix; the new "Identifying project via …" line doesn't need it. Worst-case time until a final failure doubles (two calls). A classifier failure propagates like a main-call failure (`Failed [<file>]`, file not marked processed, and with 06 a notification).
- **07-watch-folder-config:** `--dry-run` never calls `process_meeting()`, so the classifier must not run in a dry run — nothing to do as long as the classification stays inside `process_meeting()`. Don't move it into `main()`.
- **05-project-descriptions:** builds on this plan — descriptions go into the `identify_project.md` prompt. Nothing in this plan should anticipate it beyond the `_project_names()` helper.

## Steps
- [x] 1. **Load context files.** `context` in `_load_projects()`, `_load_project_context()`, `PROJECT_CONTEXT_DIR`, `Content.project_context`, `.gitignore` entry, commented `context` example in `projects.example.toml`. Not used yet — runs produce the same notes as before.
- [x] 2. **Classifier, log only.** `prompts/identify_project.md`, `_load_identify_prompt()`, `Content.identify_prompt`, helpers `_project_names()` / `_lookup_project()` / `_user_message()` (refactor existing code to use them, no behaviour change), `identify_project()`; `process_meeting()` calls it when any project has context and logs the result. MOC/tags still come from the note call.
- [x] 3. **Use the result.** `build_messages()` adds the background block for the identified project; `resolve_project_and_tags()` takes `project` and uses it, logging disagreements; `process_meeting()` passes `project` to both.
- [x] 4. **Docs.** `README.md` (repo root): new section "Project context" — `context` in `projects.toml`, the `project_context/` folder (git-ignored), what makes a good context file (people + roles, name spellings, glossary/abbreviations, goals, current phase; keep it short, no meeting history), the extra classification call (cost/latency, only when any project has context), the classifier deciding the MOC, Troubleshooting rows for the new config errors and "wrong project identified" (check the "Project identified" log line). `CLAUDE.md`: `Content` fields, `project_context/` and `prompts/` folders, new pipeline step "Project identification" between `parse_transcript` and `build_messages`, the `project` override in `resolve_project_and_tags`. Set Status to `done` and update the plan index.

## Acceptance criteria
- No project has `context` → no "Identifying project" line, exactly one AI call, notes as before.
- Halvorsen has `context = ["halvorsen_context.md"]`, Halvorsen meeting → log shows "Identifying project…", then `Project identified: Halvorsen (context: halvorsen_context.md, N chars)`; two AI calls; note has `MOC: "[[Halvorsen MOC]]"` and Halvorsen tags; a distinctive fact from the context (e.g. the project lead's name) is used correctly in the note; background-only facts are **not** listed as decisions / action items.
- Meeting of a project without context (while another project has context) → classifier runs, `Project identified: <X> (no context)`, user message has no background block, MOC is X's.
- Classifier answer not in the list → warning, `Unknown`, `MOC: ""`, no project tags — even if the note call answered a valid project (info line about the disagreement).
- Note call's `project:` differs from the identified project → info line; identified project wins.
- Two projects listing the same file → both load and both get the context.
- Config errors (one `Config error: …` line, exit 1, nothing processed): `context` not a list; file not `.md`; `../x.md` or absolute path; file missing; `project_context/` missing while a project has context; empty file; undecodable file; `prompts/identify_project.md` missing or without `{PROJECT_NAMES}`.
- `context = []` → treated as no context, no error.

## How to test
Work in `meeting_processor/` with the venv active. Remove the sample's line from `logs/meeting_processor_processed.txt` before each re-run.

Setup for steps 2–3: temporarily add to your `projects.toml`
```toml
[projects.Halvorsen]
wikilink = "Halvorsen MOC"
tags     = ["halvorsen"]
context  = ["halvorsen_context.md"]
```
and create `project_context/halvorsen_context.md`, e.g.:
```markdown
# Halvorsen rollout
- Halvorsen project lead: Dr. Ingrid Solberg (client side)
- Anna Weber: our project manager; James Okafor: our technical lead
- "Second plant" = plant Bergen, go-live August 2026
- Plant Trondheim is planned for Q1 2027 (not yet decided)
```
Test transcript: `samples/Project Halvorsen Rollout Sync 2026-07-07.txt`. Remove the temporary project and file after testing.

- **Step 1:** without `context` anywhere → normal run, same note as before. Add the Halvorsen setup → still the same behaviour (context loaded but unused). Break it one way at a time (`context = "x.md"`, `["x.txt"]`, `["../config.toml"]`, `["missing.md"]`, an empty file, rename the folder) → each gives a one-line `Config error`, exit 1 (`echo $?`).
- **Step 2:** run the Halvorsen sample → "Identifying project…" + `Project identified: Halvorsen (context: …)`. Run a non-Halvorsen sample (e.g. `samples/Sprint Retro 2026-05-20.txt`) → `Unknown` or the matching project, `(no context)`. Remove all `context` entries → no "Identifying" line. Delete `{PROJECT_NAMES}` from the prompt file → config error; restore.
- **Step 3:** Halvorsen sample → note mentions Dr. Ingrid Solberg / plant Bergen where it fits; plant Trondheim / Q1 2027 does **not** appear as a decision; `MOC: "[[Halvorsen MOC]]"`. To see the override: temporarily set the classifier prompt to always answer `Unknown` → note gets `MOC: ""` and the disagreement info line; restore.
- **Step 4:** read the README section and the CLAUDE.md change.

## Open questions
- none
