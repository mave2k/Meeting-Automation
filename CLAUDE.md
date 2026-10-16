# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-script macOS automation: MacWhisper JSON transcript export → LLM (Gemini / Claude / OpenAI) → structured Markdown note written into an Obsidian vault via the Obsidian Local REST API plugin. Triggered by an Automator Folder Action watching `MacWhisper-Exports/` (sibling of `meeting_processor/`). Full setup and troubleshooting live in [meeting_processor/README.md](meeting_processor/README.md).

`Pauls Whisper Script.py` at the repo root is an unrelated reference script (reads `.whisper` ZIP files, calls the Anthropic API via raw `urllib`, creates a Bear note). It is not part of the pipeline.

## Commands

There is no build, lint, or test suite.

```zsh
cd meeting_processor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Process a specific export (Mode A — what Automator does)
python3 meeting_processor.py "../MacWhisper-Exports/<export>.json"

# Scan WATCH_FOLDER for unprocessed *.json files (Mode B — launchd style)
python3 meeting_processor.py
```

Running it calls a real LLM API and writes to the live Obsidian vault (Obsidian must be running). Already-processed paths are recorded in `logs/meeting_processor_processed.txt` and skipped — remove the line to re-run a file. Logs go to `logs/meeting_processor.log` and stdout.

## Architecture (meeting_processor/meeting_processor.py)

All configuration is module-level constants at the top of the file (provider, API keys, models, Obsidian host/folder, `PROJECTS`, `DEFAULT_TAGS`) — there is no config file or env-var loading. Paths are anchored to `SCRIPT_DIR`.

Pipeline in `process_meeting()`:

1. **Flavor detection** — filename ending `.mom.json` selects `SYSTEM_PROMPT_MOM` (formal, exhaustive, attributed Minutes of Meeting, adds `mom` tag); otherwise `SYSTEM_PROMPT` (concise personal note).
2. **`parse_macwhisper_json`** — handles two MacWhisper shapes: a wrapped dict with `segments` + date metadata, or a bare array of segments (no date → falls back to file mtime). Produces `Speaker: text` lines.
3. **`build_messages`** — injects `{DATE}`, `{AI_NAME}`, `{PROJECT_NAMES}` into the prompt via `str.replace` (not `.format`, so braces in prompts are safe).
4. **`call_ai`** — dispatches on `AI_PROVIDER`; SDKs are imported lazily so only the chosen one must be installed. Gemini uses the new `google-genai` SDK (`from google import genai`), not `google-generativeai`.
5. **Post-processing contract with the LLM output** — the prompts instruct the model to:
   - leave `{TITLE}` literally in frontmatter; Python extracts the first `# H1` and injects it (`extract_title_from_h1`, `inject_title`). The H1 also becomes the note filename.
   - emit `project: "<PROJECTS key>"` and an `extra_tags:` list; `resolve_project_and_tags` regex-rewrites these into `project: "[[wikilink]]"` and a merged, deduplicated `tags:` block (project tags + `DEFAULT_TAGS` + flavor tags + AI tags).

   These regexes depend on the exact frontmatter shape in the prompts — if you change field names or layout in either `SYSTEM_PROMPT*`, update the corresponding regexes (and vice versa). Both prompts must stay structurally in sync for these fields.
6. **`write_to_obsidian`** — `PUT {OBSIDIAN_HOST}/vault/{OBSIDIAN_FOLDER}/{filename}` with `verify=False` (localhost self-signed cert). Overwrites an existing note with the same title.

Prompts and generated notes follow the meeting's language (German or English).

## Documentation lookups

When you need library or API documentation (e.g. `google-genai`, `anthropic`, `openai`, `requests`, Obsidian Local REST API), use the Context7 MCP server to fetch current docs instead of relying on memory — these SDKs change frequently.
