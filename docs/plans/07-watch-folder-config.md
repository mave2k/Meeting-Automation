# Feature: Watch folder in config.toml + `--dry-run`

Status: ready

Planned against: commit `57c1a65`
Depends on: none (`06-ai-call-resilience` also edits `main()` — see Approach). Interacts with `08-project-context`: 08 adds an AI classification call inside `process_meeting()`. `--dry-run` must not reach `process_meeting()` (as planned below), so the classifier never runs in a dry run — keep it that way.

## Resume here
1. Start a fresh Claude Code session and run `/implement-step 07-watch-folder-config`. It implements the first unchecked step below and stops.
2. Test that step as described in "How to test", then `git commit`.
3. Repeat until all steps are ticked; the last step sets Status to `done`.

Before step 1: if the code has changed since the planning commit (`git diff 57c1a65 -- meeting_processor/src`), check that "Approach" still matches the code. If it doesn't, update this plan before implementing. If `06-ai-call-resilience` was implemented first, `main()` already calls `notify_failure()` — keep those calls when restructuring it.

## Goal
User story: *As user, I want the folder that `meeting-processor` scans without arguments (Mode B) to come from `config.toml`, and to preview what it would process, so that I can catch up on missed exports after an outage without surprises.*

Today `WATCH_FOLDER` is hard-coded to `<repo>/MacWhisper-Exports` (`processor.py:58`). The real exports live in `~/Documents/MacWhisper Files` (the folder the Automator Folder Action is attached to), so Mode B silently finds nothing (`"No new files to process."`). Afterwards the folder is set in `config.toml`; a missing or wrong setting gives a clear error instead of silence; and `meeting-processor --dry-run` lists what would be processed without calling the AI or writing anything.

Context at planning time: 5 of 84 transcripts in `~/Documents/MacWhisper Files` were never processed (no trace in `automator.log` — the Folder Action never fired for them). The first real Mode B run would send exactly these to the AI. `--dry-run` exists so this is visible before it happens.

## Out of scope
- Automator setup: the Folder Action's watched folder is still set in Automator; the code cannot read it. `[watch] folder` must be kept equal to it by hand (documented).
- A launchd agent for Mode B (still not set up; Mode B is run manually).
- Why the Folder Action missed those 5 files.
- Recursive scanning of sub-folders.
- Changing Mode A behaviour (apart from honouring `--dry-run`).
- Exit code of the Automator wrapper.

## Approach
**`config.toml` / `config.example.toml`** — new optional section:
```toml
[watch]
folder = "~/Projects/Meeting Automation/MacWhisper-Exports"   # same folder the Automator Folder Action watches;
                                                              # only used when meeting-processor runs without a file path
```
The example keeps the documented default path; the user's own `config.toml` gets `~/Documents/MacWhisper Files`.

**`processor.py` — config:**
- `Config` gets `watch_folder: Path | None`.
- `load_config()`: `[watch] folder` is optional. Missing → `None`. Present → must be a non-empty string and not a `YOUR_…` placeholder (else `ConfigError`); `expanduser()`; a relative path is anchored to `PROJECT_DIR` (like all other paths). Existence is **not** checked here — Mode A must keep working even if the folder is wrong.
- Remove the `WATCH_FOLDER` constant and its comment.

**`processor.py` — `main()`:**
- Parse arguments with stdlib `argparse` (no new dependency): positional `files` (`nargs="*"`), flag `--dry-run`. Gives `--help` for free. Paths are used exactly as before (`Path(arg)`).
- Mode B: new helper `_watch_folder(config) -> Path` raises `ConfigError` when `config.watch_folder` is `None` (`"[watch] folder is not set in config.toml — needed when running without a file path"`) or not an existing directory (`"[watch] folder '<path>' does not exist"`). It is called inside the existing `try … except ConfigError` in `main()`, so the handling is the same as for other config errors: one log line, exit 1 (plus `notify_failure()` if plan 06 is in place). Only called when no files are given — Mode A never touches it.
- `--dry-run`, both modes: run the same filtering as a real run (unsupported → skipped, already processed → skipped), then log one line per candidate — `[dry-run] Would process: <name> (template: <name>)` — plus a closing count, and return. No `time.sleep`, no `parse_transcript`, no AI call, no Obsidian write, no `mark_processed`. Mode B with nothing to do still logs `No new files to process.`
- Keep the per-file processing loop as it is (incl. the 2 s sleep and the `except Exception` per file); only the candidate selection and the dry-run branch are added. A small shared helper for "process these candidates or list them" is fine if it keeps Mode A and B from duplicating the dry-run branch.

