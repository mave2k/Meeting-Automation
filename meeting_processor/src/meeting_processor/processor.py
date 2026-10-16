"""
meeting_processor.processor
──────────────────────────────────────────────────────
Triggered by Automator Folder Action or launchd WatchPaths.
Reads a MacWhisper JSON export, sends the transcript to
Google Gemini, and writes the formatted note to Obsidian
via the Local REST API plugin.

FLAVOR DETECTION
  Standard note  →  any filename ending in .json
                    e.g.  2026-05-12 Client Call.json
  Minutes of Meeting → filename contains .mom before .json
                    e.g.  2026-05-12 Client Call.mom.json

Dependencies:
pip install google-genai anthropic openai requests
──────────────────────────────────────────────────────
"""

import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Project folder (meeting_processor/) — all relative paths anchor here ──
# This file lives in <project>/src/meeting_processor/, so go up two levels.
# Requires an editable install (pip install -e .) so __file__ points at the source tree.
PROJECT_DIR = Path(__file__).resolve().parents[2]

# ══════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════

# ── AI Provider ───────────────────────────────────────
AI_PROVIDER = "gemini"  # options: "gemini" | "claude" | "openai"

GEMINI_API_KEY = "AQ.Ab8RN6IgCtXZmu6rnDNqfhraNQJGJCCghg6DLmxRkY8FhiyPOw"
GEMINI_MODEL = "gemini-2.5-flash"

ANTHROPIC_API_KEY = "YOUR_ANTHROPIC_API_KEY"
ANTHROPIC_MODEL = "claude-opus-4-5"

OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
OPENAI_MODEL = "gpt-4o"

# ── Obsidian Local REST API ───────────────────────────
# Install plugin: https://github.com/coddingtonbear/obsidian-local-rest-api
# Find your API key in Obsidian → Settings → Local REST API
OBSIDIAN_HOST = "https://127.0.0.1:27124"  # default; uses self-signed cert
OBSIDIAN_API_KEY = "56aa5311d9ac2ab908c278177c8886478ed4d798cd3920e5696c51758794ea6f"
OBSIDIAN_FOLDER = "+/Meetings"  # folder path inside your vault

# ── Projects ──────────────────────────────────────────
# Key: the name the AI will use to identify the project
# wikilink: exact Obsidian note name to link to in [[...]]
# tags: fixed tags always added when this project is matched
PROJECTS = {
    "Halvorsen": {
        "wikilink": "Halvorsen MOC",
        "tags": ["halvorsen"],
    },
    "Atlas": {
        "wikilink": "Halvorsen MOC",
        "tags": ["halvorsen", "atlas"],
    },
    "Blackwyse": {
        "wikilink": "Blackwyse",
        "tags": ["blackwyse"],
    },
    "Medivia": {
        "wikilink": "Medivia MOC",
        "tags": ["medivia", "vantix", "clarion"],
    },
    "Clarion": {
        "wikilink": "Medivia MOC",
        "tags": ["medivia", "vantix", "clarion"],
    },
    "Vantix": {
        "wikilink": "Medivia MOC",
        "tags": ["medivia", "vantix", "clarion"],
    },
    "Internal": {
        "wikilink": "Internal",
        "tags": ["internal"],
    },
}

# Tags added to every note regardless of project
DEFAULT_TAGS = ["meeting", "transcript"]

# ── Paths (all relative to the project folder) ────
LOG_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOG_DIR / "meeting_processor.log"
PROCESSED_LOG = LOG_DIR / "meeting_processor_processed.txt"

# NOTE: WATCH_FOLDER is only needed for launchd Mode B (no-argument scan).
WATCH_FOLDER = PROJECT_DIR.parent / "MacWhisper-Exports"

# ══════════════════════════════════════════════════════
# SYSTEM PROMPTS
# ══════════════════════════════════════════════════════
# Placeholders injected by Python at runtime:
#   {DATE}          → meeting date from JSON metadata
#   {TITLE}         → literal placeholder; replaced after H1 extraction
#   {PROJECT_NAMES} → keys from PROJECTS config
#   {AI_NAME}       → active AI provider + model name

