# Feature: Config file for API keys and models

Status: done

## Goal
All secrets and machine-specific settings (AI provider, model per provider, API keys, Obsidian host/key/folder) live in a single `meeting_processor/config.toml` that is ignored by git. The code no longer contains any keys; a committed `config.example.toml` documents the setup. Switching provider or model means editing only that file.

## Out of scope
- Moving `PROJECTS`, `DEFAULT_TAGS` or the prompts out of the code (planned later as a separate prompt/template management feature; prompts will then likely live in their own `.md` files, not in `config.toml`).
- Rewriting git history to remove the old keys (repo is private; rotating the keys makes the old ones worthless).
- Rotating the keys — done manually by the user (Gemini key in Google AI Studio, Obsidian key in the Local REST API plugin settings). Do this before or during step 1.
- Environment variables, `.env` files, macOS Keychain. (Env vars from `~/.zshrc` are not available when Automator runs the script — a file at a fixed path is reliable.)
- Any change to the LLM output contract (frontmatter, `{TITLE}`, `project:`, `extra_tags:`) — prompts and regexes are untouched.

## Approach
Format: TOML, read with the stdlib `tomllib` (Python ≥ 3.11) — no new dependency, supports comments and sections.

- **New `meeting_processor/config.example.toml`** (committed), placeholder values:
  ```toml
  [ai]
  provider = "gemini"            # gemini | claude | openai

  [gemini]
  api_key = "YOUR_GEMINI_API_KEY"
  model   = "gemini-2.5-flash"

  [claude]
  api_key = "YOUR_ANTHROPIC_API_KEY"
  model   = "claude-opus-4-5"

  [openai]
  api_key = "YOUR_OPENAI_API_KEY"
  model   = "gpt-4o"

  [obsidian]
  host    = "https://127.0.0.1:27124"
  api_key = "YOUR_OBSIDIAN_API_KEY"
  folder  = "+/Meetings"
  ```
- **`.gitignore`**: add `meeting_processor/config.toml`.
- **`pyproject.toml`**: `requires-python = ">=3.11"` (first version with `tomllib`).
- **`processor.py`**:
  - New `load_config()` reads `PROJECT_DIR / "config.toml"` via `tomllib` and returns a small settings object (e.g. a dataclass: `provider`, `api_key`, `model` of the selected provider, `obsidian_host`, `obsidian_api_key`, `obsidian_folder`). Called from `main()` next to `setup_logging()`, not at import time.
  - Validation (clear one-line error in the log, exit non-zero, no traceback, no API call):
    - file missing → "config.toml not found — copy config.example.toml to config.toml and fill it in"
    - invalid TOML → error with the parse message
    - `provider` not one of `gemini | claude | openai`
    - selected provider's `api_key` or `model` empty or still a `YOUR_…` placeholder
    - Obsidian `api_key` empty / placeholder
    - Unselected providers may be empty or placeholders.
  - `call_ai`, the AI-name logic in `build_messages`, and `write_to_obsidian` use the loaded config (passed in as a parameter) instead of module constants.
  - Delete the constants `AI_PROVIDER`, `*_API_KEY`, `*_MODEL`, `OBSIDIAN_HOST`, `OBSIDIAN_API_KEY`, `OBSIDIAN_FOLDER`. `PROJECTS`, `DEFAULT_TAGS`, prompts, paths stay.
  - Update the module docstring (it still says "sends the transcript to Google Gemini").
- **Docs**: `meeting_processor/README.md` (setup: copy example → fill in; model table points at `config.toml`; troubleshooting entries) and `CLAUDE.md` (remove "there is no config file or env-var loading", describe `config.toml`).

## Steps
- [x] 1. Add `config.example.toml`, the `.gitignore` entry and the `requires-python` bump. User copies it to `config.toml` and fills in the **new** (rotated) keys. No code change.
- [x] 2. Add `load_config()` with validation and call it in `main()` (result not yet used). Existing behavior unchanged.
- [x] 3. Switch `call_ai`, the AI-name logic and `write_to_obsidian` to the config; delete the hardcoded key/model/Obsidian constants; update the module docstring.
- [x] 4. Update `meeting_processor/README.md` and `CLAUDE.md`.

## Acceptance criteria
- `grep -rn "API_KEY" meeting_processor/src` shows no key values (only config field access).
- `git status` never lists `meeting_processor/config.toml`; `config.example.toml` is tracked.
- With `provider = "gemini"` and only Gemini key + model filled in (other providers left as placeholders), an export is processed and the note appears in Obsidian.
- Changing `provider` to `claude` (with its key filled in) works without touching code; the note's AI name shows the configured Claude model.
- Missing `config.toml`, invalid TOML, unknown provider, or missing key/model for the selected provider each stop the run with a one-line, actionable log message — no traceback, no API call, nothing written to Obsidian, file not marked as processed.

## How to test
- Step 1: `git status` — `config.toml` must not appear; `git check-ignore -v meeting_processor/config.toml` shows the rule.
- Step 2: temporarily rename `config.toml` → run `meeting-processor` → expect the "not found" error. Restore; then try `provider = "foo"` and an empty Gemini key → expect the matching errors. With a correct file, run `meeting-processor "../MacWhisper-Exports/<export>.json"` → works as before.
- Step 3: run `meeting-processor "../MacWhisper-Exports/<export>.json"` (remove its line from `logs/meeting_processor_processed.txt` first if needed) → note appears in `+/Meetings` in Obsidian with correct title, project link and tags. Optionally switch provider and repeat.
- Step 4: read through the README setup section on a "fresh install" basis.

## Open questions
- None.
