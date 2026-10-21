# Feature: Move prompts, projects and tags out of the code

Status: ready

Planned against: commit `d35c1f3` (after `01-config-file`)
Depends on: `01-config-file` (done)

## Resume here
1. Start a fresh Claude Code session and run `/implement-step 02-content-files`. It implements the first unchecked step below and stops.
2. Test that step as described in "How to test", then `git commit`.
3. Repeat until all steps are ticked; the last step sets Status to `done`.

Before step 1: if `processor.py` has changed since the planning commit (`git diff d35c1f3 -- meeting_processor/src`), for example because another plan was implemented first, check that "Approach" still matches the code. If it doesn't, update this plan before implementing.

## Goal
`processor.py` contains only code. The two system prompts live in `meeting_processor/prompts/` as Markdown files, projects and default tags in `meeting_processor/projects.toml` — all tracked in git. Editing a prompt or adding a project no longer means touching Python, and a broken prompt or projects file stops the run with a clear message instead of silently producing wrong notes.

## Out of scope
- Generic note types / new flavors (`….<name>.json` → `prompts/<name>.md`, per-flavor tags). Planned as the next feature; this plan only names files so that extension is easy.
- Option B (code owns the frontmatter instructions, files hold only body/style). We go with option A: the whole prompt in the file, plus validation.
- Any change to prompt wording or to the LLM output contract (`{TITLE}`, `project:`, `extra_tags:`) and its regexes. Prompts are moved verbatim.
- Hardcoded `mom` flavor tag and `.mom.json` detection — stay in code for now.
- Storing prompts in the Obsidian vault, env vars, hot reload.
- `config.toml` (secrets) — unchanged.

## Approach
New files (committed to git):

```
meeting_processor/
  projects.toml
  prompts/
    standard.md   ← SYSTEM_PROMPT, verbatim
    mom.md        ← SYSTEM_PROMPT_MOM, verbatim
```

`projects.toml` (same values as today's `PROJECTS` / `DEFAULT_TAGS`):
```toml
# Tags added to every note regardless of project
default_tags = ["meeting", "transcript"]

# Key = name the AI uses to identify the project (names with spaces need quotes:
# [projects."Halvorsen Nord"]). wikilink = Obsidian note to link in [[...]].
# tags = fixed tags added when this project is matched.
[projects.Halvorsen]
wikilink = "Halvorsen MOC"
tags     = ["halvorsen"]
# … one table per current PROJECTS entry
```

Prompt files: content identical to the current string constants (text between the opening `"""` and the closing `"""`, i.e. ending with exactly one newline). Placeholders `{DATE}`, `{AI_NAME}`, `{PROJECT_NAMES}`, `{TITLE}` unchanged.

`processor.py`:
- New path constants `PROJECTS_FILE = PROJECT_DIR / "projects.toml"`, `PROMPTS_DIR = PROJECT_DIR / "prompts"`.
- New frozen dataclass `Content` (`projects: dict`, `default_tags: list[str]`, `prompt_standard: str`, `prompt_mom: str`) and `load_content()` that reads and validates both. Errors raise the existing `ConfigError` (one-line log + exit 1 in `main()`, before any file is processed and before anything is marked processed).
- Validation:
  - `projects.toml` missing or invalid TOML.
  - `default_tags` is a list of strings (may be empty).
  - `projects` has at least one entry; each has a non-empty string `wikilink` and a `tags` list of strings (may be empty).
  - No project named `Unknown` (reserved — the code appends it to `{PROJECT_NAMES}`).
  - Each prompt file exists and contains all contract markers: `{TITLE}`, `{PROJECT_NAMES}`, `project:`, `extra_tags:`. Error names the file and the missing marker.
- `main()` calls `load_content()` right after `load_config()` and passes `content` down: `process_meeting(json_file, config, content)` → `build_messages(parsed, config, content, mom=…)` and `resolve_project_and_tags(markdown, content, extra_fixed_tags=…)`.
- Delete `PROJECTS`, `DEFAULT_TAGS`, `SYSTEM_PROMPT`, `SYSTEM_PROMPT_MOM`. The placeholder documentation comment moves to the README (and a short note in `projects.toml`/prompt section).

LLM output contract: unchanged — prompts are byte-identical, regexes untouched.

## Steps
- [ ] 1. Create `prompts/standard.md`, `prompts/mom.md` (verbatim copies) and `projects.toml` (same values). No code change. Verify with a one-off Python check that the files equal the current constants (see How to test).
- [ ] 2. Add `PROJECTS_FILE`, `PROMPTS_DIR`, `Content`, `load_content()` with validation; call it in `main()` after `load_config()` with the same `ConfigError` handling. Result not used yet — behavior unchanged.
- [ ] 3. Pass `content` through `process_meeting` → `build_messages` / `resolve_project_and_tags`; delete the four constants; update the module docstring and section comments.
- [ ] 4. Update `meeting_processor/README.md` (Projects + System prompt sections, placeholder list, troubleshooting, file tree) and `CLAUDE.md` (Architecture: where prompts/projects live, `load_content()`, contract markers are validated).

## Acceptance criteria
- `processor.py` contains no prompt text, no project names, no tag values.
- `prompts/standard.md`, `prompts/mom.md`, `projects.toml` are tracked in git (`git ls-files` lists them).
- Given the same export, the generated note has the same structure, project wikilink and tags as before the change (prompt sent to the LLM is identical).
- Adding a project in `projects.toml` makes it appear in `{PROJECT_NAMES}` and resolve to its wikilink/tags without touching code.
- Removing `extra_tags:` (or `{TITLE}`, `{PROJECT_NAMES}`, `project:`) from a prompt file stops the run with a one-line error naming the file and marker — no API call, nothing written to Obsidian, file not marked processed.
- Missing/invalid `projects.toml`, a project without `wikilink`, or a project named `Unknown` each give a one-line actionable error.

## How to test
- Step 1 (run from `meeting_processor/` with the venv active):
  ```zsh
  python -c "
  import tomllib; from pathlib import Path
  from meeting_processor import processor as p
  assert Path('prompts/standard.md').read_text(encoding='utf-8') == p.SYSTEM_PROMPT
  assert Path('prompts/mom.md').read_text(encoding='utf-8') == p.SYSTEM_PROMPT_MOM
  d = tomllib.loads(Path('projects.toml').read_text(encoding='utf-8'))
  assert d['projects'] == p.PROJECTS and d['default_tags'] == p.DEFAULT_TAGS
  print('identical')"
  ```
- Step 2: run `meeting-processor "../MacWhisper-Exports/<export>.json"` → works as before. Then temporarily: rename `projects.toml`; remove `extra_tags:` from `prompts/mom.md`; add `[projects.Unknown]` → each gives the expected error. Restore with `git checkout -- projects.toml prompts/`.
- Step 3: remove the export's line from `logs/meeting_processor_processed.txt`, run once with a standard export and once with a `.mom.json` export → notes in `+/Meetings` have correct title, `project: "[[…]]"` and merged tags (incl. `mom` for MoM). Add a test project to `projects.toml` and check it appears in the log's project resolution when matched.
- Step 4: read the README setup/customization sections as a new user.

## Open questions
- None.
