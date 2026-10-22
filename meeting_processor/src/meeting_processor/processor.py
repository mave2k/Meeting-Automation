"""
meeting_processor.processor
──────────────────────────────────────────────────────
Triggered by Automator Folder Action or launchd WatchPaths.
Reads a transcript file (MacWhisper JSON, …; see
transcripts.SUPPORTED_EXTENSIONS), sends the transcript to
the LLM configured in config.toml (Gemini, Claude or OpenAI),
and writes the formatted note to Obsidian via the Local
REST API plugin.

FLAVOR DETECTION
  Standard note  →  any supported transcript file
                    e.g.  2026-05-12 Client Call.json
  Minutes of Meeting → filename has .mom before the extension
                    e.g.  2026-05-12 Client Call.mom.json

Dependencies:
pip install google-genai anthropic openai requests
──────────────────────────────────────────────────────
"""

import logging
import re
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meeting_processor.transcripts import SUPPORTED_EXTENSIONS, is_supported, parse_transcript

# ── Project folder (meeting_processor/) — all relative paths anchor here ──
# This file lives in <project>/src/meeting_processor/, so go up two levels.
# Requires an editable install (pip install -e .) so __file__ points at the source tree.
PROJECT_DIR = Path(__file__).resolve().parents[2]

# ══════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════
# AI provider, models, API keys and Obsidian settings live in config.toml
# (see config.example.toml and load_config() below).

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
CONFIG_FILE = PROJECT_DIR / "config.toml"

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
# Triggered by naming the transcript file with .mom before the extension (e.g. meeting.mom.json).
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
# CONFIG FILE (config.toml)
# ══════════════════════════════════════════════════════

PROVIDERS = ("gemini", "claude", "openai")


class ConfigError(Exception):
    """Invalid or missing config.toml — message is shown to the user as-is."""


@dataclass(frozen=True)
class Config:
    provider: str
    api_key: str
    model: str
    obsidian_host: str
    obsidian_api_key: str
    obsidian_folder: str


def _is_unset(value) -> bool:
    """True if a config value is missing, empty, or still a YOUR_… placeholder."""
    return not isinstance(value, str) or not value.strip() or value.strip().startswith("YOUR_")


def load_config(path: Path = CONFIG_FILE) -> Config:
    """Read and validate config.toml. Raises ConfigError with an actionable message."""
    if not path.exists():
        raise ConfigError(
            f"{path.name} not found — copy config.example.toml to {path.name} and fill it in"
        )
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name} is not valid TOML: {e}") from None

    provider = data.get("ai", {}).get("provider")
    if provider not in PROVIDERS:
        raise ConfigError(
            f"{path.name}: [ai] provider = {provider!r} — must be one of {', '.join(PROVIDERS)}"
        )

    section = data.get(provider, {})
    for field in ("api_key", "model"):
        if _is_unset(section.get(field)):
            raise ConfigError(f"{path.name}: [{provider}] {field} is empty or a placeholder")

    obsidian = data.get("obsidian", {})
    for field in ("host", "api_key", "folder"):
        if _is_unset(obsidian.get(field)):
            raise ConfigError(f"{path.name}: [obsidian] {field} is empty or a placeholder")

    return Config(
        provider=provider,
        api_key=section["api_key"].strip(),
        model=section["model"].strip(),
        obsidian_host=obsidian["host"].strip().rstrip("/"),
        obsidian_api_key=obsidian["api_key"].strip(),
        obsidian_folder=obsidian["folder"].strip().strip("/"),
    )

# ══════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════

def is_mom_flavor(filepath: Path) -> bool:
    """Return True if the filename signals Minutes-of-Meeting flavor.

    Convention: .mom right before the file extension triggers MoM mode.
    Examples:
        2026-05-12 Client Call.mom.json  → MoM
        2026-05-12 Client Call.json      → standard
    """
    return filepath.stem.lower().endswith(".mom")


def get_processed() -> set:
    """Return set of already-processed file paths."""
    if PROCESSED_LOG.exists():
        return set(PROCESSED_LOG.read_text(encoding="utf-8").splitlines())
    return set()


def mark_processed(filepath: Path) -> None:
    with open(PROCESSED_LOG, "a", encoding="utf-8") as f:
        f.write(str(filepath) + "\n")


