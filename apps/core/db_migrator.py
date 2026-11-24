import json
import logging
import os
import tempfile
from pathlib import Path
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.apps import apps
from django.core.management.color import no_style

logger = logging.getLogger(__name__)

TARGET_ALIAS = "target_migration_alias"


def recreate_target_database(target_type: str, config: dict) -> tuple[bool, str]:
    """
    清除並重新建立目標資料庫 (DROP DATABASE + CREATE DATABASE)
    """
    if target_type == "postgres":
        try:
            import psycopg2
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        except ImportError:
            return False, "伺服器尚未安裝 psycopg2-binary 套件，無法連線 PostgreSQL。"

        host = config.get("host", "127.0.0.1")
        port = int(config.get("port", 5432))
        dbname = config.get("name", "CRMServerDB").strip()
        user = config.get("user", "postgres").strip()
        password = config.get("password", "").strip()

        try:
            # 連線至 PostgreSQL 預設維護庫 postgres
            conn = psycopg2.connect(
                host=host,
                port=port,
                dbname="postgres",
                user=user,
                password=password,
                connect_timeout=10,
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                # 終止目標資料庫現有的所有活躍連線
                cur.execute(
                    """
                    SELECT pg_terminate_backend(pg_stat_activity.pid)
                    FROM pg_stat_activity
                    WHERE pg_stat_activity.datname = %s
                      AND pid <> pg_backend_pid();
                    """,
                    [dbname],
                )
                # 強制刪除並重新建立全新資料庫
                cur.execute(f'DROP DATABASE IF EXISTS "{dbname}";')
                cur.execute(f'CREATE DATABASE "{dbname}" WITH ENCODING \'UTF8\';')
            conn.close()
            return True, f"成功於 PostgreSQL 伺服器 ({host}:{port}) 清空並重建「{dbname}」資料庫！"
        except Exception as e:
            return False, f"PostgreSQL 資料庫清除重建失敗: {str(e)}"

    elif target_type == "mssql":
        try:
            import pyodbc
        except ImportError:
            return False, "伺服器尚未安裝 pyodbc 套件，無法連線 MS SQL Server。"

        host = config.get("host", "").strip()
        port = config.get("port", "1433")
        dbname = config.get("name", "CRMServerDB").strip()
        user = config.get("user", "").strip()
        password = config.get("password", "").strip()
        driver = config.get("driver", "ODBC Driver 18 for SQL Server").strip()

        try:
            conn_str = f"DRIVER={{{driver}}};SERVER={host},{port};UID={user};PWD={password};DATABASE=master;Timeout=10;TrustServerCertificate=yes"
            conn = pyodbc.connect(conn_str, autocommit=True)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    IF DB_ID('{dbname}') IS NOT NULL
                    BEGIN
                        ALTER DATABASE [{dbname}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
                        DROP DATABASE [{dbname}];
                    END
                    CREATE DATABASE [{dbname}] COLLATE Chinese_Taiwan_Stroke_CI_AS;
                    """
                )
            conn.close()
            return True, f"成功於 MS SQL Server ({host}:{port}) 清空並重建「{dbname}」資料庫！"
        except Exception as e:
            return False, f"MS SQL 資料庫清除重建失敗: {str(e)}"

    elif target_type == "sqlite":
        try:
            sqlite_path = settings.BASE_DIR / "db.sqlite3"
            connections.close_all()
            if sqlite_path.exists():
                sqlite_path.unlink()
            return True, "成功清除舊有 SQLite 本地資料庫檔案，準備重新初始化！"
        except Exception as e:
            return False, f"SQLite 清除重建失敗: {str(e)}"

    return False, f"不支援的資料庫類型: {target_type}"


def export_current_data(source_alias="default") -> tuple[Path | None, str]:
    """
    從目前運行中的資料庫完整導出所有商務與帳號資料 (排除 contenttypes, permissions, sessions)
    """
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()

        with open(tmp_path, "w", encoding="utf-8") as f:
            call_command(
                "dumpdata",
                database=source_alias,
                natural_foreign=True,
                natural_primary=True,
                exclude=["contenttypes", "auth.Permission", "sessions"],
                stdout=f,
            )
        return tmp_path, "成功導出目前資料庫完整快照！"
    except Exception as e:
        return None, f"導出目前資料失敗: {str(e)}"


def get_target_db_settings(target_type: str, config: dict) -> dict:
    """
    根據目標資料庫類型與連線參數產生完整的 Django DATABASES 設定字典
    """
    base_settings = {
        "TIME_ZONE": settings.TIME_ZONE if settings.USE_TZ else None,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "AUTOCOMMIT": True,
        "ATOMIC_REQUESTS": False,
        "OPTIONS": {},
    }

    if target_type == "postgres":
        base_settings.update({
            "ENGINE": "django.db.backends.postgresql",
            "NAME": config.get("name", "CRMServerDB").strip(),
            "USER": config.get("user", "postgres").strip(),
            "PASSWORD": config.get("password", "").strip(),
            "HOST": config.get("host", "127.0.0.1").strip(),
            "PORT": str(config.get("port", "5432")).strip(),
        })
    elif target_type == "mssql":
        base_settings.update({
            "ENGINE": "mssql",
            "NAME": config.get("name", "CRMServerDB").strip(),
            "USER": config.get("user", "").strip(),
            "PASSWORD": config.get("password", "").strip(),
            "HOST": config.get("host", "").strip(),
            "PORT": str(config.get("port", "1433")).strip(),
            "OPTIONS": {
                "driver": config.get("driver", "ODBC Driver 18 for SQL Server").strip(),
                "extra_params": "TrustServerCertificate=yes",
            },
        })
    else:  # sqlite
        base_settings.update({
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": settings.BASE_DIR / "db.sqlite3",
        })

    return base_settings


def reset_target_sequences(target_alias: str):
    """
    在 PostgreSQL / MS SQL 匯入完畢後，重設所有資料表的主鍵自動遞增序號 (Sequence Reset)
    """
    try:
        connection = connections[target_alias]
        if connection.vendor == "postgresql":
            commands = []
            for app_config in apps.get_app_configs():
                statements = connection.ops.sequence_reset_sql(
                    no_style(), app_config.get_models()
                )
                commands.extend(statements)
            if commands:
                with connection.cursor() as cursor:
                    for sql in commands:
                        cursor.execute(sql)
    except Exception as e:
        logger.warning(f"Reset sequences failed: {e}")


def execute_full_database_migration_and_switch(
    target_type: str, config: dict
) -> tuple[bool, str]:
    """
    執行全套資料庫清除、重建、資料轉移與熱切換流程：
    1. 導出目前運作中資料庫的所有商務資料
    2. 清空並重建目標資料庫 (DROP + CREATE DATABASE)
    3. 連線目標庫執行 migrate 建立包含 django_session 在內的所有資料表
    4. 將導出資料匯入目標庫 (loaddata) 並重整主鍵序號
    5. 更新 db_config.json 與記憶體連線池完成即時熱切換
    """
    # 步驟 1：導出當前資料庫所有現有資料
    fixture_path, export_msg = export_current_data(source_alias="default")
    if not fixture_path:
        return False, export_msg

    try:
        # 步驟 2：清除並重建目標資料庫
        recreate_ok, recreate_msg = recreate_target_database(target_type, config)
        if not recreate_ok:
            return False, recreate_msg

        # 步驟 3：配置動態連線別名至全新目標庫
        target_db_dict = get_target_db_settings(target_type, config)
        settings.DATABASES[TARGET_ALIAS] = target_db_dict
        connections.databases[TARGET_ALIAS] = target_db_dict

        # 確保關閉該別名任何殘留連線
        try:
            connections[TARGET_ALIAS].close()
        except Exception:
            pass

        # 執行 Migrate 建立目標庫的所有資料表結構 (包含 sessions 等)
        try:
            call_command("migrate", database=TARGET_ALIAS, interactive=False, verbosity=0)
        except Exception as e:
            return False, f"對目標資料庫套用 Migrations 失敗: {str(e)}"

        # 步驟 4：將來源資料完整匯入至目標資料庫
        try:
            call_command("loaddata", str(fixture_path), database=TARGET_ALIAS, verbosity=0)
        except Exception as e:
            return False, f"將現有資料匯入目標資料庫失敗: {str(e)}"

        # 重整主鍵序號
        reset_target_sequences(TARGET_ALIAS)

        # 步驟 5：寫入持久設定檔 db_config.json
        db_config_data = {}
        if settings.DB_CONFIG_PATH.exists():
            try:
                with open(settings.DB_CONFIG_PATH, "r", encoding="utf-8") as f:
                    db_config_data = json.load(f)
            except Exception:
                pass

        db_config_data["current_db"] = target_type
        if target_type == "postgres":
            db_config_data["postgres"] = config
        elif target_type == "mssql":
            db_config_data["mssql"] = config

        with open(settings.DB_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(db_config_data, f, indent=4, ensure_ascii=False)

        # 步驟 6：Django 記憶體即時熱切換 (Hot Swap)
        settings.DATABASES["default"] = target_db_dict
        connections.databases["default"] = target_db_dict
        connections.close_all()

        try:
            from .auto_migrate import ensure_db_migrated
            ensure_db_migrated(force_check=True)
        except Exception:
            pass

        target_name_display = {
            "postgres": f"PostgreSQL ({config.get('name', 'CRMServerDB')})",
            "mssql": f"MS SQL Server ({config.get('name', 'CRMServerDB')})",
            "sqlite": "SQLite 本地資料庫",
        }.get(target_type, target_type)

        return True, f"全自動轉移成功！目標資料庫已重新建立、資料表已套用、現有資料已無損轉入，系統已即時熱切換為「{target_name_display}」！"

    finally:
        # 清理暫存檔案與別名
        if fixture_path and fixture_path.exists():
            try:
                fixture_path.unlink()
            except Exception:
                pass
        try:
            connections[TARGET_ALIAS].close()
        except Exception:
            pass
        settings.DATABASES.pop(TARGET_ALIAS, None)
        connections.databases.pop(TARGET_ALIAS, None)