# ── Standard flavor ───────────────────────────────────
# Concise personal reference note; distilled bullets; brevity over completeness.
SYSTEM_PROMPT = """You are a precise meeting note formatter for an Obsidian knowledge vault.
Given a raw meeting transcript, produce ONLY a valid Obsidian markdown note —
no preamble, no explanation, no code fences around the output.

Important: keep the string {TITLE} literally in the frontmatter title field.
It will be replaced programmatically. Do NOT substitute it yourself.

Also, there might be some microphone feedback, meaning that spoken parts are
exactly repeated by myself in the transcript. Please account for that
possibility when attributing meaning to the different persons in the meeting.

For the meeting note, use the language the meeting was conducted in: If the conversation was mainly in German, create the note in German. If the conversation was mainly in English, create the note in English.

Use this exact structure:
---
title: "{TITLE}"
type: meeting
date: {DATE} <keep the date in form yyyy-mm-dd so that Obsidian interprets it correctly as a date field>
participants:
  - <full name of each speaker found in the transcript, one per line>
project: "<one of: {PROJECT_NAMES} — or Unknown if none match>"
summary: "<1-2 sentence summary of the core topic and key outcome>"
extra_tags:
  - <2-4 lowercase tags derived from transcript content only, no project or standard tags>
---
# <Concise title: YYYY-MM-DD + short description, include key participant names>

## TL;DR
<2-3 sentences on what was discussed and decided>

## Context
<1 short paragraph: why this meeting happened and what problem it addressed>

## Key Discussion Points
- <one bullet per major topic, one to two lines each>

## Decisions
- <explicit decisions made — if none, write "None recorded">

## Action Items
- [ ] <action item> — <owner if mentioned>

## Open Questions
- <unresolved questions or follow-ups — omit this entire section if none>

---
*Transcribed via MacWhisper · Auto-formatted with {AI_NAME}*
"""

# ── Minutes of Meeting (MoM) flavor ───────────────────
# Comprehensive, attribution-focused, suitable for external distribution.
# Triggered by naming the export file with .mom.json (e.g. meeting.mom.json).
SYSTEM_PROMPT_MOM = """You are a professional meeting minutes formatter producing formal Minutes of
Meeting (MoM) suitable for external distribution to all participants.

Given a raw meeting transcript, produce ONLY a valid Obsidian markdown note —
no preamble, no explanation, no code fences around the output.

Create the note in the language the meeting was mainly conducted in.

Important: keep the string {TITLE} literally in the frontmatter title field.
It will be replaced programmatically. Do NOT substitute it yourself.

There might be some microphone feedback in the transcript, meaning spoken parts
may be exactly repeated. Account for this — do not attribute repeated passages
as separate contributions from different speakers.

CONTENT RULES — follow these strictly:
- Completeness over brevity. Do NOT condense or omit any significant point,
  statement, or exchange. If something was said and matters, it must appear.
- Attribution is mandatory wherever the transcript identifies a speaker.
  Use the format "Speaker Name: …" or "According to [Name], …" consistently.
- Under Key Discussion Points, use as many bullets as the content requires.
  Group related points under sub-headings if the meeting covered multiple topics.
- Decisions must state who made or confirmed the decision, and any conditions
  or dependencies mentioned.
- Action Items must include the responsible person's name and any deadline or
  timeframe explicitly stated in the transcript.
- Open Questions must list every unresolved point, with the name of the person
  who raised it if identifiable.
- Language must be neutral, precise, and professional — suitable for sending
  directly to all meeting participants.

Create the note content in English, if the main language of the meeting was English. If the raw transcript is mostly German, create the meeting summary in German, too.

Use this exact structure:

---
title: "{TITLE}"
type: meeting
date: {DATE} <keep the date in form yyyy-mm-dd so that Obsidian interprets it correctly as a date field>
participants:
  - <full name of each speaker found in the transcript, one per line>
project: "<one of: {PROJECT_NAMES} — or Unknown if none match>"
summary: "<1-2 sentence summary of the core topic and key outcome>"
extra_tags:
  - <2-4 lowercase tags derived from transcript content only, no project or standard tags>
---
# <Concise title: YYYY-MM-DD + short description, include key participant names>

## Summary
<3-5 sentences covering what was discussed, decisions reached, and next steps>

## Context
<1-2 paragraphs: background, purpose of the meeting, and the problem or
opportunity it was called to address>

## Attendees & Roles
<List each identified meeting participant and their role or affiliation if determinable
from the transcript>

## Key Discussion Points
<Comprehensive, attributed bullets — do not omit anything material.
Use sub-headings (###) if the meeting covered clearly separate topics.>

## Decisions
<Each decision on its own bullet, with decision-owner and any conditions>

## Action Items
- [ ] <action item> — <owner if mentioned>

## Open Questions
<Each open question on its own bullet, with the name of who raised it>

---
*Minutes of Meeting · Transcribed via MacWhisper · Auto-formatted with {AI_NAME}*
"""

