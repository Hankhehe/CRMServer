"""
Backup & Restore Service for CRMServer
Handles creating and restoring composite backups (DB + Media attachments)
with cross-database engine compatibility (SQLite & MSSQL).
"""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple
import zipfile
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.db import connection, connections
import django

from .permissions import log_action

logger = logging.getLogger(__name__)

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def get_backup_dir() -> Path:
    """Return the Path to the directory where backups are stored."""
    backup_dir = settings.BASE_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def format_file_size(size_in_bytes: int) -> str:
    """Format bytes into human-readable string."""
    if size_in_bytes < 1024:
        return f"{size_in_bytes} B"
    elif size_in_bytes < 1024 * 1024:
        return f"{size_in_bytes / 1024:.1f} KB"
    elif size_in_bytes < 1024 * 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_in_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_current_db_engine() -> str:
    """Identify whether current active DB is sqlite, mssql, or postgres."""
    default_db = settings.DATABASES.get("default", {})
    engine = default_db.get("ENGINE", "").lower()
    if "sqlite" in engine:
        return "sqlite"
    elif "mssql" in engine:
        return "mssql"
    elif "postgres" in engine:
        return "postgres"
    return "other"


def create_backup(
    user: Optional[Any] = None,
    ip_address: Optional[str] = None,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Create a composite backup archive containing:
      1. manifest.json (metadata, timestamp, db engine, checksums)
      2. data.json (Django ORM dumpdata for cross-DB compatibility)
      3. db.sqlite3 (binary snapshot if sqlite is active)
      4. media/ (attachments and user-uploaded files)
    """
    now = datetime.now(TAIPEI_TZ)
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    backup_dir = get_backup_dir()

    if output_path is None:
        target_zip = backup_dir / f"CRM_Backup_{timestamp_str}.zip"
    else:
        target_zip = Path(output_path)
        target_zip.parent.mkdir(parents=True, exist_ok=True)

    db_engine = get_current_db_engine()
    sqlite_path = settings.BASE_DIR / "db.sqlite3"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        data_json_path = temp_path / "data.json"
        manifest_json_path = temp_path / "manifest.json"

        # 1. Generate Django ORM dumpdata JSON
        logger.info("Dumping Django ORM data to JSON...")
        with open(data_json_path, "w", encoding="utf-8") as f:
            call_command(
                "dumpdata",
                exclude=["contenttypes", "auth.permission", "sessions.session"],
                natural_foreign=True,
                natural_primary=True,
                indent=2,
                stdout=f,
            )

        # 2. Count media files & calculate size
        media_count = 0
        media_total_bytes = 0
        media_root = Path(settings.MEDIA_ROOT)
        if media_root.exists():
            for root, _, files in os.walk(media_root):
                for file_name in files:
                    file_path = Path(root) / file_name
                    if file_path.is_file():
                        media_count += 1
                        media_total_bytes += file_path.stat().st_size

        # 3. Create manifest.json
        manifest_data = {
            "version": "1.0",
            "system": "CRMServer",
            "django_version": django.get_version(),
            "created_at": now.isoformat(),
            "db_engine": db_engine,
            "has_sqlite_file": bool(db_engine == "sqlite" and sqlite_path.exists()),
            "has_orm_data": True,
            "media_files_count": media_count,
            "media_total_bytes": media_total_bytes,
        }
        with open(manifest_json_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)

        # 4. Pack into ZIP archive
        logger.info("Packing backup archive %s...", target_zip.name)
        with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Add manifest
            zipf.write(manifest_json_path, arcname="manifest.json")
            # Add ORM data
            zipf.write(data_json_path, arcname="data.json")

            # Add SQLite file if sqlite
            if db_engine == "sqlite" and sqlite_path.exists():
                # Close connection temporarily to ensure WAL / buffer flushed
                connection.close()
                zipf.write(sqlite_path, arcname="db.sqlite3")

            # Add Media directory
            if media_root.exists():
                for root, _, files in os.walk(media_root):
                    for file_name in files:
                        full_path = Path(root) / file_name
                        if full_path.is_file():
                            rel_path = full_path.relative_to(media_root)
                            arcname = Path("media") / rel_path
                            zipf.write(full_path, arcname=str(arcname).replace("\\", "/"))

    # Log action if requested
    if user:
        try:
            log_action(
                user=user,
                action="建立系統備份",
                target_type="System",
                target_identifier=target_zip.name,
                detail=f"建立備份檔案: {target_zip.name} (大小: {format_file_size(target_zip.stat().st_size)})",
                ip_address=ip_address,
            )
        except Exception as e:
            logger.warning("Failed to record audit log: %s", e)

    return target_zip


def list_backups() -> List[Dict[str, Any]]:
    """List all available backup archives in backups/ with metadata."""
    backup_dir = get_backup_dir()
    backups = []

    for file_path in backup_dir.glob("*.zip"):
        try:
            stat_info = file_path.stat()
            file_size_bytes = stat_info.st_size
            mtime_dt = datetime.fromtimestamp(stat_info.st_mtime, tz=TAIPEI_TZ)

            manifest = {}
            if zipfile.is_zipfile(file_path):
                try:
                    with zipfile.ZipFile(file_path, "r") as zf:
                        if "manifest.json" in zf.namelist():
                            with zf.open("manifest.json") as mf:
                                manifest = json.load(mf)
                except Exception:
                    pass

            created_at_str = manifest.get("created_at")
            if created_at_str:
                try:
                    created_dt = datetime.fromisoformat(created_at_str)
                    created_display = created_dt.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    created_display = mtime_dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                created_display = mtime_dt.strftime("%Y-%m-%d %H:%M:%S")

            backups.append(
                {
                    "filename": file_path.name,
                    "filepath": str(file_path),
                    "size_bytes": file_size_bytes,
                    "size_display": format_file_size(file_size_bytes),
                    "created_at_display": created_display,
                    "db_engine": manifest.get("db_engine", "未知"),
                    "media_files_count": manifest.get("media_files_count", 0),
                    "version": manifest.get("version", "1.0"),
                    "mtime": stat_info.st_mtime,
                }
            )
        except Exception as e:
            logger.warning("Error reading backup %s: %s", file_path.name, e)

    # Sort descending by mtime
    backups.sort(key=lambda x: x["mtime"], reverse=True)
    return backups


def get_backup_file(filename: str) -> Optional[Path]:
    """Safely retrieve a backup file path by filename, preventing path traversal."""
    safe_name = Path(filename).name
    if not safe_name.endswith(".zip"):
        return None
    file_path = get_backup_dir() / safe_name
    if file_path.is_file():
        return file_path
    return None


def delete_backup(
    filename: str, user: Optional[Any] = None, ip_address: Optional[str] = None
) -> bool:
    """Delete a backup archive by filename."""
    file_path = get_backup_file(filename)
    if not file_path:
        return False

    try:
        file_path.unlink()
        if user:
            try:
                log_action(
                    user=user,
                    action="刪除系統備份",
                    target_type="System",
                    target_identifier=filename,
                    detail=f"刪除備份檔案: {filename}",
                    ip_address=ip_address,
                )
            except Exception:
                pass
        return True
    except Exception as e:
        logger.error("Failed to delete backup file %s: %s", filename, e)
        return False


def restore_backup(
    zip_path: Path, user: Optional[Any] = None, ip_address: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Restore system state from a composite backup archive:
      - Automatically detects active DB engine (SQLite vs MSSQL).
      - If active is SQLite and db.sqlite3 is in zip: direct binary replacement.
      - If active is MSSQL or db.sqlite3 not in zip: loads data.json via ORM loaddata.
      - Cleans and extracts media/ directory.
    """
    if not zipfile.is_zipfile(zip_path):
        return False, "所提供的檔案不是有效的 ZIP 備份封裝檔。"

    current_db = get_current_db_engine()
    media_root = Path(settings.MEDIA_ROOT)
    sqlite_path = settings.BASE_DIR / "db.sqlite3"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(temp_path)
        except Exception as e:
            return False, f"解壓縮備份檔失敗: {str(e)}"

        manifest_path = temp_path / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                pass

        extracted_sqlite = temp_path / "db.sqlite3"
        extracted_data_json = temp_path / "data.json"
        extracted_media = temp_path / "media"

        # 1. Restore Database
        db_restored = False
        try:
            # Close all current DB connections
            for conn in connections.all():
                conn.close()

            if current_db == "sqlite":
                if extracted_sqlite.exists():
                    logger.info("Restoring SQLite directly from db.sqlite3 binary snapshot...")
                    shutil.copy2(extracted_sqlite, sqlite_path)
                    call_command("migrate", interactive=False, verbosity=0)
                    db_restored = True
                elif extracted_data_json.exists():
                    logger.info("Restoring SQLite via ORM loaddata...")
                    if sqlite_path.exists():
                        sqlite_path.unlink()
                    call_command("migrate", interactive=False, verbosity=0)
                    call_command("loaddata", str(extracted_data_json), verbosity=0)
                    db_restored = True
                else:
                    return False, "備份檔中找不到 db.sqlite3 或 data.json，無法進行資料庫還原。"

            elif current_db in ("mssql", "postgres"):
                if extracted_data_json.exists():
                    logger.info("Restoring %s via ORM loaddata...", current_db.upper())
                    # Ensure schema is migrated, flush existing tables, and load data
                    call_command("migrate", interactive=False, verbosity=0)
                    call_command("flush", interactive=False, verbosity=0)
                    call_command("loaddata", str(extracted_data_json), verbosity=0)
                    if current_db == "postgres":
                        from .db_migrator import reset_target_sequences
                        reset_target_sequences("default")
                    db_restored = True
                else:
                    return False, f"目標系統為 {current_db.upper()} 資料庫，但備份檔內未包含 data.json ORM 資料檔。"
            else:
                if extracted_data_json.exists():
                    call_command("migrate", interactive=False, verbosity=0)
                    call_command("loaddata", str(extracted_data_json), verbosity=0)
                    db_restored = True
                else:
                    return False, "未支援的資料庫引擎且缺少 data.json 資料檔。"

        except Exception as e:
            logger.exception("Database restore error: %s", e)
            return False, f"資料庫還原過程發生錯誤: {str(e)}"

        # 2. Restore Media Files
        try:
            if extracted_media.exists() and extracted_media.is_dir():
                logger.info("Restoring media directory...")
                media_root.mkdir(parents=True, exist_ok=True)
                # Clear existing media contents except .gitkeep
                for item in media_root.iterdir():
                    if item.name == ".gitkeep":
                        continue
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()

                # Copy extracted media files over
                for item in extracted_media.iterdir():
                    dst_item = media_root / item.name
                    if item.is_dir():
                        shutil.copytree(item, dst_item, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dst_item)
        except Exception as e:
            logger.exception("Media restore error: %s", e)
            return False, f"附件檔案還原發生錯誤: {str(e)}"

    # 3. Log Action in restored DB
    try:
        operator_name = user.username if user else "CLI"
        log_action(
            user=None,
            action="還原系統備份",
            target_type="System",
            target_identifier=zip_path.name,
            detail=f"成功還原備份包: {zip_path.name} (操作員: {operator_name})",
            ip_address=ip_address,
        )
    except Exception as e:
        logger.warning("Could not log post-restore action: %s", e)

    return True, "系統資料庫與附件檔案已成功還原！"
