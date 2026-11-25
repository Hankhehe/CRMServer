# CRM Server - 客戶關係、維護合約、軟體授權與硬體資產管理系統

本專案為獨立之企業級 CRM Server，以客戶為核心，整合維護合約、軟體授權、硬體設備庫存 (`apps/hardware`)、簽核流程、證書匯出與企業級 LDAP/AD 雙軌認證，並支援 **SQLite / PostgreSQL / Microsoft SQL Server (MS-SQL)** 三資料庫引擎切換與自動結構升級。

---

## 🌟 核心功能架構

### 1. 👥 客戶與維護合約模組 (`apps/customers`)
* **客戶主檔管理 (`Customer`)**：支援客戶階層樹、唯一客戶代號／統編、雙欄位模糊搜尋、自訂欄位與互動歷程附件。
* **多窗口聯絡人名冊 (`ContactPerson`)**：單一客戶支援多位聯絡人與主要聯絡人同步。
* **維護合約預警中心 (`MaintenanceContract`)**：支援多份歷史／續約合約、附件管理與 **30 天內到期預警**。

### 2. 📦 軟體產品與授權管理模組 (`apps/licenses`)
* **軟體產品主檔 (`Product`)**：管理產品代號、計量單位與動態擴充欄位規格 (JSON Schema)。
* **客戶軟體授權書管理 (`CustomerLicense`)**：支援永久買斷、訂閱與測試授權、到期預警、授權／保固簽核流程與證書匯出。
* **軟硬體雙向勾稽**：硬體出貨時可直接綁定軟體授權書並即時統計配額。

### 3. 🖥️ 硬體設備與庫存模組 (`apps/hardware`)
* **硬體設備庫存主檔 (`HardwareDevice`)**：完整序號追蹤、型號規格、供應商、庫存狀態、借測歸還預警與客製硬體覆寫。
* **庫存異動與出貨簽核 (`InventoryTransaction`, `HardwareOutboundApproval`)**：完整記錄入庫、出貨、借測、歸還、退貨與報廢流程及審計資訊。

### 4. 💼 訂單與報價管理模組 (`apps/orders`)
* **報價單與訂單流程**：支援客戶報價單與訂單建立、明細管理、賣方範本設定與報價單列印。
* **多階段簽核與串接**：支援送審、主管簽核、出貨作業、收款確認與結案，並串接硬體設備、軟體授權與維護合約來源單號。

### 5. ⚙️ 系統核心與安全授權 (`apps/core`)
* **總覽儀表板 (`Dashboard`)**：彙整客戶、合約到期、授權、訂單待辦與硬體庫存警示。
* **企業級 AD / LDAP 雙軌認證與 RBAC**：支援本機帳號與 AD 網域帳號登入、AD 群組對應系統角色。
* **三資料庫熱切換與自動遷移**：支援 SQLite、PostgreSQL、MS-SQL 線上遷移與 `AutoMigrate` 自動升級。
* **系統備份與還原**：支援後台一鍵匯出／還原資料庫與 `media/` 附件備份包。
* **版本號管制 (`VERSION` / `CHANGELOG.md`)**：採用 SemVer 語意化版本管理，登入頁與側邊欄底部即時顯示目前版號，點擊側邊欄版號可查看完整更新歷程。

---

## 🚀 快速開始（首次拉取程式碼）

本專案不包含資料庫 (`db.sqlite3`)、連線設定 (`db_config.json`) 與上傳附件 (`media/*`)。首次 `git clone` 後，依以下步驟即可啟動使用：

### 1. 取得程式碼並建立虛擬環境

**Windows (PowerShell)**
```powershell
git clone https://github.com/Hankhehe/CRMServer.git
cd CRMServer

# 建立並啟動虛擬環境
python -m venv env
.\env\Scripts\Activate.ps1

# 安裝相依套件
pip install -r requirements.txt
```

**Linux / macOS**
```bash
git clone https://github.com/Hankhehe/CRMServer.git
cd CRMServer

python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

### 2. 初始化資料庫與預設角色
若未設定 `db_config.json`，系統預設使用根目錄的 `db.sqlite3`。執行以下指令會自動建立資料庫、所有資料表與內建權限角色：

```powershell
python manage.py migrate
```

### 3. 建立第一位超級管理員帳號
由於全新的資料庫內沒有任何預設帳號，**必須先建立管理員帳號才能登入**：

```powershell
python manage.py createsuperuser
```
> 依提示輸入帳號（例如 `admin`）、Email（可留空）與密碼即可完成。

### 4. 啟動伺服器
```powershell
python manage.py runserver 0.0.0.0:8000
```
啟動後開啟瀏覽器前往：`http://127.0.0.1:8000/login/`，使用剛建立的管理員帳號登入即可開始使用。

---

## ⚙️ 首次登入後建議設定（選用）

登入超級管理員後，可直接在系統後台完成進階配置（皆儲存於資料庫或本機設定檔，無需修改程式碼）：

1. **AD / LDAP 與 SMTP 設定**：於後台系統設定頁面填入 AD 伺服器 URI、Bind DN、密碼與 SMTP 寄信資訊（儲存於資料庫 `SystemSetting`）。
2. **切換外部資料庫（PostgreSQL / MS-SQL）**：於後台「資料庫遷移」頁面輸入連線資訊並執行遷移，系統會自動搬移資料並於根目錄產生 `db_config.json`（已列入 `.gitignore`）。
3. **還原既有資料**：若已有系統備份壓縮包（`.zip`），可於後台「系統備份與還原」頁面直接上傳還原資料庫與 `media/` 附件。

---

## 🔐 正式環境建議環境變數

正式部署時，建議透過環境變數覆蓋以下設定（未設定時仍可於本機開發模式正常啟動）：

| 環境變數 | 說明 |
| :--- | :--- |
| `DJANGO_SECRET_KEY` | Django 簽章密鑰（正式環境務必設定為高強度隨機字串） |
| `CSRF_TRUSTED_ORIGINS` | 允許跨站請求之網域／IP 清單，以逗號分隔（例如 `https://crm.example.com,http://192.168.1.10:8000`） |
| `LDAP_SERVER_URI` | （選用）LDAP fallback 伺服器 URI，系統優先讀取資料庫內的 `SystemSetting` |
| `LDAP_BIND_DN` | （選用）LDAP fallback Bind DN |
| `LDAP_BIND_PASSWORD` | （選用）LDAP fallback Bind Password |
| `LDAP_BASE_DN` | （選用）LDAP fallback Base DN |
| `LDAP_DOMAIN` | （選用）LDAP fallback 網域名稱 |

---

## 🧪 執行測試與檢查

```powershell
# 系統健康檢查
python manage.py check

# 確認沒有遺漏未產生的 Migration
python manage.py makemigrations --check --dry-run

# 執行自動化測試
python manage.py test
```

---

## 🏷️ 版本號管制

本專案採用 **Semantic Versioning 2.0.0 (`MAJOR.MINOR.PATCH`)** 進行版號管制：

* **版號來源**：根目錄 `VERSION` 檔案（由系統 Context Processor 自動注入各頁面）。
* **更新歷程**：根目錄 `CHANGELOG.md`，登入系統後可點擊左側邊欄底部的版本按鈕開啟「系統版本與更新歷程」彈窗檢視。
* **升版工具**：
  ```powershell
  # 預覽修訂版號升級
  python manage.py bump_version patch --dry-run

  # 升級次版本並寫入 CHANGELOG.md
  python manage.py bump_version minor --section added --note "新增功能說明"
  ```
