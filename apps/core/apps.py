import sys
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "系統核心與權限"

    def ready(self):
        # Skip auto-migrate when running management commands that manage schema/tests themselves
        skip_commands = {
            "makemigrations",
            "migrate",
            "showmigrations",
            "sqlmigrate",
            "squashmigrations",
            "test",
            "collectstatic",
            "check",
            "shell",
            "dbshell",
            "dumpdata",
            "loaddata",
            "flush",
            "restore_system",
        }
        if len(sys.argv) > 1 and sys.argv[1] in skip_commands:
            return

        from .auto_migrate import ensure_db_migrated
        ensure_db_migrated(force_check=False)

