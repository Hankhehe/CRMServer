"""Tests for SemVer governance, changelog parsing, UI version display, and bump_version."""

from datetime import datetime
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from apps.core import versioning

User = get_user_model()


class VersioningUnitTests(TestCase):
    def test_version_file_reads_expected_semver(self):
        version = versioning.get_app_version()
        major, minor, patch = versioning.parse_semver(version)
        self.assertEqual(version, f"{major}.{minor}.{patch}")
        self.assertEqual(versioning.parse_semver("v1.2.3"), (1, 2, 3))
        self.assertEqual(versioning.parse_semver(" 2.0.10 "), (2, 0, 10))
        with self.assertRaises(ValueError):
            versioning.parse_semver("1.2")
        with self.assertRaises(ValueError):
            versioning.parse_semver("invalid")

    def test_parse_changelog_returns_releases_and_escapes_html(self):
        current_ver = versioning.get_app_version()
        releases = versioning.parse_changelog()
        versions = [r["version"] for r in releases]
        self.assertIn(current_ver, versions)
        self.assertIn("1.0.0", versions)

        latest = releases[0]
        self.assertEqual(latest["version"], current_ver)
        self.assertEqual(latest["display_version"], f"v{current_ver}")
        self.assertTrue(latest["date"])
        self.assertTrue(latest["sections"])

        # Verify XSS-safe inline markdown rendering
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_changelog = Path(tmpdir) / "CHANGELOG.md"
            tmp_changelog.write_text(
                "## [1.1.0] - 2026-11-25\n"
                "### 新增 (Added)\n"
                "- 支援 **粗體** 與 `code` 以及 <script>alert(1)</script>\n",
                encoding="utf-8",
            )
            with patch.object(versioning, "get_changelog_file", return_value=tmp_changelog):
                parsed = versioning.parse_changelog()
                item_html = str(parsed[0]["sections"][0]["items"][0])
                self.assertIn("<strong>粗體</strong>", item_html)
                self.assertIn("<code>code</code>", item_html)
                self.assertNotIn("<script>", item_html)
                self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", item_html)


class VersioningUITests(TestCase):
    def setUp(self):
        self.regular_user = User.objects.create_user(
            username="regular_ver_user",
            password="Password123!",
        )
        self.superuser = User.objects.create_superuser(
            username="super_ver_user",
            email="admin@example.com",
            password="Password123!",
        )

    def tearDown(self):
        versioning.get_git_metadata.cache_clear()

    def test_login_page_displays_version_without_modal_or_git_metadata(self):
        current_ver = versioning.get_app_version()
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"CRM Server v{current_ver}")
        self.assertNotContains(resp, "version-info-modal-overlay")
        self.assertNotContains(resp, "Commit:")

    def test_authenticated_regular_user_sees_sidebar_version_and_modal_without_git(self):
        current_ver = versioning.get_app_version()
        self.client.force_login(self.regular_user)
        with patch.object(
            versioning,
            "get_git_metadata",
            return_value={"commit": "abc1234", "branch": "main"},
        ):
            resp = self.client.get(reverse("account-center"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"版本 v{current_ver}")
        self.assertContains(resp, "version-info-modal-overlay")
        self.assertContains(resp, "系統版本與更新歷程")
        self.assertNotContains(resp, "Commit: <code>abc1234</code>")

    def test_authenticated_superuser_sees_git_metadata_when_available_and_hides_when_empty(self):
        current_ver = versioning.get_app_version()
        self.client.force_login(self.superuser)
        with patch.object(
            versioning,
            "get_git_metadata",
            return_value={"commit": "abc1234", "branch": "main"},
        ):
            resp = self.client.get(reverse("account-center"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"版本 v{current_ver}")
        self.assertContains(resp, "Commit: <code>abc1234</code>")
        self.assertContains(resp, "Branch: <code>main</code>")

        # Simulate Docker environment without .git
        with patch.object(
            versioning,
            "get_git_metadata",
            return_value={"commit": "", "branch": ""},
        ):
            resp_no_git = self.client.get(reverse("account-center"))
        self.assertEqual(resp_no_git.status_code, 200)
        self.assertContains(resp_no_git, f"版本 v{current_ver}")
        self.assertContains(resp_no_git, "version-info-modal-overlay")
        self.assertNotContains(resp_no_git, "Commit:")


class BumpVersionCommandTests(TestCase):
    def test_bump_version_dry_run_does_not_modify_real_files(self):
        current_ver = versioning.get_app_version()
        major, minor, patch_num = versioning.parse_semver(current_ver)
        expected_next = f"{major}.{minor}.{patch_num + 1}"
        out = StringIO()
        call_command("bump_version", "patch", "--dry-run", stdout=out)
        output = out.getvalue()
        self.assertIn(f"{current_ver} -> {expected_next}", output)
        self.assertEqual(versioning.get_app_version(), current_ver)

    def test_bump_version_minor_updates_temp_version_and_changelog(self):
        today_taipei = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_version = Path(tmpdir) / "VERSION"
            tmp_changelog = Path(tmpdir) / "CHANGELOG.md"
            tmp_version.write_text("1.1.0\n", encoding="utf-8")
            tmp_changelog.write_text(
                "# 版本更新歷程 (Changelog)\n\n"
                "## [1.1.0] - 2026-11-25\n"
                "### 新增 (Added)\n"
                "- 既有功能\n",
                encoding="utf-8",
            )

            out = StringIO()
            with (
                patch.object(versioning, "get_version_file", return_value=tmp_version),
                patch.object(versioning, "get_changelog_file", return_value=tmp_changelog),
            ):
                call_command(
                    "bump_version",
                    "minor",
                    "--section",
                    "added",
                    "--note",
                    "測試新功能",
                    stdout=out,
                )

                self.assertEqual(tmp_version.read_text(encoding="utf-8").strip(), "1.2.0")
                changelog_text = tmp_changelog.read_text(encoding="utf-8")
                expected_heading = f"## [1.2.0] - {today_taipei}"
                self.assertIn(expected_heading, changelog_text)
                self.assertIn("### 新增 (Added)\n- 測試新功能", changelog_text)
                self.assertLess(
                    changelog_text.index(expected_heading),
                    changelog_text.index("## [1.1.0]"),
                )

                # Reject duplicate version
                with self.assertRaises(CommandError):
                    call_command("bump_version", "--set", "1.1.0", stdout=StringIO())
