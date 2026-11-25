"""Semantic Versioning and Changelog utilities for CRM Server."""

from functools import lru_cache
from pathlib import Path
import re
import subprocess
from django.conf import settings
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
RELEASE_HEADER_PATTERN = re.compile(
    r"^##\s+\[?v?(\d+\.\d+\.\d+)\]?(?:\s*-\s*(\d{4}-\d{2}-\d{2}))?\s*$"
)
SECTION_HEADER_PATTERN = re.compile(r"^###\s+(.+?)\s*$")
_INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
_INLINE_BOLD_PATTERN = re.compile(r"\*\*([^*]+)\*\*")

_VERSION_CACHE: tuple[tuple[str, int], str] | None = None
_CHANGELOG_CACHE: tuple[tuple[str, int], list[dict]] | None = None


def get_version_file() -> Path:
    """Return the path to the single-source-of-truth VERSION file."""
    return Path(settings.BASE_DIR) / "VERSION"


def get_changelog_file() -> Path:
    """Return the path to the CHANGELOG.md file."""
    return Path(settings.BASE_DIR) / "CHANGELOG.md"


def parse_semver(raw: str) -> tuple[int, int, int]:
    """Parse and validate a SemVer string (with optional leading 'v')."""
    cleaned = (raw or "").strip()
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:].strip()
    match = SEMVER_PATTERN.match(cleaned)
    if not match:
        raise ValueError(f"無效的 SemVer 版號格式: {raw!r}（預期格式為 X.Y.Z）")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def get_app_version() -> str:
    """Read and cache the current application SemVer string from VERSION."""
    global _VERSION_CACHE
    version_path = get_version_file()
    try:
        mtime_ns = version_path.stat().st_mtime_ns
        cache_key = (str(version_path), mtime_ns)
        if _VERSION_CACHE is not None and _VERSION_CACHE[0] == cache_key:
            return _VERSION_CACHE[1]
        raw = version_path.read_text(encoding="utf-8")
        major, minor, patch = parse_semver(raw)
        normalized = f"{major}.{minor}.{patch}"
        _VERSION_CACHE = (cache_key, normalized)
        return normalized
    except Exception:
        return "0.0.0"


def _render_inline_markdown(text: str) -> SafeString:
    """Escape HTML first, then render inline `code` and **bold** spans safely."""
    escaped = escape(text.strip())
    rendered = _INLINE_CODE_PATTERN.sub(r"<code>\1</code>", str(escaped))
    rendered = _INLINE_BOLD_PATTERN.sub(r"<strong>\1</strong>", rendered)
    return mark_safe(rendered)


def parse_changelog(limit: int | None = 15) -> list[dict]:
    """Parse CHANGELOG.md into structured release entries."""
    global _CHANGELOG_CACHE
    changelog_path = get_changelog_file()
    try:
        mtime_ns = changelog_path.stat().st_mtime_ns
        cache_key = (str(changelog_path), mtime_ns)
        if _CHANGELOG_CACHE is not None and _CHANGELOG_CACHE[0] == cache_key:
            releases = _CHANGELOG_CACHE[1]
        else:
            releases = _parse_changelog_text(changelog_path.read_text(encoding="utf-8"))
            _CHANGELOG_CACHE = (cache_key, releases)
    except Exception:
        return []

    if limit is not None:
        return list(releases[:limit])
    return list(releases)


def _parse_changelog_text(content: str) -> list[dict]:
    """Parse raw CHANGELOG.md text into release dicts."""
    releases: list[dict] = []
    current_release: dict | None = None
    current_section: dict | None = None

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        release_match = RELEASE_HEADER_PATTERN.match(line)
        if release_match:
            version_str = release_match.group(1)
            date_str = release_match.group(2) or ""
            current_release = {
                "version": version_str,
                "display_version": f"v{version_str}",
                "date": date_str,
                "sections": [],
            }
            releases.append(current_release)
            current_section = None
            continue

        if current_release is None:
            continue

        section_match = SECTION_HEADER_PATTERN.match(line)
        if section_match:
            current_section = {
                "title": section_match.group(1),
                "items": [],
            }
            current_release["sections"].append(current_section)
            continue

        if line.startswith("- ") or line.startswith("* "):
            item_text = line[2:].strip()
            if not item_text:
                continue
            if current_section is None:
                current_section = {"title": "變更項目", "items": []}
                current_release["sections"].append(current_section)
            current_section["items"].append(_render_inline_markdown(item_text))

    return releases


@lru_cache(maxsize=1)
def get_git_metadata() -> dict[str, str]:
    """Return short commit hash and branch name if .git exists; otherwise empty strings."""
    base_dir = Path(settings.BASE_DIR)
    if not (base_dir / ".git").exists():
        return {"commit": "", "branch": ""}

    commit = ""
    branch = ""
    try:
        res_commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(base_dir),
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if res_commit.returncode == 0:
            commit = res_commit.stdout.strip()

        res_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(base_dir),
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if res_branch.returncode == 0:
            branch = res_branch.stdout.strip()
    except Exception:
        return {"commit": "", "branch": ""}

    return {"commit": commit, "branch": branch}


def get_version_context(
    include_changelog: bool = False,
    include_admin_meta: bool = False,
) -> dict:
    """Build template context dictionary for version display and changelog modal."""
    app_version = get_app_version()
    app_version_display = f"v{app_version}"

    releases = parse_changelog(limit=15 if include_changelog else None)
    app_release_date = ""
    for entry in releases:
        if entry["version"] == app_version:
            app_release_date = entry["date"]
            break
    if not app_release_date and releases:
        app_release_date = releases[0].get("date", "")

    git_meta = get_git_metadata() if include_admin_meta else {"commit": "", "branch": ""}

    return {
        "app_version": app_version,
        "app_version_display": app_version_display,
        "app_release_date": app_release_date,
        "app_changelog": releases if include_changelog else [],
        "app_git_commit": git_meta.get("commit", ""),
        "app_git_branch": git_meta.get("branch", ""),
    }
