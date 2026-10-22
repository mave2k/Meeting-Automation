"""
meeting_processor.transcripts
──────────────────────────────────────────────────────
Reads transcript files of different formats and normalizes
them into the same shape for the pipeline:

    {"date_display": "17.09.2026",
     "date_prefix":  "2026-09-17",
     "transcript":   "Speaker: text\\nSpeaker: text ..."}

One reader per file extension, registered in READERS.
──────────────────────────────────────────────────────
"""

import html
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

FILENAME_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# Subtitle cues (.vtt / .srt)
VOICE_TAG_RE = re.compile(r"^<v(?:\.[^\s>]*)?\s+([^>]+)>")   # <v Name> or <v.class Name>  (Teams)
SPEAKER_PREFIX_RE = re.compile(r"^([^:<>]{1,60}):\s+(.+)$")  # "Name: text"  (Zoom, SRT)
TAG_RE = re.compile(r"<[^>]*>")
VTT_SKIP_BLOCKS = ("WEBVTT", "NOTE", "STYLE", "REGION")


# ══════════════════════════════════════════════════════
# DATE
# ══════════════════════════════════════════════════════

def date_from_filename(path: Path) -> datetime | None:
    """Return the first valid YYYY-MM-DD date anywhere in the filename, if any."""
    for m in FILENAME_DATE_RE.finditer(path.name):
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            continue
    return None


def resolve_date(path: Path, metadata_date: datetime | None = None) -> dict:
    """Date fallback chain: metadata → date in filename → file mtime (with warning).

    Returns {"date_display": "DD.MM.YYYY", "date_prefix": "YYYY-MM-DD"}.
    """
    dt = metadata_date or date_from_filename(path)
    if dt is None:
        log.warning("No date in metadata or filename — using file modification time.")
        dt = datetime.fromtimestamp(path.stat().st_mtime)
    return {
        "date_display": dt.strftime("%d.%m.%Y"),
        "date_prefix": dt.strftime("%Y-%m-%d"),
    }


# ══════════════════════════════════════════════════════
# TEXT + CUE HELPERS
# ══════════════════════════════════════════════════════

def read_text(path: Path) -> str:
    """Decode a text transcript: UTF-8 (BOM-tolerant), fallback cp1252. Line endings → \\n."""
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def cue_text_blocks(text: str, skip_prefixes: tuple[str, ...] = ()) -> list[str]:
    """Return the text of each subtitle cue, with its lines joined by a space.

    A cue is a blank-line-separated block containing a timestamp line ("-->").
    Everything before the timestamp line (cue IDs, SRT index numbers) is dropped.
    Blocks starting with one of skip_prefixes (e.g. WEBVTT header, NOTE) are ignored.
    """
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.strip().split("\n")]
        if not lines[0] or lines[0].startswith(skip_prefixes):
            continue
        ts = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if ts is None:
            continue
        body = " ".join(line for line in lines[ts + 1:] if line)
        if body:
            cues.append(body)
    return cues


def split_speaker(cue: str) -> tuple[str, str]:
    """Split a cue into (speaker, plain text).

    Speaker from a <v Name> voice tag (Teams; closing </v> optional) or a
    leading "Name: " prefix (Zoom, SRT). No speaker → "Unknown".
    """
    speaker = None
    m = VOICE_TAG_RE.match(cue)
    if m:
        speaker = m[1].strip()
        cue = cue[m.end():]
    text = html.unescape(TAG_RE.sub("", cue)).strip()
    text = re.sub(r"\s+", " ", text)
    if speaker is None:
        m = SPEAKER_PREFIX_RE.match(text)
        if m:
            speaker, text = m[1].strip(), m[2].strip()
    return speaker or "Unknown", text


def merge_turns(cues: list[str]) -> str:
    """Turn cues into "Speaker: text" lines, merging consecutive cues of the same speaker.

    File order is kept on purpose: Teams emits overlapping cues out of order,
    and sorting by timestamp would split statements that belong together.
    """
    turns: list[list] = []
    for cue in cues:
        speaker, text = split_speaker(cue)
        if not text:
            continue
        if turns and turns[-1][0] == speaker:
            turns[-1][1].append(text)
        else:
            turns.append([speaker, [text]])
    return "\n".join(f"{speaker}: {' '.join(parts)}" for speaker, parts in turns)


# ══════════════════════════════════════════════════════
# READERS
# ══════════════════════════════════════════════════════

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
    metadata_date = None
    if raw_date:
        if isinstance(raw_date, (int, float)):
            if raw_date > 1e10:  # milliseconds → seconds
                raw_date /= 1000
            metadata_date = datetime.fromtimestamp(raw_date)
        else:
            metadata_date = datetime.fromisoformat(str(raw_date))

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
        **resolve_date(json_file, metadata_date),
        "transcript": transcript_text,
    }


def parse_vtt(vtt_file: Path) -> dict:
    """Parse a WebVTT transcript (MS Teams, Zoom, …)."""
    cues = cue_text_blocks(read_text(vtt_file), skip_prefixes=VTT_SKIP_BLOCKS)
    transcript_text = merge_turns(cues)
    if not transcript_text:
        raise ValueError("No transcript content found in VTT file.")
    return {
        **resolve_date(vtt_file),
        "transcript": transcript_text,
    }


def parse_srt(srt_file: Path) -> dict:
    """Parse an SRT subtitle transcript (index line, timestamp line, text lines)."""
    cues = cue_text_blocks(read_text(srt_file))
    transcript_text = merge_turns(cues)
    if not transcript_text:
        raise ValueError("No transcript content found in SRT file.")
    return {
        **resolve_date(srt_file),
        "transcript": transcript_text,
    }


def parse_txt(txt_file: Path) -> dict:
    """Plain-text transcript: passed through as-is (no speaker detection)."""
    transcript_text = read_text(txt_file).strip()
    if not transcript_text:
        raise ValueError("No transcript content found in TXT file.")
    return {
        **resolve_date(txt_file),
        "transcript": transcript_text,
    }


# ══════════════════════════════════════════════════════
# DISPATCH
# ══════════════════════════════════════════════════════

READERS: dict[str, Callable[[Path], dict]] = {
    ".json": parse_macwhisper_json,
    ".vtt": parse_vtt,
    ".srt": parse_srt,
    ".txt": parse_txt,
}

SUPPORTED_EXTENSIONS = tuple(READERS)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in READERS


def parse_transcript(path: Path) -> dict:
    """Parse any supported transcript file into {date_display, date_prefix, transcript}."""
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(
            f"Unsupported transcript format: {path.suffix or '(none)'} "
            f"(supported: {', '.join(SUPPORTED_EXTENSIONS)})"
        )
    return reader(path)