def build_messages(parsed: dict, config: Config, mom: bool = False) -> tuple[str, str]:
    """Return (system_prompt, user_message) with all placeholders injected.

    Args:
        parsed: Output of parse_transcript().
        config: Loaded config.toml (provider + model give the AI name).
        mom:    If True, use the MoM flavor prompt; otherwise standard.
    """
    provider_label = {"gemini": "Gemini", "claude": "Claude", "openai": "OpenAI"}
    ai_name = f"{provider_label[config.provider]} ({config.model})"

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


def call_ai(system_prompt: str, user_message: str, config: Config) -> str:
    """Dispatch to the configured AI provider."""
    if config.provider == "gemini":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=config.api_key)
        response = client.models.generate_content(
            model=config.model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt
            )
        )
        return response.text.strip()

    elif config.provider == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=config.api_key)
        message = client.messages.create(
            model=config.model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return message.content[0].text.strip()

    elif config.provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=config.api_key)
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content.strip()

    else:
        raise ValueError(f"Unknown AI provider: {config.provider}")


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


def write_to_obsidian(markdown: str, filename: str, config: Config) -> None:
    """PUT the note to Obsidian via the Local REST API plugin."""
    import requests
    url = f"{config.obsidian_host}/vault/{config.obsidian_folder}/{filename}"
    headers = {
        "Authorization": f"Bearer {config.obsidian_api_key}",
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
    log.info(f"✓ Saved to Obsidian: {config.obsidian_folder}/{filename}")


# ══════════════════════════════════════════════════════
# CORE PIPELINE
# ══════════════════════════════════════════════════════

def process_meeting(transcript_file: Path, config: Config) -> None:
    mom = is_mom_flavor(transcript_file)
    flavor_label = "MoM" if mom else "standard"
    log.info(f"── Processing ({flavor_label}): {transcript_file.name}")

    parsed = parse_transcript(transcript_file)
    log.info(
        f"  Date: {parsed['date_display']} | "
        f"Transcript length: {len(parsed['transcript'])} chars"
    )

    system_prompt, user_message = build_messages(parsed, config, mom=mom)

    log.info(f"  Sending to {config.provider} ({config.model}) [{flavor_label} prompt]...")
    markdown = call_ai(system_prompt, user_message, config)
    log.info(f"  Response received ({len(markdown)} chars)")

    title = extract_title_from_h1(markdown)
    log.info(f"  Extracted title: {title}")

    markdown = inject_title(markdown, title)

    # Pass "mom" as an extra fixed tag when using the MoM flavor
    extra_fixed_tags = ["mom"] if mom else []
    markdown = resolve_project_and_tags(markdown, extra_fixed_tags=extra_fixed_tags)

    filename = f"{sanitize_filename(title)}.md"

    write_to_obsidian(markdown, filename, config)
    log.info(f"── Done: {filename}\n")


# ══════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════

def main() -> None:
    setup_logging()
    try:
        config = load_config()
    except ConfigError as e:
        log.error(f"Config error: {e}")
        sys.exit(1)

    # ── Mode A: Automator passes file paths as arguments ──
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            filepath = Path(arg)
            if not is_supported(filepath):
                log.info(f"Skipping unsupported file type: {filepath.name}")
                continue
            if str(filepath) in get_processed():
                log.info(f"Already processed, skipping: {filepath.name}")
                continue
            time.sleep(2)  # let MacWhisper finish writing the file
            try:
                process_meeting(filepath, config)
                mark_processed(filepath)
            except Exception as e:
                log.error(f"Failed [{filepath.name}]: {e}", exc_info=True)
        return

    # ── Mode B: launchd fires without arguments — scan WATCH_FOLDER ──
    processed = get_processed()
    candidates = sorted(WATCH_FOLDER.iterdir()) if WATCH_FOLDER.is_dir() else []
    new_files = [
        f for f in candidates
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS and str(f) not in processed
    ]

    if not new_files:
        log.info("No new files to process.")
        return

    for transcript_file in new_files:
        time.sleep(2)
        try:
            process_meeting(transcript_file, config)
            mark_processed(transcript_file)
        except Exception as e:
            log.error(f"Failed [{transcript_file.name}]: {e}", exc_info=True)

