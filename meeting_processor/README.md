# Meeting Processor

Automatically turns meeting transcripts (MacWhisper, MS Teams, Zoom, …) into structured Obsidian notes using an AI model of your choice.

**Workflow:**
Record → Transcribe (MacWhisper) or download the transcript (Teams, Zoom) → Save it to the watch folder → Folder watcher triggers script → Formatted note appears in Obsidian

---

## Requirements

- macOS (Automator is required for the folder watcher)
- [MacWhisper](https://goodsnooze.gumroad.com/l/macwhisper) (Pro recommended for speaker recognition)
- [Obsidian](https://obsidian.md) with the [Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) plugin installed and enabled
- Python 3.11 or newer (check with `python3 --version`)
- An API key for at least one supported AI provider (see below)

---

## Supported AI Providers

| Provider | `provider` value | Section in `config.toml` | Get an API key |
|---|---|---|---|
| Google Gemini | `gemini` | `[gemini]` | [aistudio.google.com](https://aistudio.google.com) |
| Anthropic Claude | `claude` | `[claude]` | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI GPT | `openai` | `[openai]` | [platform.openai.com](https://platform.openai.com) |

The model for each provider is set by `model` in its section.

Only the SDK for your chosen provider needs to be installed.

---

## Supported Transcript Formats

| Extension | Typical source | How it is read |
|---|---|---|
| `.json` | MacWhisper export | Speaker + text per segment; date from the export's metadata if present |
| `.vtt` | MS Teams, Zoom, other meeting tools | Speaker from `<v Name>` (Teams) or a leading `Name: ` (Zoom); timestamps, cue IDs and formatting tags are removed; consecutive cues of the same speaker are merged into one turn |
| `.srt` | Subtitle exports | Same as `.vtt` (speaker from a leading `Name: `) |
| `.txt` | Any plain-text transcript or notes | Passed to the AI unchanged — no speaker detection |

Every format is turned into the same `Speaker: text` transcript before it goes to the AI, so the note looks the same regardless of the source. Other file types (e.g. `.pdf`, `.docx`) are skipped with `Skipping unsupported file type` in the log. MS Teams' default `.docx` download is not supported — download the transcript as `.vtt` instead.

**Meeting date** — determined in this order:
1. The date in the file's metadata (MacWhisper `.json` with metadata only)
2. The first `YYYY-MM-DD` date anywhere in the filename, e.g. `MDM Core Team Sync_2026-09-17.vtt` or `2026-05-12 Client Call.srt`
3. The file's modification time (logged as a warning)

Teams and Zoom transcripts carry no date inside the file, so put the date in the filename.

**Minutes of Meeting** — add `.mom` right before the extension to get formal, exhaustive Minutes of Meeting (with the `mom` tag) instead of the concise standard note. Works for every format: `Client Call.mom.json`, `Steering Committee 2026-06-03.mom.vtt`, `Vendor Call.mom.srt`, `Notes.mom.txt`.

**Samples** — `meeting_processor/samples/` contains one synthetic file per format (invented names and content) for trying things out without a real meeting.

---

## Setup

### 1. Create the folder structure

```zsh
mkdir -p ~/Projects/Meeting\ Automation/meeting_processor/logs
mkdir -p ~/Projects/Meeting\ Automation/MacWhisper-Exports
```

The code lives in:
```
~/Projects/Meeting Automation/meeting_processor/src/meeting_processor/processor.py
```

### 2. Create a Python virtual environment

```zsh
cd ~/Projects/Meeting\ Automation/meeting_processor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .          # installs the `meeting-processor` command (editable)
```

**What these commands do**

- **`.venv`** is a private toolbox for this project: its own Python plus its own libraries, isolated from the rest of your Mac. This is why Automator calls `.venv/bin/meeting-processor` instead of a system Python.
- **`pip install -r requirements.txt`** installs the libraries the script depends on (Gemini/Claude/OpenAI SDKs, `requests`) into the toolbox.
- **`pip install -e .`** registers this project's own code in the toolbox. It reads `pyproject.toml`, which tells pip the project name, where the code is (`src/`), where to take the dependency list from (`requirements.txt`) and which command to create (`meeting-processor`, which runs `main()` in `processor.py`). "Editable" (`-e`) means the toolbox points at your source files instead of copying them, so edits to `processor.py` take effect immediately.

You only need to rerun the install commands if you recreate the `.venv`, add a library to `requirements.txt` (rerun the first), or change the entry point in `pyproject.toml` (rerun the second).

### 3. Configure the script

**API keys, provider, models, Obsidian** — these live in `config.toml`, which is ignored by git so your keys never get committed. Create it from the template:

```zsh
cd ~/Projects/Meeting\ Automation/meeting_processor
cp config.example.toml config.toml
```

Then open `config.toml` and fill in:

```toml
[ai]
provider = "gemini"            # gemini | claude | openai

[gemini]
api_key = "your-key-here"
model   = "gemini-2.5-flash"   # change model here if needed

[obsidian]
host    = "https://127.0.0.1:27124"
api_key = "your-key-here"      # Obsidian → Settings → Local REST API
folder  = "+/Meetings"         # folder path inside your vault
```

Only the selected provider needs a key and model; the sections of the other providers may keep their `YOUR_…` placeholders. The script checks the file on every run and stops with a one-line `Config error: …` in the log if something is missing — before calling the AI or writing to Obsidian.

**Projects** — these stay in the code. Open `src/meeting_processor/processor.py` and add one entry per project you want to track:
```python
PROJECTS = {
    "Halvorsen": {
        "wikilink": "Halvorsen MOC",   # exact Obsidian note name for [[...]]
        "tags":     ["halvorsen"],     # tags always applied for this project
    },
    "Internal": {
        "wikilink": "Internal",
        "tags":     ["internal"],
    },
}
DEFAULT_TAGS = ["meeting"]          # tags added to every note
```

The AI picks the project name from your `PROJECTS` keys based on the transcript content. It also generates 2–4 additional content-specific tags automatically. All tags are merged and deduplicated before writing to Obsidian.

**System prompt** — the `SYSTEM_PROMPT` constant controls the Obsidian note format. Edit the structure there if you want to change heading names, add fields, or adjust the frontmatter layout.

### 4. Set up the Automator Folder Action

1. Open **Automator** → New Document → **Folder Action**
2. Set the watched folder to `~/Projects/Meeting Automation/MacWhisper-Exports`
3. Add a **Run Shell Script** action with Shell: `/bin/zsh` and Pass input: `as arguments`
4. Paste the contents of [`automator/folder-action.sh`](automator/folder-action.sh) (a reference copy — Automator does not read the file, so paste it again after changing it)

Every file dropped into the folder is passed on; the script itself decides whether the format is supported and skips the rest. Hidden files (`.DS_Store`) and folders are ignored. Each run is logged to `logs/automator.log` with its exit code. (Older versions of this script had an `if [[ "$f" == *.json ]]` filter — replace it with the current version, otherwise `.vtt`, `.srt` and `.txt` files never reach the script.)

5. Save the Folder Action

To verify it is active: right-click the watch folder in Finder → **Services → Folder Actions Setup** → confirm the action is listed and checked.

### 5. Test manually

Before relying on the Automator trigger, run the script directly on a real export:

```zsh
cd ~/Projects/Meeting\ Automation/meeting_processor
source .venv/bin/activate
meeting-processor ~/Projects/Meeting\ Automation/MacWhisper-Exports/your-export.json
```

A new note should appear in your Obsidian vault within a few seconds. Check `logs/meeting_processor.log` for detailed output.

---

## Daily Workflow

**MacWhisper**
1. Record the meeting in MacWhisper with speaker recognition enabled
2. When done: **Export** in MacWhisper with these settings:
   - Style: `Transcript`
   - Format: `.json`
   - Grouping: `People`
3. Save the export to `~/Projects/Meeting Automation/MacWhisper-Exports/`
4. The Automator action fires automatically → note appears in Obsidian in ~30 seconds

**MS Teams / Zoom**
1. Download the meeting transcript as `.vtt` (Teams: in the meeting's Recap → Transcript → Download → `.vtt`; Zoom: the cloud recording's transcript file)
2. Make sure the filename contains the meeting date as `YYYY-MM-DD` (Teams usually adds it; otherwise rename) — add `.mom` before `.vtt` for Minutes of Meeting
3. Save it to `~/Projects/Meeting Automation/MacWhisper-Exports/` — the rest is the same

---

## Output Format

Each generated note contains:

- **Frontmatter** — `title`, `type`, `date`, `participants`, `project` (wikilink), `summary`, `tags`
- **TL;DR** — 2–3 sentence overview
- **Context** — why the meeting happened
- **Key Discussion Points** — one bullet per topic
- **Decisions** — explicit outcomes, or "None recorded"
- **Action Items** — checkbox list with owners
- **Open Questions** — follow-ups (omitted if none)

The note filename matches the `title` frontmatter field exactly.

---

## Log Files

All logs are written relative to the script location in `logs/`:

| File | Written by | Contains |
|---|---|---|
| `automator.log` | Automator shell script | File detection events, start/finish timestamps |
| `meeting_processor.log` | Python script | Parsing, AI call, Obsidian write, errors with full tracebacks |
| `meeting_processor_processed.txt` | Python script | Paths of all successfully processed files (prevents reprocessing) |

---

## Troubleshooting

| Symptom | What to check |
|---|---|
| `Config error: config.toml not found` | Copy `config.example.toml` to `config.toml` in `meeting_processor/` and fill it in |
| `Config error: … is not valid TOML` | Syntax error in `config.toml` — the message names the line; values must be in double quotes |
| `Config error: [ai] provider = …` | `provider` must be exactly `gemini`, `claude` or `openai` |
| `Config error: [<section>] api_key is empty or a placeholder` | Fill in that key (or `model`/`host`/`folder`, whichever is named) in `config.toml` |
| AI call fails with 401 / invalid API key | The key in the selected provider's section of `config.toml` is wrong or was rotated |
| Nothing happens after export | Finder → right-click watch folder → Services → Folder Actions Setup → confirm action is checked |
| `ModuleNotFoundError` | Confirm the venv is activated; re-run `pip install -r requirements.txt` |
| Obsidian write fails (401) | `[obsidian] api_key` in `config.toml` is wrong — copy it again from Obsidian → Settings → Local REST API |
| Obsidian write fails (404) | `[obsidian] folder` in `config.toml` does not exist in your vault — create it first |
| Project shows as `Unknown` | The project name in `PROJECTS` must loosely match how it appears in the transcript; adjust the key name |
| Wrong or missing title | Check `logs/meeting_processor.log` for the extracted H1 value; the AI may have omitted the heading |
| Date is today instead of meeting date | The file has no date metadata (MacWhisper bare-array JSON, all `.vtt`/`.srt`/`.txt`) and no `YYYY-MM-DD` in its filename, so the file modification time was used — put the date in the filename |
| `Skipping unsupported file type` | The extension is not `.json`, `.vtt`, `.srt` or `.txt` — e.g. a Teams `.docx` transcript; download it as `.vtt` instead |
| `.vtt`/`.srt`/`.txt` files are never picked up | The Automator script still has the old `if [[ "$f" == *.json ]]` filter — remove it (see setup step 4) |
| Everyone shows as `Unknown` | The `.vtt`/`.srt` file has no speaker names (neither `<v Name>` nor `Name: ` in the cues); for `.txt` this is expected — it is passed through without speaker detection |
| MoM note expected but standard note created | `.mom` must come directly before the extension: `Call.mom.vtt`, not `Call.vtt.mom` or `Call mom.vtt` |

---

## Switching AI Provider

Change one line in `config.toml`:

```toml
[ai]
provider = "claude"   # was "gemini"
```

Make sure `api_key` and `model` in the `[claude]` section are filled in and the SDK is installed (`pip install anthropic`). No code changes needed — the note's footer shows the configured model.

---

## File Structure

```
Meeting Automation/
├── MacWhisper-Exports/        ← drop transcripts here (.json, .vtt, .srt, .txt)
└── meeting_processor/
    ├── config.toml            ← your keys, provider, models, Obsidian (not in git)
    ├── config.example.toml    ← template for config.toml (in git)
    ├── pyproject.toml         ← packaging + `meeting-processor` entry point
    ├── src/meeting_processor/
    │   ├── processor.py       ← main code (projects, prompts, pipeline, main())
    │   ├── transcripts.py     ← one reader per transcript format → `Speaker: text`
    │   └── __main__.py        ← enables `python -m meeting_processor`
    ├── samples/               ← synthetic example transcripts, one per format
    ├── automator/
    │   └── folder-action.sh   ← reference copy of the Automator Folder Action script
    ├── requirements.txt       ← Python dependencies
    ├── README.md              ← this file
    ├── .venv/                 ← Python virtual environment
    └── logs/
        ├── automator.log
        ├── meeting_processor.log
        └── meeting_processor_processed.txt
```

---

## Developing new features

New features are planned in `docs/plans/` before they are implemented, using Claude Code (`/plan-feature`, then `/implement-step`). See [docs/plans/README.md](../docs/plans/README.md) for the workflow.