# ══════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════

log = logging.getLogger(__name__)


def setup_logging() -> None:
    """Configure file + stdout logging. Called from main(), not at import time."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout),
        ],
    )

# ══════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════

def is_mom_flavor(filepath: Path) -> bool:
    """Return True if the filename signals Minutes-of-Meeting flavor.

    Convention: any filename ending in .mom.json triggers MoM mode.
    Examples:
        2026-05-12 Client Call.mom.json  → MoM
        2026-05-12 Client Call.json      → standard
    """
    return filepath.name.lower().endswith(".mom.json")


def get_processed() -> set:
    """Return set of already-processed file paths."""
    if PROCESSED_LOG.exists():
        return set(PROCESSED_LOG.read_text(encoding="utf-8").splitlines())
    return set()


def mark_processed(filepath: Path) -> None:
    with open(PROCESSED_LOG, "a", encoding="utf-8") as f:
        f.write(str(filepath) + "\n")


def parse_macwhisper_json(json_file: Path) -> dict:
    """
    Parse a MacWhisper JSON export.

    MacWhisper produces two different JSON shapes depending on export style:

    Shape A — Wrapped dict (full export with metadata):
        {
            "createdAt": <timestamp>,
            "segments": [ { "speaker": "...", "text": "...", "start": 0.0 }, ... ]
        }

    Shape B — Bare array (Segments/People export, no metadata):
        [ { "speaker": "...", "text": "..." }, ... ]

    This function handles both.
    """
    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)

    # ── Normalise to a common structure ───────────────
    if isinstance(data, list):
        segments = data
        raw_date = None
    else:
        segments = data.get("segments", [])
        raw_date = (
            data.get("createdAt")
            or data.get("created_at")
            or data.get("date")
            or data.get("recordingDate")
        )

    # ── Date ─────────────────────────────────────────
    if raw_date:
        if isinstance(raw_date, (int, float)):
            if raw_date > 1e10:  # milliseconds → seconds
                raw_date /= 1000
            dt = datetime.fromtimestamp(raw_date)
        else:
            dt = datetime.fromisoformat(str(raw_date))
    else:
        log.warning("No date metadata in JSON — using file modification time.")
        dt = datetime.fromtimestamp(json_file.stat().st_mtime)

    date_display = dt.strftime("%d.%m.%Y")
    date_prefix = dt.strftime("%Y-%m-%d")

    # ── Transcript ────────────────────────────────────
    lines = []
    for seg in segments:
        speaker = (
            seg.get("speaker")
            or seg.get("speakerName")
            or seg.get("speakerLabel")
            or "Unknown"
        )
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"{speaker}: {text}")

    transcript_text = (
        "\n".join(lines)
        if lines
        else (data.get("transcript", "") if isinstance(data, dict) else "")
    )

    if not transcript_text:
        raise ValueError("No transcript content found in JSON file.")

    return {
        "date_display": date_display,
        "date_prefix": date_prefix,
        "transcript": transcript_text,
    }


def build_messages(parsed: dict, mom: bool = False) -> tuple[str, str]:
    """Return (system_prompt, user_message) with all placeholders injected.

    Args:
        parsed: Output of parse_macwhisper_json().
        mom:    If True, use the MoM flavor prompt; otherwise standard.
    """
    if AI_PROVIDER == "gemini":
        ai_name = f"Gemini ({GEMINI_MODEL})"
    elif AI_PROVIDER == "claude":
        ai_name = f"Claude ({ANTHROPIC_MODEL})"
    elif AI_PROVIDER == "openai":
        ai_name = f"OpenAI ({OPENAI_MODEL})"
    else:
        ai_name = "Unknown AI"

    project_names = ", ".join(PROJECTS.keys()) + ", Unknown"

    prompt_template = SYSTEM_PROMPT_MOM if mom else SYSTEM_PROMPT
    system = (
        prompt_template
        .replace("{DATE}", parsed["date_display"])
        .replace("{AI_NAME}", ai_name)
        .replace("{PROJECT_NAMES}", project_names)
    )

    user = (
        f"Meeting date: {parsed['date_display']}\n\n"
        f"Transcript:\n{parsed['transcript']}"
    )

    return system, user


def call_ai(system_prompt: str, user_message: str) -> str:
    """Dispatch to the configured AI provider."""
    if AI_PROVIDER == "gemini":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt
            )
        )
        return response.text.strip()

    elif AI_PROVIDER == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return message.content[0].text.strip()

    elif AI_PROVIDER == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content.strip()

    else:
        raise ValueError(f"Unknown AI_PROVIDER: {AI_PROVIDER}")


def extract_title_from_h1(markdown: str) -> str:
    """Extract the first # H1 heading; fall back to timestamped default."""
    match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    if match:
        return match.group(1).strip()
    log.warning("No H1 found in AI response — using fallback title.")
    return f"Meeting {datetime.now().strftime('%Y-%m-%d %H-%M')}"


