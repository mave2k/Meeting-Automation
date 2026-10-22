# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A small macOS automation: meeting transcript (MacWhisper `.json`, Teams/Zoom `.vtt`, `.srt`, `.txt`) → LLM (Gemini / Claude / OpenAI) → structured Markdown note written into an Obsidian vault via the Obsidian Local REST API plugin. Triggered by an Automator Folder Action watching `MacWhisper-Exports/` (sibling of `meeting_processor/`). Full setup and troubleshooting live in [meeting_processor/README.md](meeting_processor/README.md).

`Pauls Whisper Script.py` at the repo root is an unrelated reference script (reads `.whisper` ZIP files, calls the Anthropic API via raw `urllib`, creates a Bear note). It is not part of the pipeline.

## Workflow: plan first, then implement

Feature work is split into two separate sessions. The plan file is the contract between them — not the chat history.

- **Plans live in `docs/plans/`**, one file per feature, named `NN-<feature>.md` (e.g. `01-speaker-mapping.md`), created from [docs/plans/_template.md](docs/plans/_template.md).
- **Planning sessions** (Plan Mode, or `/plan-feature`): read and analyze code, ask questions, challenge the approach. Do not modify code — the only files you may write are in `docs/plans/`.
- **Implementation sessions** (fresh context, or `/implement-step`): read the plan file first. Implement **one step at a time**, then tick its checkbox in the plan and stop so the user can test and commit. Do not continue to the next step unprompted.
- **When reality differs from the plan** (a step is wrong, scope grows, an open question blocks): stop and propose a plan update instead of improvising in the code. Continue only after the plan file is updated.
- Respect the plan's "Out of scope" section.
- Ask before adding new dependencies to `requirements.txt`.

## Commands

There is no build, lint, or test suite.

```zsh
cd meeting_processor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .          # src layout: installs the `meeting-processor` command

# Process a specific transcript (Mode A — what Automator does)
meeting-processor "../MacWhisper-Exports/<export>.json"
meeting-processor "samples/Roadmap Review_2026-04-02.vtt"   # synthetic samples, one per format

# Scan WATCH_FOLDER for unprocessed supported files (Mode B — launchd style)
meeting-processor         # equivalent: python -m meeting_processor
```

Running it calls a real LLM API and writes to the live Obsidian vault (Obsidian must be running). Already-processed paths are recorded in `logs/meeting_processor_processed.txt` and skipped — remove the line to re-run a file. Logs go to `logs/meeting_processor.log` and stdout.

## Architecture (meeting_processor/src/meeting_processor/processor.py)

Secrets and machine-specific settings (AI provider, API key + model per provider, Obsidian host/key/folder) live in `meeting_processor/config.toml` (git-ignored; template `config.example.toml`). `load_config()` reads it with stdlib `tomllib` (hence Python ≥ 3.11), validates it, and returns a frozen `Config` dataclass for the selected provider; `main()` calls it once and passes `config` down through `process_meeting` → `build_messages` / `call_ai` / `write_to_obsidian`. Invalid config raises `ConfigError` → one-line log + exit 1, before any file is processed. No env vars or `.env` (Automator doesn't see `~/.zshrc`). `PROJECTS`, `DEFAULT_TAGS` and the prompts stay module-level constants in `processor.py`. Paths are anchored to `PROJECT_DIR` (the `meeting_processor/` folder, two levels above `processor.py`), which is why an editable install (`pip install -e .`) is required. Logging is configured in `setup_logging()`, called from `main()`, not at import time. Entry points: the `meeting-processor` console script (`pyproject.toml`) and `__main__.py`.

Pipeline in `process_meeting()`:

1. **Flavor detection** — `.mom` right before the extension (`Call.mom.json`, `Call.mom.vtt`, …) selects `SYSTEM_PROMPT_MOM` (formal, exhaustive, attributed Minutes of Meeting, adds `mom` tag); otherwise `SYSTEM_PROMPT` (concise personal note).
2. **`parse_transcript`** (in `transcripts.py`) — dispatches on the file extension via the `READERS` registry (`SUPPORTED_EXTENSIONS` is derived from it; Mode A skips other files with a log line, Mode B ignores them). Every reader returns `{date_display, date_prefix, transcript}` with a plain `Speaker: text` transcript, so everything downstream is format-agnostic.
   - `.json` — `parse_macwhisper_json`: a wrapped dict with `segments` + date metadata, or a bare array of segments.
   - `.vtt` / `.srt` — shared cue helpers: drop headers/`NOTE` blocks, cue IDs and timestamp lines; speaker from `<v Name>` (Teams) or a leading `Name: ` (Zoom/SRT), else `Unknown`; strip tags; merge consecutive same-speaker cues. File order is kept on purpose (Teams emits overlapping cues out of order — sorting by timestamp would split statements).
   - `.txt` — passed through unchanged.
   - Text files are decoded `utf-8-sig`, falling back to `cp1252`.
   - Date fallback chain (`resolve_date`): metadata (JSON only) → first valid `YYYY-MM-DD` in the filename → file mtime with a warning.

   To add a format, write a reader returning that dict and register it in `READERS`. Real transcripts for local testing go in `examples/` (git-ignored, never commit); `meeting_processor/samples/` holds synthetic ones.
3. **`build_messages`** — injects `{DATE}`, `{AI_NAME}` (provider + configured model), `{PROJECT_NAMES}` into the prompt via `str.replace` (not `.format`, so braces in prompts are safe).
4. **`call_ai`** — dispatches on `config.provider`; SDKs are imported lazily so only the chosen one must be installed. Gemini uses the new `google-genai` SDK (`from google import genai`), not `google-generativeai`.
5. **Post-processing contract with the LLM output** — the prompts instruct the model to:
   - leave `{TITLE}` literally in frontmatter; Python extracts the first `# H1` and injects it (`extract_title_from_h1`, `inject_title`). The H1 also becomes the note filename.
   - emit `project: "<PROJECTS key>"` and an `extra_tags:` list; `resolve_project_and_tags` regex-rewrites these into `project: "[[wikilink]]"` and a merged, deduplicated `tags:` block (project tags + `DEFAULT_TAGS` + flavor tags + AI tags).

   These regexes depend on the exact frontmatter shape in the prompts — if you change field names or layout in either `SYSTEM_PROMPT*`, update the corresponding regexes (and vice versa). Both prompts must stay structurally in sync for these fields.
6. **`write_to_obsidian`** — `PUT {config.obsidian_host}/vault/{config.obsidian_folder}/{filename}` with `verify=False` (localhost self-signed cert). Overwrites an existing note with the same title.

Prompts and generated notes follow the meeting's language (German or English).

## Documentation lookups

When you need library or API documentation (e.g. `google-genai`, `anthropic`, `openai`, `requests`, Obsidian Local REST API), use the Context7 MCP server to fetch current docs instead of relying on memory — these SDKs change frequently.
