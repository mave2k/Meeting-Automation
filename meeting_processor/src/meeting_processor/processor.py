"""
meeting_processor.processor
──────────────────────────────────────────────────────
Triggered by Automator Folder Action or launchd WatchPaths.
Reads a transcript file (MacWhisper JSON, …; see
transcripts.SUPPORTED_EXTENSIONS), sends the transcript to
the LLM configured in config.toml (Gemini, Claude or OpenAI),
and writes the formatted note to Obsidian via the Local
REST API plugin.

TEMPLATE SELECTION
  Note templates (prompt + note layout) are the .md files in templates/;
  projects and default tags live in projects.toml.
  Default template  →  [template] active in config.toml
  Per-file override →  template name right before the extension
                       e.g.  2026-05-12 Client Call.<name>.vtt → templates/<name>.md
  The template name is added to the note's tags.

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

# Projects + default tags live in projects.toml, note templates (prompts) in
# templates/*.md (see load_content() below).

# ── Paths (all relative to the project folder) ────
LOG_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOG_DIR / "meeting_processor.log"
PROCESSED_LOG = LOG_DIR / "meeting_processor_processed.txt"
CONFIG_FILE = PROJECT_DIR / "config.toml"
PROJECTS_FILE = PROJECT_DIR / "projects.toml"
TEMPLATES_DIR = PROJECT_DIR / "templates"

# NOTE: WATCH_FOLDER is only needed for launchd Mode B (no-argument scan).
WATCH_FOLDER = PROJECT_DIR.parent / "MacWhisper-Exports"

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
    template: str


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

    template = data.get("template", {}).get("active")
    if not isinstance(template, str) or not template.strip():
        raise ConfigError(f"{path.name}: [template] active is missing — see config.example.toml")

    return Config(
        provider=provider,
        api_key=section["api_key"].strip(),
        model=section["model"].strip(),
        obsidian_host=obsidian["host"].strip().rstrip("/"),
        obsidian_api_key=obsidian["api_key"].strip(),
        obsidian_folder=obsidian["folder"].strip().strip("/"),
        template=template.strip().lower(),
    )

# ══════════════════════════════════════════════════════
# CONTENT FILES (projects.toml, templates/*.md)
# ══════════════════════════════════════════════════════

TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Every template must keep these, or title injection / project + tag rewriting breaks
TEMPLATE_MARKERS = ("{TITLE}", "{PROJECT_NAMES}", "project:", "extra_tags:")
RESERVED_PROJECT = "Unknown"  # appended to {PROJECT_NAMES} by build_messages()


@dataclass(frozen=True)
class Content:
    projects: dict        # name → {"wikilink": str, "tags": [str, ...]}
    default_tags: list[str]
    templates: dict[str, str]  # template name (file stem) → prompt text


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _load_projects(path: Path) -> tuple[dict, list[str]]:
    name = path.name
    if not path.exists():
        raise ConfigError(
            f"{name} not found in {path.parent.name}/ — copy projects.example.toml to {name} and fill it in"
        )
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{name} is not valid TOML: {e}") from None

    default_tags = data.get("default_tags", [])
    if not _is_str_list(default_tags):
        raise ConfigError(f'{name}: default_tags must be a list of strings, e.g. ["meeting"]')

    projects = data.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise ConfigError(f"{name}: no projects defined — add at least one [projects.<Name>] table")

    seen: dict[str, str] = {}  # lowercased name → name as written (lookup is case-insensitive)
    for project, entry in projects.items():
        key = project.strip().lower()
        if key == RESERVED_PROJECT.lower():
            raise ConfigError(f"{name}: project name '{project}' is reserved — rename it")
        if key in seen:
            raise ConfigError(
                f"{name}: projects '{seen[key]}' and '{project}' differ only in case — keep one"
            )
        seen[key] = project
        if not isinstance(entry, dict):
            raise ConfigError(f"{name}: projects.{project} must be a table with wikilink and tags")
        wikilink = entry.get("wikilink")
        if not isinstance(wikilink, str) or not wikilink.strip():
            raise ConfigError(f'{name}: [projects.{project}] wikilink is missing or empty')
        if not _is_str_list(entry.get("tags")):
            raise ConfigError(f'{name}: [projects.{project}] tags must be a list of strings, e.g. ["{project.lower()}"]')

    return projects, default_tags


def _load_templates(templates_dir: Path) -> dict[str, str]:
    folder = f"{templates_dir.name}/"
    files = sorted(templates_dir.glob("*.md")) if templates_dir.is_dir() else []
    if not files:
        raise ConfigError(f"No templates found — {folder} must contain at least one .md file")

    templates = {}
    for file in files:
        if not TEMPLATE_NAME_RE.match(file.stem):
            raise ConfigError(
                f"Invalid template filename '{file.name}' in {folder} — use lowercase letters, "
                "digits, '-' or '_' only (the name becomes a tag and a filename suffix)"
            )
        text = file.read_text(encoding="utf-8")
        missing = [m for m in TEMPLATE_MARKERS if m not in text]
        if missing:
            raise ConfigError(f"Template {folder}{file.name} is missing required marker(s): {', '.join(missing)}")
        templates[file.stem] = text
    return templates


def load_content(
    config: Config,
    projects_file: Path = PROJECTS_FILE,
    templates_dir: Path = TEMPLATES_DIR,
) -> Content:
    """Read and validate projects.toml and templates/*.md. Raises ConfigError."""
    projects, default_tags = _load_projects(projects_file)
    templates = _load_templates(templates_dir)
    if config.template not in templates:
        raise ConfigError(
            f"Template '{config.template}' not found in {templates_dir.name}/ — "
            f"available: {', '.join(templates)}"
        )
    return Content(projects=projects, default_tags=default_tags, templates=templates)

# ══════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════

def select_template(filepath: Path, content: Content, default: str) -> str:
    """Return the template name to use for a transcript file.

    A suffix right before the file extension that names an existing template
    overrides the default (case-insensitive):
        Call.<name>.vtt  → templates/<name>.md, if it exists
        Call.vtt         → default ([template] active in config.toml)
        Review v1.2.vtt  → default (no template named "2")
    """
    suffix = Path(filepath.stem).suffix.lstrip(".").lower()
    return suffix if suffix in content.templates else default


def get_processed() -> set:
    """Return set of already-processed file paths."""
    if PROCESSED_LOG.exists():
        return set(PROCESSED_LOG.read_text(encoding="utf-8").splitlines())
    return set()


def mark_processed(filepath: Path) -> None:
    with open(PROCESSED_LOG, "a", encoding="utf-8") as f:
        f.write(str(filepath) + "\n")


def build_messages(parsed: dict, config: Config, content: Content, template: str) -> tuple[str, str]:
    """Return (system_prompt, user_message) with all placeholders injected.

    Args:
        parsed:   Output of parse_transcript().
        config:   Loaded config.toml (provider + model give the AI name).
        content:  Loaded projects.toml + templates.
        template: Template name (see select_template()).

    Placeholders in templates/*.md:
        {DATE}          → meeting date
        {AI_NAME}       → active AI provider + model name
        {PROJECT_NAMES} → project keys from projects.toml + "Unknown"
        {TITLE}         → left as-is here; replaced after H1 extraction (inject_title)
    """
    provider_label = {"gemini": "Gemini", "claude": "Claude", "openai": "OpenAI"}
    ai_name = f"{provider_label[config.provider]} ({config.model})"

    project_names = ", ".join([*content.projects, RESERVED_PROJECT])

    prompt_template = content.templates[template]
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


def resolve_project_and_tags(
    markdown: str, content: Content, extra_fixed_tags: list[str] | None = None
) -> str:
    """
    Reads the AI-generated project name and extra_tags from the frontmatter,
    replaces them with a MOC: link and a merged, deduplicated tag list.

    The AI's project answer is matched against projects.toml case-insensitively
    and whitespace-trimmed. "Unknown", empty or unmatched → MOC: "" and no project
    tags — never a link to a name that isn't configured. The MOC note itself is not
    checked against the vault (projects.toml is the source of truth).

    Args:
        markdown:         The AI-generated markdown string.
        content:          Loaded projects.toml (projects + default tags).
        extra_fixed_tags: Additional tags to inject (the template name, e.g. ["summary"]).

    Input frontmatter (AI-generated):
        project: "acme "
        extra_tags:
        - error-handling
        - support-process

    Output frontmatter (Python-resolved):
        MOC: "[[Acme MOC]]"   ← replaces the project: line in place
        tags:
        - acme            ← from projects.toml (project tags)
        - meeting         ← from projects.toml (default_tags)
        - summary         ← from extra_fixed_tags (template name)
        - error-handling  ← from AI extra_tags
        - support-process
    """
    extra_fixed_tags = extra_fixed_tags or []

    # ── Extract project name ──────────────────────────
    project_match = re.search(r'^project:\s*"?([^"\n]+)"?', markdown, re.MULTILINE)
    project_answer = project_match.group(1).strip() if project_match else RESERVED_PROJECT

    # ── Look up MOC link and fixed tags (case-insensitive) ─
    by_lower = {name.strip().lower(): name for name in content.projects}
    project_key = by_lower.get(project_answer.lower())
    project_data = content.projects[project_key] if project_key else {}
    wikilink = project_data.get("wikilink", "")
    project_tags = project_data.get("tags", [])
    if project_key:
        log.info(f"  Project resolved: '{project_answer}' → '{project_key}' → MOC: '{wikilink}'")
    else:
        log.info(f"  Project unresolved: '{project_answer}' → MOC: ''")

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
    for tag in project_tags + content.default_tags + extra_fixed_tags + extra_tags:
        if tag not in seen:
            merged.append(tag)
            seen.add(tag)

    tags_yaml = "\n".join(f"  - {t}" for t in merged)
    log.info(f"  Tags merged: {merged}")

    # ── Replace the AI's project: line with MOC: (same position) ─
    # Only the first occurrence (the frontmatter), so a "project:" line in the body stays.
    moc_line = f'MOC: "[[{wikilink}]]"' if wikilink else 'MOC: ""'
    markdown = re.sub(
        r'^project:\s*"?[^"\n]*"?',
        lambda _: moc_line,
        markdown,
        count=1,
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

def process_meeting(transcript_file: Path, config: Config, content: Content) -> None:
    template = select_template(transcript_file, content, config.template)
    log.info(f"── Processing (template: {template}): {transcript_file.name}")

    parsed = parse_transcript(transcript_file)
    log.info(
        f"  Date: {parsed['date_display']} | "
        f"Transcript length: {len(parsed['transcript'])} chars"
    )

    system_prompt, user_message = build_messages(parsed, config, content, template)

    log.info(f"  Sending to {config.provider} ({config.model}) [template: {template}]...")
    markdown = call_ai(system_prompt, user_message, config)
    log.info(f"  Response received ({len(markdown)} chars)")

    title = extract_title_from_h1(markdown)
    log.info(f"  Extracted title: {title}")

    markdown = inject_title(markdown, title)

    # The template name is always added as a fixed tag
    markdown = resolve_project_and_tags(markdown, content, extra_fixed_tags=[template])

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
        content = load_content(config)
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
                process_meeting(filepath, config, content)
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
            process_meeting(transcript_file, config, content)
            mark_processed(transcript_file)
        except Exception as e:
            log.error(f"Failed [{transcript_file.name}]: {e}", exc_info=True)