def inject_title(markdown: str, title: str) -> str:
    """Replace the {TITLE} placeholder; regex safety net as backup."""
    result = markdown.replace("{TITLE}", title)
    result = re.sub(
        r'^(title:\s*")[^"]*(")'  ,
        rf'\g<1>{title}\g<2>',
        result,
        flags=re.MULTILINE,
    )
    return result


def resolve_project_and_tags(markdown: str, extra_fixed_tags: list[str] | None = None) -> str:
    """
    Reads the AI-generated project name and extra_tags from the frontmatter,
    replaces them with the proper wikilink and a merged, deduplicated tag list.

    Args:
        markdown:         The AI-generated markdown string.
        extra_fixed_tags: Additional tags to inject (e.g. ["mom"] for MoM flavor).

    Input frontmatter (AI-generated):
        project: "Halvorsen"
        extra_tags:
        - error-handling
        - support-process

    Output frontmatter (Python-resolved):
        project: "[[Halvorsen MOC]]"
        tags:
        - halvorsen           ← from PROJECTS config
        - meeting         ← from DEFAULT_TAGS
        - mom             ← from extra_fixed_tags (MoM flavor only)
        - error-handling  ← from AI extra_tags
        - support-process
    """
    extra_fixed_tags = extra_fixed_tags or []

    # ── Extract project name ──────────────────────────
    project_match = re.search(r'^project:\s*"?([^"\n]+)"?', markdown, re.MULTILINE)
    project_name = project_match.group(1).strip() if project_match else "Unknown"

    # ── Look up wikilink and fixed tags ───────────────
    project_data = PROJECTS.get(project_name, {})
    wikilink = project_data.get("wikilink", "")
    project_tags = project_data.get("tags", [])
    log.info(f"  Project resolved: '{project_name}' → wikilink: '{wikilink}'")

    # ── Extract AI-generated extra_tags ───────────────
    extra_tags_match = re.search(
        r'^extra_tags:\n((?:[ \t]+-[ \t]+\S+\n?)+)',
        markdown,
        re.MULTILINE,
    )
    extra_tags = []
    if extra_tags_match:
        extra_tags = re.findall(r'-\s+(\S+)', extra_tags_match.group(1))

    # ── Merge all tags (deduplicated, order preserved) ─
    seen = set()
    merged = []
    for tag in project_tags + DEFAULT_TAGS + extra_fixed_tags + extra_tags:
        if tag not in seen:
            merged.append(tag)
            seen.add(tag)

    tags_yaml = "\n".join(f"  - {t}" for t in merged)
    log.info(f"  Tags merged: {merged}")

    # ── Replace project: field ─────────────────────────
    wikilink_value = f'"[[{wikilink}]]"' if wikilink else '""'
    markdown = re.sub(
        r'^project:\s*"?[^"\n]*"?',
        f'project: {wikilink_value}',
        markdown,
        flags=re.MULTILINE,
    )

    # ── Replace extra_tags block with final tags block ─
    markdown = re.sub(
        r'^extra_tags:\n(?:[ \t]+-[ \t]+\S+\n?)+',
        f'tags:\n{tags_yaml}\n',
        markdown,
        flags=re.MULTILINE,
    )

    return markdown


