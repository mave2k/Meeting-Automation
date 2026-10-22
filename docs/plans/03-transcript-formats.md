# Feature: Transcript-agnostic input (.json, .vtt, .srt, .txt)

Status: done

Planned against: commit `d35c1f3`
Depends on: none (independent of `02-content-files`; either can be implemented first — they only touch neighboring lines in `main()` and `process_meeting()`)

## Resume here
1. Start a fresh Claude Code session and run `/implement-step 03-transcript-formats`. It implements the first unchecked step below and stops.
2. Test that step as described in "How to test", then `git commit`.
3. Repeat until all steps are ticked; the last step sets Status to `done`.

Before step 1: check `git diff d35c1f3 -- meeting_processor/src`. If `02-content-files` (or anything else) was implemented in the meantime, `process_meeting()` may take an extra `content` parameter — that doesn't affect this plan, but make sure "Approach" still matches the code (`main()`, `process_meeting()`, `is_mom_flavor()`). If it doesn't, update this plan before implementing.

## Goal
The processor accepts MacWhisper `.json`, `.vtt` (MS Teams, Zoom, …), `.srt` and `.txt` transcripts. Every format is normalized into the same plain `Speaker: text` transcript that is sent to the LLM, so prompts and post-processing stay unchanged. The Minutes-of-Meeting flavor works for every format via `….mom.<ext>`.

