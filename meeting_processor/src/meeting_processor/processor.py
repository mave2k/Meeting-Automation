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
PROJECT_CONTEXT_DIR = PROJECT_DIR / "project_context"  # git-ignored; files named in projects.toml context
PROMPTS_DIR = PROJECT_DIR / "prompts"  # internal prompts (not note templates)
IDENTIFY_PROMPT_FILE = PROMPTS_DIR / "identify_project.md"

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
    projects: dict        # name → {"wikilink": str, "tags": [str, ...], optional "context": [str, ...]}
    default_tags: list[str]
    templates: dict[str, str]  # template name (file stem) → prompt text
    project_context: dict[str, str]  # project name → combined context text (only projects with context)
    identify_prompt: str  # system prompt of the classification call (contains {PROJECT_NAMES})


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
        if "context" in entry and not _is_str_list(entry["context"]):
            raise ConfigError(
                f'{name}: [projects.{project}] context must be a list of .md filenames, '
                f'e.g. ["{project.lower()}_context.md"]'
            )

    return projects, default_tags


def _load_project_context(projects: dict, context_dir: Path) -> dict[str, str]:
    """Return project name → combined context text, for projects with a non-empty context list.

    Files are read from context_dir (sub-folders allowed, nothing outside it) and
    joined in the listed order, each preceded by a "[File: <name>]" line.
    """
    folder = f"{context_dir.name}/"
    root = context_dir.resolve()
    result: dict[str, str] = {}
    for project, entry in projects.items():
        files = entry.get("context") or []
        if not files:
            continue
        if not context_dir.is_dir():
            raise ConfigError(f"{folder} not found — create it next to projects.toml")
        parts = []
        for file in files:
            where = f"[projects.{project}] context file '{file}'"
            if not file.lower().endswith(".md"):
                raise ConfigError(f"{where} must be a .md file")
            path = (context_dir / file).resolve()
            if not path.is_relative_to(root):
                raise ConfigError(f"{where} must be inside {folder}")
            if not path.is_file():
                raise ConfigError(f"{where} not found in {folder}")
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                raise ConfigError(f"{where} is not valid UTF-8 — save it as UTF-8") from None
            if not text.strip():
                raise ConfigError(f"{where} is empty")
            parts.append(f"[File: {file}]\n{text.strip()}")
        result[project] = "\n\n".join(parts)
    return result


def _load_identify_prompt(path: Path) -> str:
    where = f"{path.parent.name}/{path.name}"
    if not path.is_file():
        raise ConfigError(f"{where} not found — it is part of the repository, restore it")
    text = path.read_text(encoding="utf-8")
    if "{PROJECT_NAMES}" not in text:
        raise ConfigError(f"{where} is missing the required marker {{PROJECT_NAMES}}")
    return text


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
    context_dir: Path = PROJECT_CONTEXT_DIR,
    identify_prompt_file: Path = IDENTIFY_PROMPT_FILE,
) -> Content:
    """Read and validate projects.toml, project_context/ files, templates/*.md and
    prompts/identify_project.md. Raises ConfigError."""
    projects, default_tags = _load_projects(projects_file)
    project_context = _load_project_context(projects, context_dir)
    templates = _load_templates(templates_dir)
    identify_prompt = _load_identify_prompt(identify_prompt_file)
    if config.template not in templates:
        raise ConfigError(
            f"Template '{config.template}' not found in {templates_dir.name}/ — "
            f"available: {', '.join(templates)}"
        )
    return Content(
        projects=projects,
        default_tags=default_tags,
        templates=templates,
        project_context=project_context,
        identify_prompt=identify_prompt,
    )

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


def _project_names(content: Content) -> str:
    """Project keys from projects.toml + "Unknown", as offered to the AI in {PROJECT_NAMES}."""
    return ", ".join([*content.projects, RESERVED_PROJECT])


def _lookup_project(answer: str, content: Content) -> str | None:
    """Match an AI project answer case-insensitively and whitespace-trimmed.

    Returns the key as written in projects.toml, or None ("Unknown", empty, unmatched).
    """
    by_lower = {name.strip().lower(): name for name in content.projects}
    return by_lower.get(answer.strip().lower())


# Precedes the project context files in the note call's user message
PROJECT_CONTEXT_HEADER = (
    "Project background — reference material for this project, NOT part of the meeting. "
    "Use it to understand names, roles, terms and goals, and to spell them correctly. "
    "Do not report anything from it as discussed, decided or assigned in this meeting."
)


def _user_message(parsed: dict, context: str | None = None) -> str:
    """User message shared by the classification call and the note call.

    With context, a clearly delimited project background block goes between
    the date and the transcript.
    """
    background = ""
    if context:
        background = (
            f"{PROJECT_CONTEXT_HEADER}\n"
            f"--- begin project background ---\n"
            f"{context}\n"
            f"--- end project background ---\n\n"
        )
    return (
        f"Meeting date: {parsed['date_display']}\n\n"
        f"{background}"
        f"Transcript:\n{parsed['transcript']}"
    )


