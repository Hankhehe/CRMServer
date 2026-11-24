from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from apps.core.backup_service import create_backup, format_file_size


class Command(BaseCommand):
    help = "建立 CRM 系統完整備份 (包含資料庫與 media/ 附件檔案)"

    def add_arguments(self, parser):
        parser.add_argument(
            "-o",
            "--output",
            type=str,
            help="指定備份檔案輸出路徑 (預設儲存於 backups/ 目錄)",
        )

    def handle(self, *args, **options):
        output_arg = options.get("output")
        output_path = Path(output_arg) if output_arg else None

        self.stdout.write(self.style.NOTICE("開始執行 CRM 系統備份..."))

        try:
            target_zip = create_backup(user=None, output_path=output_path)
            file_size_display = format_file_size(target_zip.stat().st_size)
            self.stdout.write(
                self.style.SUCCESS(
                    f"[OK] 系統備份成功完成！\n"
                    f"  備份檔案: {target_zip.resolve()}\n"
                    f"  檔案大小: {file_size_display}"
                )
            )
        except Exception as e:
            raise CommandError(f"備份失敗: {str(e)}")