def sanitize_filename(name: str) -> str:
    """Remove characters illegal in macOS / Obsidian filenames."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip()


def write_to_obsidian(markdown: str, filename: str) -> None:
    """PUT the note to Obsidian via the Local REST API plugin."""
    import requests
    url = f"{OBSIDIAN_HOST}/vault/{OBSIDIAN_FOLDER}/{filename}"
    headers = {
        "Authorization": f"Bearer {OBSIDIAN_API_KEY}",
        "Content-Type": "text/markdown; charset=utf-8",
    }
    resp = requests.put(
        url,
        data=markdown.encode("utf-8"),
        headers=headers,
        verify=False,  # self-signed cert on localhost — safe here
        timeout=15,
    )
    resp.raise_for_status()
    log.info(f"✓ Saved to Obsidian: {OBSIDIAN_FOLDER}/{filename}")


# ══════════════════════════════════════════════════════
# CORE PIPELINE
# ══════════════════════════════════════════════════════

def process_meeting(json_file: Path) -> None:
    mom = is_mom_flavor(json_file)
    flavor_label = "MoM" if mom else "standard"
    log.info(f"── Processing ({flavor_label}): {json_file.name}")

    parsed = parse_macwhisper_json(json_file)
    log.info(
        f"  Date: {parsed['date_display']} | "
        f"Transcript length: {len(parsed['transcript'])} chars"
    )

    system_prompt, user_message = build_messages(parsed, mom=mom)

    log.info(f"  Sending to {AI_PROVIDER} [{flavor_label} prompt]...")
    markdown = call_ai(system_prompt, user_message)
    log.info(f"  Response received ({len(markdown)} chars)")

    title = extract_title_from_h1(markdown)
    log.info(f"  Extracted title: {title}")

    markdown = inject_title(markdown, title)

    # Pass "mom" as an extra fixed tag when using the MoM flavor
    extra_fixed_tags = ["mom"] if mom else []
    markdown = resolve_project_and_tags(markdown, extra_fixed_tags=extra_fixed_tags)

    filename = f"{sanitize_filename(title)}.md"

    write_to_obsidian(markdown, filename)
    log.info(f"── Done: {filename}\n")


# ══════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════

def main() -> None:
    setup_logging()

    # ── Mode A: Automator passes file paths as arguments ──
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            filepath = Path(arg)
            if filepath.suffix.lower() != ".json":
                continue
            if str(filepath) in get_processed():
                log.info(f"Already processed, skipping: {filepath.name}")
                continue
            time.sleep(2)  # let MacWhisper finish writing the file
            try:
                process_meeting(filepath)
                mark_processed(filepath)
            except Exception as e:
                log.error(f"Failed [{filepath.name}]: {e}", exc_info=True)
        return

    # ── Mode B: launchd fires without arguments — scan WATCH_FOLDER ──
    processed = get_processed()
    new_files = [
        f for f in sorted(WATCH_FOLDER.glob("*.json"))
        if str(f) not in processed
    ]

    if not new_files:
        log.info("No new files to process.")
        return

    for json_file in new_files:
        time.sleep(2)
        try:
            process_meeting(json_file)
            mark_processed(json_file)
        except Exception as e:
            log.error(f"Failed [{json_file.name}]: {e}", exc_info=True)