def build_messages(
    parsed: dict, config: Config, content: Content, template: str, project: str | None = None
) -> tuple[str, str]:
    """Return (system_prompt, user_message) with all placeholders injected.

    Args:
        parsed:   Output of parse_transcript().
        config:   Loaded config.toml (provider + model give the AI name).
        content:  Loaded projects.toml + templates.
        template: Template name (see select_template()).
        project:  Project from identify_project(), or None. If it has context
                  files, their text is added to the user message.

    Placeholders in templates/*.md:
        {DATE}          → meeting date
        {AI_NAME}       → active AI provider + model name
        {PROJECT_NAMES} → project keys from projects.toml + "Unknown"
        {TITLE}         → left as-is here; replaced after H1 extraction (inject_title)
    """
    provider_label = {"gemini": "Gemini", "claude": "Claude", "openai": "OpenAI"}
    ai_name = f"{provider_label[config.provider]} ({config.model})"

    prompt_template = content.templates[template]
    system = (
        prompt_template
        .replace("{DATE}", parsed["date_display"])
        .replace("{AI_NAME}", ai_name)
        .replace("{PROJECT_NAMES}", _project_names(content))
    )

    context = content.project_context.get(project) if project else None
    return system, _user_message(parsed, context)


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


def identify_project(parsed: dict, config: Config, content: Content) -> str:
    """Classification call: ask the AI which project the meeting belongs to.

    Returns the project key as written in projects.toml, or RESERVED_PROJECT
    ("Unknown") if the AI answered Unknown or a name that matches no project.
    """
    system = content.identify_prompt.replace("{PROJECT_NAMES}", _project_names(content))
    response = call_ai(system, _user_message(parsed), config)

    first_line = next((line for line in response.splitlines() if line.strip()), "")
    answer = first_line.strip().strip("\"'`*.").strip()

    project = _lookup_project(answer, content)
    if project:
        return project
    if answer.lower() != RESERVED_PROJECT.lower():
        log.warning(f"  Classifier answer '{answer}' matches no project — using {RESERVED_PROJECT}")
    return RESERVED_PROJECT


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
    markdown: str,
    content: Content,
    extra_fixed_tags: list[str] | None = None,
    project: str | None = None,
) -> str:
    """
    Reads the AI-generated project name and extra_tags from the frontmatter,
    replaces them with a MOC: link and a merged, deduplicated tag list.

    Without `project`, the note call's project answer decides: it is matched against
    projects.toml case-insensitively and whitespace-trimmed. "Unknown", empty or
    unmatched → MOC: "" and no project tags — never a link to a name that isn't
    configured. The MOC note itself is not checked against the vault (projects.toml
    is the source of truth).

    With `project` (from identify_project()), that project decides; the note call's
    answer is only compared against it and a disagreement is logged.

    Args:
        markdown:         The AI-generated markdown string.
        content:          Loaded projects.toml (projects + default tags).
        extra_fixed_tags: Additional tags to inject (the template name, e.g. ["summary"]).
        project:          Identified project key or RESERVED_PROJECT, or None.

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
    answer_key = _lookup_project(project_answer, content)
    if project is None:
        project_key = answer_key
        source = f"'{project_answer}' → "
    else:
        if (answer_key or RESERVED_PROJECT) != project:
            log.info(
                f"  Note call answered project '{project_answer}' — "
                f"using identified project '{project}'"
            )
        project_key = None if project == RESERVED_PROJECT else project
        source = "identified → "
    project_data = content.projects[project_key] if project_key else {}
    wikilink = project_data.get("wikilink", "")
    project_tags = project_data.get("tags", [])
    if project_key:
        log.info(f"  Project resolved: {source}'{project_key}' → MOC: '{wikilink}'")
    else:
        log.info(f"  Project unresolved: {source}'{RESERVED_PROJECT if project else project_answer}' → MOC: ''")

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

    # Classification call only when at least one project has context files.
    # Must stay inside process_meeting() (a dry run never reaches it).
    project = None
    if content.project_context:
        log.info(f"  Identifying project via {config.provider} ({config.model})...")
        project = identify_project(parsed, config, content)
        if project in content.project_context:
            files = ", ".join(content.projects[project]["context"])
            chars = len(content.project_context[project])
            log.info(f"  Project identified: {project} (context: {files}, {chars:,} chars)")
        else:
            log.info(f"  Project identified: {project} (no context)")

    system_prompt, user_message = build_messages(parsed, config, content, template, project)

    log.info(f"  Sending to {config.provider} ({config.model}) [template: {template}]...")
    markdown = call_ai(system_prompt, user_message, config)
    log.info(f"  Response received ({len(markdown)} chars)")

    title = extract_title_from_h1(markdown)
    log.info(f"  Extracted title: {title}")

    markdown = inject_title(markdown, title)

    # The template name is always added as a fixed tag
    markdown = resolve_project_and_tags(
        markdown, content, extra_fixed_tags=[template], project=project
    )

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

