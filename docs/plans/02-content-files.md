# Feature: Templates, projects and tags out of the code

Status: done

Planned against: commit `883636b` (after `01-config-file` and `03-transcript-formats`)
Depends on: `01-config-file` (done)

## Resume here
1. Start a fresh Claude Code session and run `/implement-step 02-content-files`. It implements the first unchecked step below and stops.
2. Test that step as described in "How to test", then `git commit`.
3. Repeat until all steps are ticked; the last step sets Status to `done`.

Before step 1: if the code has changed since the planning commit (`git diff 883636b -- meeting_processor/src`), check that "Approach" still matches the code. If it doesn't, update this plan before implementing.

## Goal
`processor.py` contains only code. Note templates (prompt + note layout) live as Markdown files in `meeting_processor/templates/`, projects and default tags in `meeting_processor/projects.toml` — all tracked in git. The hardcoded MoM variant disappears: `config.toml` names the default template, a filename suffix (`Call.<template>.vtt`) overrides it per file, and adding a new note type means adding a `.md` file — no code change. Broken or missing templates stop the run with a clean error.

## Out of scope
- Per-template tag lists or other per-template settings (the template name is the only automatic tag).
- Choosing templates by project, folder, or anything other than config default + filename suffix.
- Option B (code owns the frontmatter instructions). We use option A: the whole prompt is in the template file, plus validation of the contract markers.
- Any change to prompt wording or to the LLM output contract (`{TITLE}`, `project:`, `extra_tags:`) and its regexes. Templates are moved verbatim.
- Shared/inherited template parts, storing templates in the Obsidian vault, hot reload.
- New templates beyond `summary` and `mom`.

## Approach

### Files
```
meeting_processor/
  config.toml / config.example.toml   ← new [template] section
  projects.toml                       ← projects + default tags (new, committed)
  templates/
    summary.md                        ← today's SYSTEM_PROMPT, verbatim
    mom.md                            ← today's SYSTEM_PROMPT_MOM, verbatim
```

