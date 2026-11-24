from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from apps.core.backup_service import restore_backup


class Command(BaseCommand):
    help = "從指定的 CRM 備份檔 (ZIP) 還原系統資料庫與 media/ 附件檔案"

    def add_arguments(self, parser):
        parser.add_argument(
            "backup_file",
            type=str,
            help="要還原的備份檔案路徑 (例如: backups/CRM_Backup_20261021_081100.zip)",
        )
        parser.add_argument(
            "-y",
            "--yes",
            action="store_true",
            help="直接確認覆蓋還原，略過互動式確認提示",
        )

    def handle(self, *args, **options):
        backup_file_arg = options.get("backup_file")
        skip_confirm = options.get("yes", False)

        backup_path = Path(backup_file_arg)
        if not backup_path.is_file():
            raise CommandError(f"指定的備份檔案不存在: {backup_path.resolve()}")

        if not skip_confirm:
            self.stdout.write(
                self.style.WARNING(
                    "[警告] 還原操作將會完全覆蓋當前的資料庫與所有 media/ 附件檔案！\n"
                    "請確認您是否要繼續執行還原？"
                )
            )
            confirm = input("請輸入 'yes' 確認還原: ").strip().lower()
            if confirm != "yes":
                self.stdout.write(self.style.NOTICE("操作已取消。"))
                return

        self.stdout.write(self.style.NOTICE(f"開始從 {backup_path.name} 執行系統還原..."))

        success, msg = restore_backup(backup_path, user=None)
        if success:
            self.stdout.write(self.style.SUCCESS(f"[OK] {msg}"))
        else:
            raise CommandError(f"還原失敗: {msg}")
