# CRMServer Project Rules

## 1. 執行權限與環境規範
- 執行本專案內的 Python 腳本（包括 `.\env\Scripts\python.exe`）及一般終端命令 (cmd / powershell) 採自動執行，毋須逐一詢問，直接同意。
- 專案環境評估與查詢工具統一放置於 `antigravity_env_tools/` 資料夾。
- 安裝任何新套件至 Python `env` 虛擬環境時，必須先向使用者詢問並說明用途。
- 時區統一使用 `Asia/Taipei` (`zoneinfo.ZoneInfo("Asia/Taipei")` 或 `django.utils.timezone`)。

## 2. 資料庫結構變更與跨資料庫相容性強制規範 (SQLite / MS-SQL / PostgreSQL)
本專案同時支援 **SQLite**、**MS-SQL (`mssql-django`)** 與 **PostgreSQL (`psycopg2`)** 三種資料庫引擎，且正式環境採用直接更新/覆蓋檔案並透過 `apps.core.auto_migrate` 自動靜默升級 DB 結構的部署模式。每次進行任何涉及 `models.py` 或資料庫的修改時，**必須嚴格遵守以下所有規則**：

1. **強制同步產生並套用 Migration 檔案**：
   - 只要修改了任何 `apps/*/models.py`，必須立即執行：
     - `.\env\Scripts\python.exe manage.py makemigrations`
     - `.\env\Scripts\python.exe manage.py migrate`
   - 任務結束前必須執行 `.\env\Scripts\python.exe manage.py makemigrations --check --dry-run`，確保沒有任何遺漏未產生的 Migration。
   - **嚴禁**手動刪除或竄改已發布的舊 `migrations/000x_*.py` 檔案（這會導致正式環境 `django_migrations` 紀錄對不上而引發表格/欄位衝突），所有結構變更一律透過新增後續的 Migration 檔案完成。

2. **無縫自動遷移欄位預設值規範 (Non-Interactive Safe Defaults)**：
   - 正式環境已有多筆既有商務資料，且自動遷移 (`migrate --noinput`) 在無人工介入下靜默執行。
   - 新增欄位時，**必須明確提供安全的 `default=...`（如 `default=""`、`default=0`、`default=False`、`default=dict`、`default=list`）或設定 `null=True, blank=True`**，絕不可產生需要互動式詢問 one-off default 的 Migration。
   - 若新增 `ForeignKey` 至既有資料表，原則上應允許 `null=True, blank=True`，或提供安全的資料遷移 (`RunPython`)。

3. **跨三種資料庫 (SQLite / MS-SQL / PostgreSQL) 語法與型別相容規範**：
   - **禁止使用特定資料庫專屬的 Raw SQL**：一律優先使用 Django ORM；若必須撰寫原生 SQL，必須同時相容或分別處理 `connection.vendor in ('sqlite', 'microsoft', 'postgresql')`。
   - **MS-SQL 特殊限制防護**：
     - 避免在同一模型出現多重串聯刪除路徑 (Multiple Cascade Paths)：若同一 Model 有多個 `ForeignKey` 指向相同或關聯的上游表，次要關聯請使用 `on_delete=models.SET_NULL, null=True` 或 `on_delete=models.PROTECT`，避免 MS-SQL 報錯 `may cause cycles or multiple cascade paths`。
     - `CharField` 建立 `unique=True` 或索引時，`max_length` 切勿超過 `450`（MS-SQL 索引鍵長度上限為 900 bytes）。
   - **PostgreSQL 特殊限制防護**：
     - 欄位型別轉換（例如 `CharField` 轉 `IntegerField` 或 `JSONField`）不可直接假設隱式轉型成功，必要時應分階段遷移。
     - 凡涉及大量匯入或指定 ID 寫入，需確保呼叫 `reset_target_sequences` 重設 PostgreSQL Sequence。

4. **環境設定與本地資料庫保護**：
   - `db.sqlite3` 與 `db_config.json` 屬於環境執行期狀態，已列入 `.gitignore` 與 `.dockerignore` 保護，嚴禁將開發機的 `db.sqlite3` 或 `db_config.json` 強制加入 Git commit，以免覆蓋正式環境設定與資料庫。

## 3. 版本號管制與發版規範 (Semantic Versioning)
本專案採用 **SemVer 2.0.0 (`MAJOR.MINOR.PATCH`)** 進行版號管制，所有發版與合併主線作業必須遵守以下規範：

1. **單一來源與文件同步**：
   - 根目錄 `VERSION` 為系統唯一版號來源（純文字 `X.Y.Z`），UI（側邊欄底部、登入頁底部）與更新歷程彈窗皆由此檔案與 `CHANGELOG.md` 動態讀取。
   - 每次發版或將功能分支合併至 `main` 時，必須同步更新 `VERSION`、`CHANGELOG.md`，並建立對應的 Annotated Git Tag（格式 `vX.Y.Z`）。

2. **版號遞增規則**：
   - **MAJOR (`X.0.0`)**：不相容的系統架構調整或重大改版。
   - **MINOR (`x.Y.0`)**：向下相容的新功能模組或功能分支（如 `Hank`）合併至 `main`。
   - **PATCH (`x.y.Z`)**：向下相容的錯誤修正（Bugfix）、文件微調或安全性修補。

3. **標準升版操作指令**：
   - 使用內建管理指令自動更新 `VERSION` 與 `CHANGELOG.md`（日期統一採用 `Asia/Taipei`）：
     - `.\env\Scripts\python.exe manage.py bump_version patch --section fixed --note "修正內容"`
     - `.\env\Scripts\python.exe manage.py bump_version minor --section added --note "新增功能"`
     - 確認測試全數通過後，提交變更並建立 Git Tag：`git tag -a vX.Y.Z -m "Release vX.Y.Z"`（或加上 `--tag` 參數）。
