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

**Note type** — add a template name right before the extension to pick a different note template for that file, e.g. `Steering Committee 2026-06-03.mom.vtt` for formal Minutes of Meeting. Works for every format. See [Note Templates](#note-templates).

**Samples** — `meeting_processor/samples/` contains one synthetic file per format (invented names and content) for trying things out without a real meeting.

---

## Note Templates

A note template is a Markdown file in `meeting_processor/templates/` holding the complete instruction (prompt) for the AI, including the note layout it should produce. The file name without `.md` is the template name.

| Template | Produces |
|---|---|
| `summary` | Concise personal reference note — distilled bullets, brevity over completeness |
| `mom` | Formal, exhaustive Minutes of Meeting with attributions, suitable for sending to participants |

**Which template is used**
1. If the transcript's filename has a template name right before the extension, that template is used: `Client Call.mom.vtt` → `templates/mom.md`. Case doesn't matter.
2. Otherwise the default from `config.toml` is used:
   ```toml
   [template]
   active = "summary"
   ```
   A suffix that is not a template name is ignored, so `Review v1.2.vtt` simply uses the default.

The template name is added to the note's tags (`summary`, `mom`, …), so you can filter notes by type in Obsidian. The log shows which template was used for each file.

**Adding a new note type** — no code change needed:
1. Copy an existing template, e.g. `templates/summary.md` → `templates/steering.md`.
2. Edit the instructions and the note layout.
3. Use it with `Call.steering.vtt`, or make it the default with `active = "steering"`.

Rules for templates (checked on every run — a broken template stops the run with a one-line error before anything is processed):
- **Filename:** lowercase letters, digits, `-` and `_` only (`steering.md`, `client-call.md` — not `My Template.md`), because the name becomes a tag and a filename suffix.
- **Required markers:** every template must contain `{TITLE}`, `{PROJECT_NAMES}`, `project:` and `extra_tags:`. The script relies on them to set the note title, link the MOC and build the tag list (`project:` is where the AI names the project; the script turns that line into `MOC:`) — keep the frontmatter part of an existing template as it is and only change the rest.

**Placeholders** the script fills in before sending the template to the AI:

| Placeholder | Replaced with |
|---|---|
| `{DATE}` | Meeting date (see [date determination](#supported-transcript-formats)) |
| `{AI_NAME}` | Provider and model, e.g. `Gemini (gemini-2.5-flash)` |
| `{PROJECT_NAMES}` | The project names from `projects.toml`, plus `Unknown` |
| `{TITLE}` | Left for the AI to copy into the frontmatter; the script later replaces it with the note's `# H1` heading, which also becomes the filename |

---

## Setup

### 1. Get the code and create the watch folder

```zsh
mkdir -p ~/Projects
git clone https://github.com/mave2k/Meeting-Automation.git ~/Projects/Meeting\ Automation
mkdir -p ~/Projects/Meeting\ Automation/MacWhisper-Exports
```

The code then lives in:
```
~/Projects/Meeting Automation/meeting_processor/src/meeting_processor/processor.py
```

`logs/` is created automatically on the first run. The paths in this README and in the Automator script assume this location — if you clone elsewhere, adjust them.

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

A fresh clone contains only templates for your personal settings. Create two files from them in `meeting_processor/` — both are ignored by git, so your keys and client names never get committed:

| Create | From | Contains |
|---|---|---|
| `config.toml` | `config.example.toml` | API keys, AI provider and model, Obsidian connection, default note template |
| `projects.toml` | `projects.example.toml` | Your projects (MOC link + tags) and the tags added to every note |

The script won't run until both exist; if one is missing or incomplete, it stops with a one-line `Config error: …` that names the file and the problem.

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

[template]
active = "summary"             # default note template (a file in templates/)
```

Only the selected provider needs a key and model; the sections of the other providers may keep their `YOUR_…` placeholders. The script checks the file on every run and stops with a one-line `Config error: …` in the log if something is missing — before calling the AI or writing to Obsidian.

**Projects and tags** — live in `projects.toml`, which is ignored by git because it contains your real client names. Create it from the template, which holds made-up example projects:

```zsh
cp projects.example.toml projects.toml
```

Then replace the examples with one table per project you want to track:
```toml
# Tags added to every note regardless of project
default_tags = ["meeting", "transcript"]

[projects.Halvorsen]
wikilink = "Halvorsen MOC"   # exact name of an existing MOC note → MOC: "[[Halvorsen MOC]]"
tags     = ["halvorsen"]     # tags always applied for this project

[projects."Medivia Health"]  # names with spaces need quotes
wikilink = "Medivia MOC"
tags     = ["medivia"]
```

The AI picks the project name from these keys based on the transcript content (`Unknown` if none fits — so `Unknown` can't be used as a project name). The script then writes the project's `wikilink` into the note as the parent link `MOC: "[[…]]"`. Several projects may share one MOC (e.g. a sub-project, a partner company, or `Internal` → your own company's MOC, each with its own tags — see `projects.example.toml`).

- The AI's answer is matched ignoring case and surrounding spaces (`internal` finds `Internal`), so two project names that differ only in case are not allowed.
- If the answer is `Unknown` or not a configured project, the note gets `MOC: ""` and no project tags — the script never links to a note name that isn't in `projects.toml`.
- `wikilink` is **not** checked against your vault. A typo gives a broken (greyed-out) link — fix the name in `projects.toml`.

The AI also generates 2–4 additional content-specific tags automatically. Project tags, `default_tags`, the template name and the AI tags are merged and deduplicated before writing to Obsidian.

**Note templates** — the note format is defined by the Markdown files in `templates/`; `summary` is used by default. See [Note Templates](#note-templates) to change the layout or add a new note type.

### 4. Set up the Automator Folder Action

1. Open **Automator** → New Document → **Folder Action**
2. Set the watched folder to `~/Projects/Meeting Automation/MacWhisper-Exports`
3. Add a **Run Shell Script** action with Shell: `/bin/zsh` and Pass input: `as arguments`
4. Paste the contents of [`meeting_processor/automator/folder-action.sh`](meeting_processor/automator/folder-action.sh) (a reference copy — Automator does not read the file, so paste it again after changing it)

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
2. Make sure the filename contains the meeting date as `YYYY-MM-DD` (Teams usually adds it; otherwise rename) — add `.mom` (or another template name) before `.vtt` for Minutes of Meeting
3. Save it to `~/Projects/Meeting Automation/MacWhisper-Exports/` — the rest is the same

---

## Output Format

The note layout is defined by the template (see [Note Templates](#note-templates)). With the default `summary` template, each note contains:

- **Frontmatter** — `title`, `type`, `date`, `participants`, `MOC` (link to the project's MOC note), `summary`, `tags`:
  ```yaml
  ---
  title: "Halvorsen Rollout Sync – Approval Workflow"
  type: meeting
  date: 2026-07-07
  participants:
    - Anna Weber
    - James Okafor
  MOC: "[[Halvorsen MOC]]"
  summary: "Pilot plant is live; approval workflow must be faster before the second plant."
  tags:
    - halvorsen
    - meeting
    - transcript
    - summary
    - approval-workflow
  ---
  ```
- **TL;DR** — 2–3 sentence overview
- **Context** — why the meeting happened
- **Key Discussion Points** — one bullet per topic
- **Decisions** — explicit outcomes, or "None recorded"
- **Action Items** — checkbox list with owners
- **Open Questions** — follow-ups (omitted if none)

The `mom` template produces a more formal and complete structure with speaker attributions. The note filename matches the `title` frontmatter field exactly.

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
| `Config error: config.toml: [template] active is missing` | Add the `[template]` section with `active = "summary"` to `config.toml` (see `config.example.toml`) |
| `Config error: Template '…' not found in templates/` | `[template] active` names a template that doesn't exist — the message lists the available ones; check spelling |
| `Config error: Template templates/….md is missing required marker(s): …` | Put the named marker(s) back into that template — copy the frontmatter block from `templates/summary.md` |
| `Config error: Invalid template filename '…'` | Rename the file in `templates/` to lowercase letters, digits, `-` or `_` only (e.g. `My Template.md` → `my-template.md`) |
| `Config error: No templates found` | `templates/` is missing or empty — restore it with `git checkout -- templates/` |
| `Config error: projects.toml …` | The message names the problem: file missing (copy `projects.example.toml` to `projects.toml`), invalid TOML, a project without `wikilink` or `tags`, a project named `Unknown` (reserved), or two projects that differ only in case (`Halvorsen` / `halvorsen`) |
| AI call fails with 401 / invalid API key | The key in the selected provider's section of `config.toml` is wrong or was rotated |
| Nothing happens after export | Finder → right-click watch folder → Services → Folder Actions Setup → confirm action is checked |
| `ModuleNotFoundError` | Confirm the venv is activated; re-run `pip install -r requirements.txt` |
| Obsidian write fails (401) | `[obsidian] api_key` in `config.toml` is wrong — copy it again from Obsidian → Settings → Local REST API |
| Obsidian write fails (404) | `[obsidian] folder` in `config.toml` does not exist in your vault — create it first |
| `MOC` is empty (`MOC: ""`) | The AI answered `Unknown` or a name that isn't in `projects.toml` — the log line `Project unresolved: '…'` shows its answer. The project name in `projects.toml` must loosely match how the project is mentioned in the transcript; adjust the key name or add the project |
| `MOC` link is greyed out / "create note" in Obsidian | The `wikilink` in `projects.toml` doesn't match an existing note exactly (it is not checked against the vault) — fix the spelling |
| Wrong or missing title | Check `logs/meeting_processor.log` for the extracted H1 value; the AI may have omitted the heading |
| Date is today instead of meeting date | The file has no date metadata (MacWhisper bare-array JSON, all `.vtt`/`.srt`/`.txt`) and no `YYYY-MM-DD` in its filename, so the file modification time was used — put the date in the filename |
| `Skipping unsupported file type` | The extension is not `.json`, `.vtt`, `.srt` or `.txt` — e.g. a Teams `.docx` transcript; download it as `.vtt` instead |
| `.vtt`/`.srt`/`.txt` files are never picked up | The Automator script still has the old `if [[ "$f" == *.json ]]` filter — remove it (see setup step 4) |
| Everyone shows as `Unknown` | The `.vtt`/`.srt` file has no speaker names (neither `<v Name>` nor `Name: ` in the cues); for `.txt` this is expected — it is passed through without speaker detection |
| Wrong note type (e.g. summary instead of MoM) | The template name must come directly before the extension and match a file in `templates/`: `Call.mom.vtt`, not `Call.vtt.mom` or `Call mom.vtt`; the log line `Processing (template: …)` shows which one was used |

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
├── README.md                  ← this file
├── CLAUDE.md                  ← project guidance for Claude Code
├── docs/plans/                ← feature plans (see "Developing new features")
├── reference/                 ← unrelated reference scripts, not part of the pipeline
├── MacWhisper-Exports/        ← drop transcripts here (.json, .vtt, .srt, .txt) — not in git
└── meeting_processor/
    ├── config.toml            ← your keys, provider, models, Obsidian (not in git)
    ├── config.example.toml    ← template for config.toml (in git)
    ├── projects.toml          ← your projects (MOC wikilink + tags) and default tags (not in git)
    ├── projects.example.toml  ← template for projects.toml with made-up projects (in git)
    ├── templates/             ← note templates (prompt + layout), one .md per note type
    │   ├── summary.md
    │   └── mom.md
    ├── pyproject.toml         ← packaging + `meeting-processor` entry point
    ├── src/meeting_processor/
    │   ├── processor.py       ← main code (config + content loading, pipeline, main())
    │   ├── transcripts.py     ← one reader per transcript format → `Speaker: text`
    │   └── __main__.py        ← enables `python -m meeting_processor`
    ├── samples/               ← synthetic example transcripts, one per format
    ├── automator/
    │   └── folder-action.sh   ← reference copy of the Automator Folder Action script
    ├── requirements.txt       ← Python dependencies
    ├── .venv/                 ← Python virtual environment
    └── logs/
        ├── automator.log
        ├── meeting_processor.log
        └── meeting_processor_processed.txt
```

---

## Developing new features

New features are planned in `docs/plans/` before they are implemented, using Claude Code (`/plan-feature`, then `/implement-step`). See [docs/plans/README.md](docs/plans/README.md) for the workflow.
