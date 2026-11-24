"""
Automatic Database Schema Migration Detector & Executor.
Supports SQLite, MS-SQL (mssql-django), and PostgreSQL seamlessly.
"""

import logging
import threading
from pathlib import Path
from typing import Tuple

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

logger = logging.getLogger(__name__)

_migration_lock = threading.Lock()
_last_checked_signature: Tuple[Tuple[str, float], ...] | None = None


def get_migrations_disk_signature() -> Tuple[Tuple[str, float], ...]:
    """
    Compute a lightweight signature of all migration files across apps/ on disk.
    Only performs fast file stat checks on apps/*/migrations/*.py.
    """
    apps_dir = Path(settings.BASE_DIR) / "apps"
    records = []
    if apps_dir.exists():
        for mig_file in sorted(apps_dir.glob("*/migrations/*.py")):
            if mig_file.name == "__init__.py":
                continue
            try:
                records.append((str(mig_file.relative_to(apps_dir)), mig_file.stat().st_mtime))
            except OSError:
                continue
    return tuple(records)


def ensure_db_migrated(force_check: bool = False) -> bool:
    """
    Check if there are any unapplied migrations on the default database and
    silently apply them if needed.

    Uses an in-memory disk signature cache so repeated requests incur zero DB overhead
    unless new migration files are added or modified on disk.
    Returns True if migrations were executed, False otherwise.
    """
    global _last_checked_signature

    current_sig = get_migrations_disk_signature()
    if not force_check and _last_checked_signature == current_sig:
        return False

    with _migration_lock:
        # Double-check inside lock
        current_sig = get_migrations_disk_signature()
        if not force_check and _last_checked_signature == current_sig:
            return False

        try:
            connection = connections["default"]
            connection.prepare_database()
            executor = MigrationExecutor(connection)
            targets = executor.loader.graph.leaf_nodes()
            plan = executor.migration_plan(targets)

            if plan:
                pending_names = [f"{mig.app_label}.{mig.name}" for mig, _ in plan]
                logger.info(
                    "Auto-Migrate: Detected %d unapplied migration(s) (%s) on database engine '%s'. Applying now...",
                    len(plan),
                    ", ".join(pending_names),
                    connection.vendor,
                )
                call_command("migrate", database="default", interactive=False, verbosity=1)
                logger.info("Auto-Migrate: Successfully applied all pending database migrations.")
                _last_checked_signature = get_migrations_disk_signature()
                return True

            _last_checked_signature = current_sig
            return False
        except Exception as exc:
            logger.warning("Auto-Migrate: Check or execution encountered an issue: %s", exc)
            return False


class AutoMigrateMiddleware:
    """
    Django Middleware that automatically checks and applies pending database
    migrations when code/migration files are updated on disk (e.g. via volume mount,
    file overwrite, or git pull) without requiring manual `python manage.py migrate`.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Perform initial check when middleware is initialized
        ensure_db_migrated(force_check=False)

    def __call__(self, request):
        ensure_db_migrated(force_check=False)
        return self.get_response(request)