## Out of scope
- `.docx` (Teams' default transcript download — would need `python-docx`).
- Speaker detection or any parsing of `.txt` — it is passed through as-is.
- Normalizing or mapping speaker names (e.g. `Lindgren Eva (ACME/OPS)` stays verbatim) — separate feature.
- Renaming the `MacWhisper-Exports` watch folder.
- Prompt changes, new note types / flavors beyond `.mom`.
- The real sample in `examples/` is never committed or copied into the repo; it is only used locally for testing.

## Approach
**New module `meeting_processor/src/meeting_processor/transcripts.py`:**
- One reader per format, registered in a dict `{".json": …, ".vtt": …, ".srt": …, ".txt": …}`; `SUPPORTED_EXTENSIONS` is derived from its keys.
- `parse_transcript(path) -> dict` dispatches on `path.suffix.lower()` and returns the same shape as today: `{date_display, date_prefix, transcript}`. Unsupported extension → `ValueError`.
- `parse_macwhisper_json` moves here from `processor.py` (logic unchanged except the shared date fallback).
- **Date fallback chain** (one shared helper): metadata date (JSON only) → first valid `YYYY-MM-DD` anywhere in the filename (e.g. `MDM Core Team Sync_2026-09-17.vtt`) → file mtime with a warning.
- **Text decoding** for `.vtt`/`.srt`/`.txt`: `utf-8-sig` (handles BOM), fallback `cp1252`.
- **`.vtt` reader:**
  - Skip the `WEBVTT` header, `NOTE`/`STYLE`/`REGION` blocks, cue identifier lines (e.g. `44670616-…/6-0`), and timestamp lines (`--> `).
  - Speaker from `<v Name>…</v>` (Teams; closing `</v>` optional) or a leading `Name: ` prefix (Zoom). No speaker → `Unknown`.
  - Strip remaining tags (`<c>`, `<i>`, `<b>`, timestamps tags, …); join a cue's lines with a space.
  - Merge consecutive cues of the same speaker into one `Speaker: text` line.
  - Keep file order — do not sort by timestamp (Teams emits overlapping cues out of order; sorting would split merged statements).
- **`.srt` reader:** blocks of index line, timestamp line (`-->`), text lines. Same tag stripping, `Name: ` prefix detection, line joining and same-speaker merging as `.vtt`.
- **`.txt` reader:** decoded text passed through unchanged (stripped).
- Empty result → existing `ValueError("No transcript content found …")`.

**`processor.py`:**
- `process_meeting` calls `parse_transcript` instead of `parse_macwhisper_json`.
- Mode A: accept files whose suffix is in `SUPPORTED_EXTENSIONS`; otherwise log `Skipping unsupported file type: <name>` (not marked processed).
- Mode B: scan `WATCH_FOLDER` for all supported extensions (sorted).
- `is_mom_flavor`: `filepath.stem.lower().endswith(".mom")` (works for `Call.mom.json`, `Call.mom.vtt`, …).
- Update the module docstring (flavor detection examples, supported formats).

**Manual user action (outside the repo):** in the Automator Folder Action script, remove the `if [[ "$f" == *.json ]]` filter so every file is passed through and the processor decides (see README step 4 after step 5 of this plan).

LLM output contract: unchanged — prompts, `{TITLE}`, `project:`, `extra_tags:` and regexes are not touched.

## Steps
- [x] 1. Add `examples/` to `.gitignore`. Create synthetic sample files (invented names and content only) in `meeting_processor/samples/`, each with a date in the filename: MacWhisper `.json`, Teams-style `.vtt` (cue IDs, multi-line cues, a sentence split over several cues, an overlapping out-of-order cue), Zoom-style `.vtt` (`Name: text`), `.srt`, `.txt`, plus one `….mom.vtt`. No code change.
- [x] 2. Create `transcripts.py` with `parse_transcript`, the reader registry, `SUPPORTED_EXTENSIONS`, the shared date fallback (incl. filename date) and the moved JSON reader. Wire `processor.py` to it (Mode A/B use `SUPPORTED_EXTENSIONS`, generic `is_mom_flavor`, skip log). Only `.json` registered yet — behavior unchanged for JSON.
- [x] 3. Add the `.vtt` reader and register it.
- [x] 4. Add the `.srt` and `.txt` readers and register them.
- [x] 5. Update `meeting_processor/README.md` (supported formats, `.mom.<ext>` convention, date determination, Automator script without the `*.json` filter, troubleshooting) and `CLAUDE.md` (pipeline step 2 now `parse_transcript` in `transcripts.py`). Then the user updates the Automator script by hand.

## Acceptance criteria
- `examples/` does not appear in `git status`; `meeting_processor/samples/` is tracked and contains no real data.
- Given `examples/MDM Core Team Sync_2026-09-17.vtt`, the transcript has one line per speaker turn, e.g. `Lindgren Eva (ACME/OPS): Okay, there we go. So I have several information, but this will spread as soon as Martin is there. …`, with no cue IDs, timestamps or `<v>` tags, and the note date is 2026-09-17.
- A MacWhisper `.json` export produces the same note as before (date from metadata).
- `….mom.vtt` uses the MoM prompt and adds the `mom` tag.
- `.srt` and `.txt` samples each produce a note in Obsidian.
- An unsupported file (e.g. `.pdf`) passed in Mode A is logged as skipped and not marked processed; Mode B ignores it.
- A file with no metadata and no date in the filename falls back to mtime with a warning.

## How to test
Run from `meeting_processor/` with the venv active. Parser-only checks (no LLM call):
```zsh
python -c "
from pathlib import Path; from meeting_processor.transcripts import parse_transcript
r = parse_transcript(Path('../examples/MDM Core Team Sync_2026-09-17.vtt'))
print(r['date_prefix']); print(r['transcript'][:800])"
```
- Step 1: `git status` — `examples/` absent, `samples/` listed as new. Open the samples and check they cover the cases listed in step 1.
- Step 2: run the parser check on `samples/<…>.json` → same output as before; `meeting-processor samples/<…>.pdf` (or any non-supported file) → "Skipping unsupported file type". Full run on a real MacWhisper export → note unchanged.
- Step 3: parser check on the real Teams `.vtt` and the Zoom sample → merged speaker turns, no IDs/timestamps/tags, date from filename. Full run: `meeting-processor "../examples/MDM Core Team Sync_2026-09-17.vtt"` → note in `+/Meetings`; then a `.mom.vtt` sample → MoM structure + `mom` tag.
- Step 4: parser check + full run on the `.srt` and `.txt` samples.
- Step 5: after updating the Automator script, drop a `.vtt` into `MacWhisper-Exports/` and check `logs/automator.log` and Obsidian.

To re-run a file, remove its line from `logs/meeting_processor_processed.txt`.

## Open questions
- None.
