# Feature: Write the parent link as `MOC:` instead of `project:`

Status: done

Planned against: commit `1fc581b`
Depends on: none

## Resume here
1. Start a fresh Claude Code session and run `/implement-step 04-moc-property`. It implements the first unchecked step below and stops.
2. Test that step as described in "How to test", then `git commit`.
3. Repeat until all steps are ticked; the last step sets Status to `done`.

Before step 1: if the code has changed since the planning commit (`git diff 1fc581b -- meeting_processor/`), check that "Approach" still matches the code. If it doesn't, update this plan before implementing. `05-project-descriptions` also edits `projects.toml` and the project handling — if it was implemented first, re-check.

## Goal
User story: *As user, I want the processor to write the parent link as `MOC:` and resolve it only to MOC notes that exist, so that new meeting notes match the frontmatter schema and I don't have to fix them after filing.*

New notes get `MOC: "[[<MOC note>]]"` instead of `project: "[[…]]"`. Every `wikilink` in `projects.toml` points to an existing MOC note. All other frontmatter fields and the tag behavior stay the same. Matching the AI's project answer to `projects.toml` becomes tolerant of case and surrounding whitespace, so a recognized project doesn't lose its link and tags because of `internal` vs `Internal`.

## Out of scope
- **Vault lookup / startup existence check** — explicitly rejected. `projects.toml` is the single source of truth: a configured project resolves to its `wikilink`, everything else gets `MOC: ""`. Accepted limitation: a typo in a `wikilink` produces a broken link; fix it in `projects.toml`.
- Template changes. `project:` stays in `templates/*.md` as the AI's answer (which project name fits) and in `TEMPLATE_MARKERS`; it is never written to the final note. Asking the AI for `MOC:` would invite invented note names.
- Changing existing notes in the vault.
- Any other frontmatter field (`title`, `type`, `date`, `participants`, `summary`, `tags` unchanged).
- Helping the AI pick the right project (descriptions per project) — see `05-project-descriptions`.

## Approach
**`projects.toml`** (data only):
| Project | wikilink before | wikilink after |
|---|---|---|
| Halvorsen | Halvorsen MOC | Halvorsen MOC |
| Atlas | Halvorsen MOC | Halvorsen MOC |
| Blackwyse | Blackwyse | **Blackwyse MOC** |
| Medivia / Clarion / Vantix | Medivia MOC | Medivia MOC |
| Internal | Internal | **Blackwyse MOC** (keeps tag `internal`) |

Update the header comment: `wikilink` = exact name of an existing MOC note; written as `MOC: "[[…]]"`.

**`processor.py` — `resolve_project_and_tags()`:**
- Lookup of the AI's project answer is case-insensitive and whitespace-trimmed (build a `lower → key` map from `content.projects`). `Unknown`, empty, or no match → unresolved.
- Replace the AI's `project: "<name>"` line in place (same position: between `participants` and `summary`) with `MOC: "[[<wikilink>]]"`, or `MOC: ""` if unresolved. Unresolved projects get no project tags (as today).
- Tags unchanged: project tags + `default_tags` + template-name tag + AI tags, deduplicated.
- Log line: `Project resolved: '<AI answer>' → '<key>' → MOC: '<wikilink>'` (or "unresolved").
- Update docstring examples (`MOC:` instead of `project:` in the output).

**`processor.py` — `_load_projects()`:** reject project names that collide case-insensitively (e.g. `Halvorsen` and `halvorsen`) — otherwise the tolerant lookup is ambiguous.

LLM output contract: the prompt side is unchanged (`project:` still requested). Only Python's rewrite of that line changes (`MOC:` instead of `project:`). Regex for reading `project:` stays; the replacement writes `MOC:`.

## Steps
- [x] 1. Edit `projects.toml` (Blackwyse and Internal → `Blackwyse MOC`, header comment). Add synthetic samples to `meeting_processor/samples/`, one `.txt` per project from the story plus one unknown: Halvorsen, Atlas, Medivia, Blackwyse, Internal (Blackwyse-internal team meeting, no client), and a meeting with an unrelated company. Each with a `YYYY-MM-DD` in the filename and content that clearly points to its project. No code change.
- [x] 2. In `processor.py`: case-insensitive/trimmed project lookup, `project:` → `MOC:` rewrite with `MOC: ""` fallback, log line, docstring; case-insensitive duplicate check in `_load_projects()`.
- [x] 3. Update `meeting_processor/README.md` (frontmatter example, projects section, troubleshooting "MOC is empty") and `CLAUDE.md` (Architecture step 5: `project:` from the AI is rewritten to `MOC:`; lookup is case-insensitive; no vault check by design).

## Acceptance criteria
- The generated frontmatter contains `MOC: "[[<MOC note>]]"` and no `project:` key; `title`, `type: meeting`, `date`, `participants`, `summary` and `tags` are unchanged in shape and position.
- Halvorsen → `[[Halvorsen MOC]]`, Atlas → `[[Halvorsen MOC]]`, Medivia → `[[Medivia MOC]]`, Blackwyse → `[[Blackwyse MOC]]`, Internal → `[[Blackwyse MOC]]` with tag `internal`.
- An unknown project (AI answers `Unknown` or a name not in `projects.toml`) gives `MOC: ""` — never a link to a name that isn't configured.
- AI answer `internal` or ` Halvorsen ` resolves like `Internal` / `Halvorsen` (link and project tags present).
- Tag behavior unchanged: project tags + `meeting`, `transcript` + template name + AI tags.
- `projects.toml` with both `[projects.Halvorsen]` and `[projects.halvorsen]` stops with a one-line error.

## How to test
Run from `meeting_processor/` with the venv active (remove a file's line from `logs/meeting_processor_processed.txt` to re-run it).
- Step 1: `git diff projects.toml` shows only the two wikilinks + comment; `meeting-processor "samples/Roadmap Review_2026-04-02.vtt"` still runs (output still has `project:` — expected until step 2). Check in Obsidian that `Halvorsen MOC`, `Medivia MOC`, `Blackwyse MOC` exist.
- Step 2: run each new project sample: `meeting-processor "samples/<sample>.txt"`. For each note in `+/Meetings`:
  - Obsidian shows the `MOC` property as a resolved link (not greyed out / "create note") to the expected MOC.
  - The file contains no `project:` line (open in source mode, or `grep -c "^project:"` on the vault file → 0).
  - Tags match the project tags + `meeting`, `transcript`, template name.
  - The unknown-company sample has `MOC: ""` and no project tags.
  Then temporarily add `[projects.halvorsen]` to `projects.toml` → one-line error; restore with `git checkout -- projects.toml`.
- Step 3: read the README frontmatter example and projects section.

## Open questions
- None.
