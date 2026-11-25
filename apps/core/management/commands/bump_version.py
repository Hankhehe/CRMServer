"""Django management command to bump SemVer VERSION, update CHANGELOG.md, and optionally tag."""

from datetime import datetime
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core import versioning

SECTION_ALIASES = {
    "added": "新增 (Added)",
    "changed": "變更 (Changed)",
    "fixed": "修復 (Fixed)",
    "removed": "移除 (Removed)",
}


class Command(BaseCommand):
    help = "更新系統語意化版本號 (VERSION)、寫入 CHANGELOG.md 並可選建立 Git Tag。"

    def add_arguments(self, parser):
        parser.add_argument(
            "part",
            nargs="?",
            choices=["patch", "minor", "major"],
            help="要遞增的 SemVer 層級：patch / minor / major（與 --set 互斥）",
        )
        parser.add_argument(
            "--set",
            dest="set_version",
            help="直接指定目標版號（格式 X.Y.Z 或 vX.Y.Z，與 part 互斥）",
        )
        parser.add_argument(
            "--note",
            action="append",
            dest="notes",
            default=[],
            help="更新說明項目，可重複傳入多個 --note",
        )
        parser.add_argument(
            "--section",
            default="changed",
            help="CHANGELOG 分類標題（預設 changed -> 變更 (Changed)，亦支援 added / fixed / removed 或自訂中文標題）",
        )
        parser.add_argument(
            "--tag",
            action="store_true",
            help="更新檔案後自動建立 Annotated Git Tag (vX.Y.Z)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="預覽升版結果與 CHANGELOG 內容，不實際寫入檔案或建立 Tag",
        )

    def handle(self, *args, **options):
        part = options.get("part")
        set_version = options.get("set_version")

        if bool(part) == bool(set_version):
            raise CommandError("請擇一提供遞增層級 (patch/minor/major) 或 --set X.Y.Z。")

        version_file = versioning.get_version_file()
        changelog_file = versioning.get_changelog_file()

        current_raw = (
            version_file.read_text(encoding="utf-8").strip()
            if version_file.exists()
            else "0.0.0"
        )
        try:
            major, minor, patch = versioning.parse_semver(current_raw)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        current_version = f"{major}.{minor}.{patch}"

        if set_version:
            try:
                t_major, t_minor, t_patch = versioning.parse_semver(set_version)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            new_version = f"{t_major}.{t_minor}.{t_patch}"
        elif part == "major":
            new_version = f"{major + 1}.0.0"
        elif part == "minor":
            new_version = f"{major}.{minor + 1}.0"
        else:
            new_version = f"{major}.{minor}.{patch + 1}"

        if new_version == current_version:
            raise CommandError(f"目標版號 {new_version} 與目前版號相同，無需升版。")

        today_taipei = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
        raw_section = (options.get("section") or "changed").strip()
        section_title = SECTION_ALIASES.get(raw_section.lower(), raw_section)

        notes = [n.strip() for n in (options.get("notes") or []) if n and n.strip()]
        if not notes:
            notes = [f"版本升級至 v{new_version}。"]

        entry_lines = [
            f"## [{new_version}] - {today_taipei}",
            f"### {section_title}",
        ]
        for note in notes:
            entry_lines.append(f"- {note}")
        entry_block = "\n".join(entry_lines)

        existing_changelog = (
            changelog_file.read_text(encoding="utf-8")
            if changelog_file.exists()
            else "# 版本更新歷程 (Changelog)\n\n"
        )

        duplicate_pattern = re.compile(
            rf"^##\s+\[?v?{re.escape(new_version)}\]?(?:\s|$|-)",
            re.MULTILINE,
        )
        if duplicate_pattern.search(existing_changelog):
            raise CommandError(
                f"CHANGELOG.md 中已存在版本 [{new_version}] 的紀錄，請先確認。"
            )

        updated_changelog = self._insert_changelog_entry(existing_changelog, entry_block)

        if options.get("dry_run"):
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] 版號變更預覽：{current_version} -> {new_version}"
                )
            )
            self.stdout.write(f"[DRY-RUN] 將寫入 {changelog_file}：\n{entry_block}")
            if options.get("tag"):
                self.stdout.write(f"[DRY-RUN] 將建立 Git Tag：v{new_version}")
            return

        version_file.write_text(f"{new_version}\n", encoding="utf-8")
        changelog_file.write_text(updated_changelog, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"已更新版本號：{current_version} -> {new_version} (日期：{today_taipei})"
            )
        )

        if options.get("tag"):
            self._create_git_tag(new_version, notes)

    def _insert_changelog_entry(self, content: str, entry_block: str) -> str:
        """Insert a new release block right before the first existing release heading."""
        first_release_match = re.search(r"^##\s+\[?v?\d+\.\d+\.\d+\]?", content, re.MULTILINE)
        if first_release_match:
            idx = first_release_match.start()
            prefix = content[:idx].rstrip("\n")
            suffix = content[idx:].lstrip("\n")
            return f"{prefix}\n\n{entry_block}\n\n{suffix}"

        stripped = content.rstrip("\n")
        return f"{stripped}\n\n{entry_block}\n"

    def _create_git_tag(self, version: str, notes: list[str]) -> None:
        tag_name = f"v{version}"
        base_dir = Path(settings.BASE_DIR)
        if not (base_dir / ".git").exists():
            raise CommandError("找不到 .git 目錄，無法建立 Git Tag。")

        check_tag = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag_name}"],
            cwd=str(base_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if check_tag.returncode == 0:
            raise CommandError(f"Git Tag {tag_name} 已存在，無法重複建立。")

        tag_message = f"Release {tag_name}\n\n" + "\n".join(f"- {n}" for n in notes)
        res = subprocess.run(
            ["git", "tag", "-a", tag_name, "-m", tag_message],
            cwd=str(base_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise CommandError(f"建立 Git Tag 失敗：{res.stderr.strip()}")
        self.stdout.write(self.style.SUCCESS(f"已建立 Git Tag：{tag_name}"))