LLM output contract: unchanged.

## Steps
- [ ] 1. `argparse` + `--dry-run` in `main()` for both modes (Mode B still on the old `WATCH_FOLDER`).
- [ ] 2. `[watch] folder` in `load_config()` / `Config` / `config.example.toml`; Mode B uses it via `_watch_folder()` with the two `ConfigError`s; remove `WATCH_FOLDER`.
- [ ] 3. Docs: `README.md` (repo root) — Setup step 3 (new `[watch] folder`, must match the Automator folder), step 4 (watched folder is free to choose, keep it in sync with `config.toml`), a short "Catch up on missed exports" section (`meeting-processor --dry-run`, then `meeting-processor`), Troubleshooting rows for the two new config errors. `meeting_processor/automator/folder-action.sh` header comment (watched folder = `[watch] folder`; remind to re-paste only if the script itself changed — a comment-only change needs no re-paste). `CLAUDE.md` — Commands (Mode B scans `[watch] folder`; `--dry-run`) and the config description in Architecture. Set Status to `done` and update the plan index.

## Acceptance criteria
- `meeting-processor --help` shows the positional files and `--dry-run`.
- `meeting-processor --dry-run "<sample>.vtt"` logs `[dry-run] Would process: … (template: summary)`; no AI request in the log; no note in Obsidian; `processed.txt` unchanged. With `"<sample>.mom.vtt"` the template shown is `mom`; with an already processed file it logs the usual "Already processed, skipping".
- Without `[watch] folder` in `config.toml`: `meeting-processor` (and `--dry-run`) → `Config error: [watch] folder is not set …`, exit 1. Mode A with a file path still works.
- With `[watch] folder` pointing to a non-existent path → `Config error: [watch] folder '…' does not exist`, exit 1.
- With `[watch] folder = "~/Documents/MacWhisper Files"`: `meeting-processor --dry-run` lists exactly the unprocessed supported files (at planning time: the 5 files from Aug–Oct 2026), sorted, plus the count; nothing is sent or written.
- Without `--dry-run`, Mode B processes those files exactly as before (same per-file logging, error handling and `mark_processed`).
- A `[watch] folder` value of `"YOUR_…"` or `""` → `ConfigError` at startup (like other placeholders).

## How to test
Work in `meeting_processor/` with the venv active. **Never run plain `meeting-processor` (without `--dry-run` and without a path) during steps 1–2** — it would process the unprocessed real exports.

- **Step 1:** `meeting-processor --help`. `meeting-processor --dry-run "samples/Roadmap Review_2026-04-02.vtt"` (remove its line from `logs/meeting_processor_processed.txt` first if present) → "Would process" line, no `HTTP Request` line in the log, nothing in Obsidian. Same with a file that is in `processed.txt` → "Already processed". `meeting-processor --dry-run` (Mode B, old folder missing) → `No new files to process.`
- **Step 2:** without `[watch]` in `config.toml` → `meeting-processor --dry-run` gives the "not set" error, exit 1 (`echo $?`); `meeting-processor "samples/…vtt" --dry-run` still works. Set `folder = "~/does-not-exist"` → "does not exist" error. Set `folder = "~/Documents/MacWhisper Files"` → `meeting-processor --dry-run` lists the unprocessed files. Then decide about them: either run `meeting-processor` to create the notes, or add their paths to `processed.txt` to skip them.
- **Step 3:** read the README sections, the script comment and the CLAUDE.md change.

## Open questions
- none
