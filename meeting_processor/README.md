# Meeting Processor

Automatically turns MacWhisper meeting transcripts into structured Obsidian notes using an AI model of your choice.

**Workflow:**
Record → Transcribe in MacWhisper → Export JSON → Folder watcher triggers script → Formatted note appears in Obsidian

---

## Requirements

- macOS (Automator is required for the folder watcher)
- [MacWhisper](https://goodsnooze.gumroad.com/l/macwhisper) (Pro recommended for speaker recognition)
- [Obsidian](https://obsidian.md) with the [Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) plugin installed and enabled
- Python 3 (pre-installed on macOS)
- An API key for at least one supported AI provider (see below)

---

## Supported AI Providers

| Provider | Model variable | Get an API key |
|---|---|---|
| Google Gemini | `GEMINI_MODEL` | [aistudio.google.com](https://aistudio.google.com) |
| Anthropic Claude | `ANTHROPIC_MODEL` | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI GPT | `OPENAI_MODEL` | [platform.openai.com](https://platform.openai.com) |

Only the SDK for your chosen provider needs to be installed.

---

## Setup

### 1. Create the folder structure

```zsh
mkdir -p ~/Projects/Meeting\ Automation/meeting_processor/logs
mkdir -p ~/Projects/Meeting\ Automation/MacWhisper-Exports
```

Place `meeting_processor.py` in:
```
~/Projects/Meeting Automation/meeting_processor/meeting_processor.py
```

### 2. Create a Python virtual environment

```zsh
cd ~/Projects/Meeting\ Automation/meeting_processor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure the script

Open `meeting_processor.py` and edit the configuration section at the top.

**AI provider:**
```python
AI_PROVIDER    = "gemini"            # "gemini" | "claude" | "openai"
GEMINI_API_KEY = "your-key-here"
GEMINI_MODEL   = "gemini-2.5-flash"  # change model here if needed
```

**Obsidian:**
```python
OBSIDIAN_API_KEY = "your-key-here"   # Obsidian → Settings → Local REST API
OBSIDIAN_FOLDER  = "Meetings"        # folder path inside your vault
```

**Projects** — add one entry per project you want to track:
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
4. Paste:

```zsh
#!/bin/zsh
SCRIPT="$HOME/Projects/Meeting Automation/meeting_processor/meeting_processor.py"
VENV="$HOME/Projects/Meeting Automation/meeting_processor/.venv/bin/activate"
LOG="$HOME/Projects/Meeting Automation/meeting_processor/logs/automator.log"

mkdir -p "$(dirname "$LOG")"
source "$VENV"

for f in "$@"; do
  if [[ "$f" == *.json ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Triggered: $f" >> "$LOG"
    python3 "$SCRIPT" "$f" >> "$LOG" 2>&1
  fi
done
```

5. Save the Folder Action

To verify it is active: right-click the watch folder in Finder → **Services → Folder Actions Setup** → confirm the action is listed and checked.

### 5. Test manually

Before relying on the Automator trigger, run the script directly on a real export:

```zsh
cd ~/Projects/Meeting\ Automation/meeting_processor
source .venv/bin/activate
python3 meeting_processor.py ~/Projects/Meeting\ Automation/MacWhisper-Exports/your-export.json
```

A new note should appear in your Obsidian vault within a few seconds. Check `logs/meeting_processor.log` for detailed output.

---

## Daily Workflow

1. Record the meeting in MacWhisper with speaker recognition enabled
2. When done: **Export** in MacWhisper with these settings:
   - Style: `Transcript`
   - Format: `.json`
   - Grouping: `People`
3. Save the export to `~/Projects/Meeting Automation/MacWhisper-Exports/`
4. The Automator action fires automatically → note appears in Obsidian in ~30 seconds

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
| Nothing happens after export | Finder → right-click watch folder → Services → Folder Actions Setup → confirm action is checked |
| `ModuleNotFoundError` | Confirm the venv is activated; re-run `pip install -r requirements.txt` |
| `'list' object has no attribute 'get'` | Update to the latest version of the script — older versions did not handle MacWhisper's bare-array JSON format |
| Obsidian write fails (401) | API key is wrong — copy it again from Obsidian → Settings → Local REST API |
| Obsidian write fails (404) | `OBSIDIAN_FOLDER` does not exist in your vault — create it first |
| Project shows as `Unknown` | The project name in `PROJECTS` must loosely match how it appears in the transcript; adjust the key name |
| Wrong or missing title | Check `logs/meeting_processor.log` for the extracted H1 value; the AI may have omitted the heading |
| Date is today instead of meeting date | MacWhisper's bare-array JSON has no date metadata — the script uses file modification time; export promptly after recording |

---

## Switching AI Provider

Change one line in the config:

```python
AI_PROVIDER = "claude"   # was "gemini"
```

Make sure the corresponding API key is filled in and the SDK is installed (`pip install anthropic`). No other changes needed.

---

## File Structure

```
Meeting Automation/
├── MacWhisper-Exports/        ← drop MacWhisper JSON exports here
└── meeting_processor/
    ├── meeting_processor.py   ← main script
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