`config.example.toml` (and the user's own `config.toml`) gets:
```toml
[template]
active = "summary"   # default template: name of a file in templates/ (without .md)
                     # override per file: "Call.mom.vtt" uses templates/mom.md
```

`projects.toml` — same values as today's `PROJECTS` / `DEFAULT_TAGS`:
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

Template files: content identical to the current string constants (text between the opening `"""` and the closing `"""`, i.e. ending with exactly one newline). Placeholders `{DATE}`, `{AI_NAME}`, `{PROJECT_NAMES}`, `{TITLE}` unchanged.

### Template selection
- Available templates = `*.md` files in `templates/`; template name = file stem.
- Per file: if the transcript's stem has a further suffix (`Path(path.stem).suffix`, e.g. `Call.mom.vtt` → `mom`) **and** a template with that name exists (case-insensitive), use it. Otherwise use `[template] active`. So `Review v1.2.vtt` (suffix `2`) falls back to the default.
- The chosen template name is logged for every file.
- The template name is added as a fixed tag to every note (`mom` → `mom`, `summary` → `summary`). This replaces the hardcoded `["mom"]`; summary notes now also get a `summary` tag (new, intended).

### processor.py
- `Config` gets `template: str` (from `[template] active`); `load_config()` errors if missing/empty ("[template] active is missing — see config.example.toml").
- New path constants `PROJECTS_FILE = PROJECT_DIR / "projects.toml"`, `TEMPLATES_DIR = PROJECT_DIR / "templates"`.
- New frozen dataclass `Content` (`projects: dict`, `default_tags: list[str]`, `templates: dict[str, str]` name → text) and `load_content(config)` reading and validating both. Errors raise the existing `ConfigError` (one-line log + exit 1 in `main()`, before any file is processed or marked processed).
- Validation in `load_content`:
  - `projects.toml` missing or invalid TOML; `default_tags` is a list of strings (may be empty); `projects` has ≥ 1 entry, each with a non-empty string `wikilink` and a `tags` list of strings; no project named `Unknown` (reserved — appended to `{PROJECT_NAMES}` by code).
  - `templates/` exists and contains ≥ 1 `.md` file; every template name matches `^[a-z0-9][a-z0-9_-]*$` (it becomes a tag and a filename suffix).
  - Every template contains all contract markers `{TITLE}`, `{PROJECT_NAMES}`, `project:`, `extra_tags:` — error names the file and missing marker.
  - `config.template` exists → otherwise `Template 'mmo' not found in templates/ — available: mom, summary`.
- New `select_template(path, content, default) -> str` replaces `is_mom_flavor`.
- `main()` calls `load_content(config)` right after `load_config()`; passes `content` down: `process_meeting(file, config, content)` → `build_messages(parsed, config, content, template)` and `resolve_project_and_tags(markdown, content, extra_fixed_tags=[template])`.
- Delete `PROJECTS`, `DEFAULT_TAGS`, `SYSTEM_PROMPT`, `SYSTEM_PROMPT_MOM`, `is_mom_flavor`. Update the module docstring ("FLAVOR DETECTION" → template selection) and the placeholder comment (moves to README).

LLM output contract: unchanged — templates are byte-identical to today's prompts, regexes untouched. Only the merged tag list changes (template name tag).

## Steps
- [x] 1. Create `templates/summary.md`, `templates/mom.md` (verbatim copies of the prompt constants) and `projects.toml` (same values). No code change. Verify with the one-off check in "How to test".
- [x] 2. Add `[template] active = "summary"` to `config.example.toml`; `Config.template` + validation in `load_config()`. Add `PROJECTS_FILE`, `TEMPLATES_DIR`, `Content`, `load_content(config)` with all validation; call it in `main()` with the same `ConfigError` handling. Result not used yet — behavior unchanged. **User:** add the `[template]` section to the local `config.toml`.
- [x] 3. Add `select_template()`; pass `content` + template name through `process_meeting` → `build_messages` / `resolve_project_and_tags`; delete the four constants and `is_mom_flavor`; update docstring and section comments.
- [x] 4. Update `meeting_processor/README.md` (config `[template]`, templates folder + how to add one, placeholder list, filename override replacing the `.mom` sections, projects section, troubleshooting, file tree) and `CLAUDE.md` (Architecture: templates/projects files, `load_content()`, template selection replaces flavor detection, contract markers validated, template-name tag).

## Acceptance criteria
- `processor.py` contains no prompt text, no project names, no tag values, no `mom`.
- `templates/summary.md`, `templates/mom.md`, `projects.toml` are tracked in git.
- With `active = "summary"`: `Call.vtt` → summary note with tag `summary`; `Call.mom.vtt` → MoM note with tag `mom`. With `active = "mom"`: `Call.vtt` → MoM note.
- `Review v1.2.vtt` uses the default template (no error).
- Adding `templates/steering.md` (with all markers) makes `Call.steering.vtt` work and tag `steering` — no code change.
- `active = "mmo"` → run stops with `Template 'mmo' not found in templates/ — available: mom, summary`; nothing processed or marked processed.
- Missing `[template]` section, a template missing `extra_tags:` (or another marker), an invalid template filename (e.g. `My Template.md`), missing/invalid `projects.toml`, a project without `wikilink`, or a project named `Unknown` each give a one-line actionable error.
- Apart from the new template tag, a note generated from the same transcript has the same structure, project wikilink and tags as before (prompt sent to the LLM is identical).

## How to test
Run from `meeting_processor/` with the venv active.
- Step 1:
  ```zsh
  python -c "
  import tomllib; from pathlib import Path
  from meeting_processor import processor as p
  assert Path('templates/summary.md').read_text(encoding='utf-8') == p.SYSTEM_PROMPT
  assert Path('templates/mom.md').read_text(encoding='utf-8') == p.SYSTEM_PROMPT_MOM
  d = tomllib.loads(Path('projects.toml').read_text(encoding='utf-8'))
  assert d['projects'] == p.PROJECTS and d['default_tags'] == p.DEFAULT_TAGS
  print('identical')"
  ```
- Step 2: `meeting-processor "samples/Roadmap Review_2026-04-02.vtt"` → works as before. Then one at a time (restore after each with `git checkout -- projects.toml templates/` or by editing `config.toml` back): `active = "mmo"`; remove the `[template]` section; remove `extra_tags:` from `templates/mom.md`; add `templates/My Template.md`; add `[projects.Unknown]`; rename `projects.toml` → each gives the expected one-line error and processes nothing.
- Step 3 (remove the file's line from `logs/meeting_processor_processed.txt` to re-run):
  - `samples/Roadmap Review_2026-04-02.vtt` → summary note, tags include `summary`.
  - `samples/Steering Committee 2026-06-03.mom.vtt` → MoM structure, tags include `mom`.
  - Set `active = "mom"`, re-run the Roadmap sample → MoM note. Set back to `summary`.
  - Copy `templates/summary.md` to `templates/steering.md`, copy a sample to `… .steering.vtt` → log shows template `steering`, tag `steering`. Delete both afterwards.
- Step 4: read README setup/customization sections as a new user.

## Open questions
- None.
