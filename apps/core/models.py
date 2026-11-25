from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Role(models.Model):
    SYSTEM_PRESETS = {
        "all_admin": {
            "name": "全域超級管理員",
            "description": "擁有全系統所有模組最高編輯、簽核覆核與系統全域管理權限",
            "can_view_crm": True,
            "can_edit_customers": True,
            "can_edit_licenses": True,
            "can_edit_products": True,
            "can_edit_probes": True,
            "can_manage_system": True,
            "can_approve_licenses": True,
            "can_approve_contracts": True,
            "can_approve_hardware": True,
            "can_edit_orders": True,
            "can_approve_orders": True,
            "can_ship_orders": True,
            "can_close_orders": True,
            "customer_scope": "all",
        },
        "sys_admin": {
            "name": "CRM 系統維護人員",
            "description": "管理全系統 Config 設定、軟體產品主檔與硬體規格主檔",
            "can_view_crm": True,
            "can_edit_customers": False,
            "can_edit_licenses": False,
            "can_edit_products": True,
            "can_edit_probes": False,
            "can_manage_system": True,
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit_orders": False,
            "can_approve_orders": False,
            "can_ship_orders": False,
            "can_close_orders": False,
            "customer_scope": "all",
        },
        "customer_admin": {
            "name": "客戶資料管理員",
            "description": "專責建檔與維護客戶名冊、各分公司環境資訊與聯絡窗口",
            "can_view_crm": True,
            "can_edit_customers": True,
            "can_edit_licenses": False,
            "can_edit_products": False,
            "can_edit_probes": False,
            "can_manage_system": False,
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit_orders": False,
            "can_approve_orders": False,
            "can_ship_orders": False,
            "can_close_orders": False,
            "customer_scope": "all",
        },
        "license_admin": {
            "name": "授權與保固開立人員",
            "description": "專責開立與維護軟體授權書、維護保固合約及換機 RMA",
            "can_view_crm": True,
            "can_edit_customers": False,
            "can_edit_licenses": True,
            "can_edit_products": False,
            "can_edit_probes": False,
            "can_manage_system": False,
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit_orders": False,
            "can_approve_orders": False,
            "can_ship_orders": False,
            "can_close_orders": False,
            "customer_scope": "all",
        },
        "probe_admin": {
            "name": "設備庫存管理人員",
            "description": "專責管理硬體設備進出貨交易、借測歸還、規格主檔與訂單出貨交付",
            "can_view_crm": True,
            "can_edit_customers": False,
            "can_edit_licenses": False,
            "can_edit_products": False,
            "can_edit_probes": True,
            "can_manage_system": False,
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit_orders": False,
            "can_approve_orders": False,
            "can_ship_orders": True,
            "can_close_orders": False,
            "customer_scope": "all",
        },
        "sales_rep": {
            "name": "業務專員",
            "description": "負責建立客戶報價訂單、議價調整、上傳回簽報價單送審與追蹤收款回報（業務隔離視野）",
            "can_view_crm": True,
            "can_edit_customers": True,
            "can_edit_licenses": False,
            "can_edit_products": False,
            "can_edit_probes": False,
            "can_manage_system": False,
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit_orders": True,
            "can_approve_orders": False,
            "can_ship_orders": False,
            "can_close_orders": False,
            "customer_scope": "followed",
        },
        "sales_manager": {
            "name": "業務主管",
            "description": "審核業務報價訂單、核准開立授權與保固並可協助退回或作廢異常訂單",
            "can_view_crm": True,
            "can_edit_customers": True,
            "can_edit_licenses": False,
            "can_edit_products": False,
            "can_edit_probes": False,
            "can_manage_system": False,
            "can_approve_licenses": True,
            "can_approve_contracts": True,
            "can_approve_hardware": False,
            "can_edit_orders": True,
            "can_approve_orders": True,
            "can_ship_orders": False,
            "can_close_orders": False,
            "customer_scope": "all",
        },
        "finance_admin": {
            "name": "管理部 / 財務帳務人員",
            "description": "專責核對客戶訂單匯款入帳憑證並將已收齊款項之訂單結案",
            "can_view_crm": True,
            "can_edit_customers": False,
            "can_edit_licenses": False,
            "can_edit_products": False,
            "can_edit_probes": False,
            "can_manage_system": False,
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit_orders": False,
            "can_approve_orders": False,
            "can_ship_orders": False,
            "can_close_orders": True,
            "customer_scope": "all",
        },
        "view": {
            "name": "系統操作 / 資源查詢人員",
            "description": "全系統資源唯讀查詢，可於客戶詳情頁留下溝通軌跡與留言",
            "can_view_crm": True,
            "can_edit_customers": False,
            "can_edit_licenses": False,
            "can_edit_products": False,
            "can_edit_probes": False,
            "can_manage_system": False,
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit_orders": False,
            "can_approve_orders": False,
            "can_ship_orders": False,
            "can_close_orders": False,
            "customer_scope": "all",
        },
        "hw_viewer": {
            "name": "硬體設備查詢人員",
            "description": "僅查詢硬體設備庫存與資產，隱藏 CRM 客戶合約相關選單",
            "can_view_crm": False,
            "can_edit_customers": False,
            "can_edit_licenses": False,
            "can_edit_products": False,
            "can_edit_probes": False,
            "can_manage_system": False,
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit_orders": False,
            "can_approve_orders": False,
            "can_ship_orders": False,
            "can_close_orders": False,
            "customer_scope": "all",
        },
    }

    name = models.CharField(max_length=100, unique=True, verbose_name="角色名稱")
    code = models.SlugField(max_length=50, unique=True, verbose_name="角色代碼")
    description = models.TextField(blank=True, verbose_name="職責描述與備註")
    is_system = models.BooleanField(default=False, verbose_name="系統內建角色 (不可刪除)")

    # 五大功能模組開關
    can_view_crm = models.BooleanField(default=True, verbose_name="可瀏覽 CRM 選單")
    can_edit_customers = models.BooleanField(default=False, verbose_name="可編輯客戶資料與聯絡人")
    can_edit_licenses = models.BooleanField(default=False, verbose_name="可開立授權書與保固合約")
    can_edit_products = models.BooleanField(default=False, verbose_name="可維護軟體產品主檔")
    can_edit_probes = models.BooleanField(default=False, verbose_name="可管理硬體庫存與規格")
    can_manage_system = models.BooleanField(default=False, verbose_name="可管理系統全域設定")

    # 三大簽核覆核權限開關
    can_approve_licenses = models.BooleanField(default=False, verbose_name="可覆核軟體授權書")
    can_approve_contracts = models.BooleanField(default=False, verbose_name="可覆核維護保固合約")
    can_approve_hardware = models.BooleanField(default=False, verbose_name="可覆核硬體設備出庫")
    can_edit_orders = models.BooleanField(default=False, verbose_name="可建立與編輯訂單/報價與回報收款 (業務)")
    can_approve_orders = models.BooleanField(default=False, verbose_name="可審核訂單與核准開單 (業務主管)")
    can_ship_orders = models.BooleanField(default=False, verbose_name="可執行訂單出貨與交付確認 (工程師)")
    can_close_orders = models.BooleanField(default=False, verbose_name="可確認訂單款項入帳與結案 (管理部)")

    # 訂單與報價流程權限開關
    can_edit_orders = models.BooleanField(default=False, verbose_name="可建立與編輯訂單/報價與回報收款 (業務)")
    can_approve_orders = models.BooleanField(default=False, verbose_name="可審核訂單與核准開單 (業務主管)")
    can_ship_orders = models.BooleanField(default=False, verbose_name="可執行訂單出貨與交付確認 (工程師)")
    can_close_orders = models.BooleanField(default=False, verbose_name="可確認訂單款項入帳與結案 (管理部)")

    # 客戶資料視野範圍 (Data Scope)
    customer_scope = models.CharField(
        max_length=20,
        choices=[
            ("all", "全域可見 (可查看全公司所有客戶)"),
            ("followed", "僅限已關注客戶 (業務隔離模式)"),
        ],
        default="all",
        verbose_name="客戶資料視野範圍",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "自訂系統角色"
        verbose_name_plural = "自訂系統角色"
        ordering = ["-is_system", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def active_permission_labels(self) -> list:
        labels = []
        if self.can_edit_customers:
            labels.append("客戶資料")
        if self.can_edit_licenses:
            labels.append("授權與保固")
        if self.can_edit_products:
            labels.append("軟體主檔")
        if self.can_edit_probes:
            labels.append("硬體庫存")
        if self.can_manage_system:
            labels.append("系統設定")
        if self.can_approve_licenses:
            labels.append("授權覆核")
        if self.can_approve_contracts:
            labels.append("保固覆核")
        if self.can_approve_hardware:
            labels.append("硬體出庫覆核")
        if self.can_edit_orders:
            labels.append("訂單報價")
        if self.can_approve_orders:
            labels.append("訂單審核")
        if self.can_ship_orders:
            labels.append("訂單出貨")
        if self.can_close_orders:
            labels.append("收款結案")
        if not labels and self.can_view_crm:
            labels.append("一般操作查詢")
        elif not labels and not self.can_view_crm:
            labels.append("硬體專用查詢")
        return labels

    @classmethod
    def ensure_system_roles(cls):
        """確保系統預設角色存在於資料庫中"""
        for code, data in cls.SYSTEM_PRESETS.items():
            obj, created = cls.objects.get_or_create(
                code=code,
                defaults={
                    "name": data["name"],
                    "description": data["description"],
                    "is_system": True,
                    "can_view_crm": data["can_view_crm"],
                    "can_edit_customers": data["can_edit_customers"],
                    "can_edit_licenses": data["can_edit_licenses"],
                    "can_edit_products": data["can_edit_products"],
                    "can_edit_probes": data["can_edit_probes"],
                    "can_manage_system": data["can_manage_system"],
                    "can_approve_licenses": data.get("can_approve_licenses", False),
                    "can_approve_contracts": data.get("can_approve_contracts", False),
                    "can_approve_hardware": data.get("can_approve_hardware", False),
                    "can_edit_orders": data.get("can_edit_orders", False),
                    "can_approve_orders": data.get("can_approve_orders", False),
                    "can_ship_orders": data.get("can_ship_orders", False),
                    "can_close_orders": data.get("can_close_orders", False),
                    "customer_scope": data["customer_scope"],
                },
            )
            if not created and code == "all_admin":
                # 確保系統內建 all_admin 永遠保有完整覆核與訂單流程權限
                updated_fields = []
                for f in (
                    "can_approve_licenses",
                    "can_approve_contracts",
                    "can_approve_hardware",
                    "can_edit_orders",
                    "can_approve_orders",
                    "can_ship_orders",
                    "can_close_orders",
                ):
                    if not getattr(obj, f):
                        setattr(obj, f, True)
                        updated_fields.append(f)
                if updated_fields:
                    obj.save(update_fields=updated_fields + ["updated_at"])
            elif not created and code == "probe_admin" and not obj.can_ship_orders:
                obj.can_ship_orders = True
                obj.save(update_fields=["can_ship_orders", "updated_at"])


class UserProfile(models.Model):
    ROLE_ALL = "all_admin"
    ROLE_SYS_ADMIN = "sys_admin"
    ROLE_SYS = "sys_admin"
    ROLE_CUSTOMER_ADMIN = "customer_admin"
    ROLE_LICENSE_ADMIN = "license_admin"
    ROLE_PROBE = "probe_admin"
    ROLE_SALES_REP = "sales_rep"
    ROLE_SALES_MANAGER = "sales_manager"
    ROLE_FINANCE = "finance_admin"
    ROLE_VIEW = "view"
    ROLE_HW_VIEWER = "hw_viewer"
    ROLE_CRM = "crm_admin"  # 相容舊版：CRM 與授權合約複合管理員
    ROLE_CUSTOM = "custom"

    ROLE_CHOICES = [
        (ROLE_ALL, "全域超級管理員 (可編輯所有模組與系統設定)"),
        (ROLE_SYS_ADMIN, "CRM 系統維護人員 (管理系統 Config、軟體產品與硬體規格主檔)"),
        (ROLE_CUSTOMER_ADMIN, "客戶資料管理員 (管理客戶名冊、聯絡窗口與環境資訊)"),
        (ROLE_LICENSE_ADMIN, "授權與保固開立人員 (開立軟體授權書與維護保固合約)"),
        (ROLE_PROBE, "設備庫存管理人員 (管理硬體設備進出貨、規格主檔與訂單出貨)"),
        (ROLE_SALES_REP, "業務專員 (建立訂單報價、回報收款，僅限已關注客戶)"),
        (ROLE_SALES_MANAGER, "業務主管 (審核業務訂單、覆核授權與保固)"),
        (ROLE_FINANCE, "管理部 / 財務帳務人員 (核對訂單入帳並結案)"),
        (ROLE_VIEW, "系統操作 / 資源查詢人員 (查詢全系統資源、可留客戶執行軌跡)"),
        (ROLE_HW_VIEWER, "硬體設備查詢人員 (僅限查詢硬體設備庫存與資產選單)"),
        (ROLE_CUSTOM, "自訂組合 (自選模組權限 / 跨角色兼任)"),
        (ROLE_CRM, "CRM 綜合管理員 (舊版相容：客戶、授權、合約完整管理)"),
    ]

    SCOPE_ALL = "all"
    SCOPE_FOLLOWED = "followed"
    CUSTOMER_SCOPE_CHOICES = [
        (SCOPE_ALL, "全域可見 (可查看全公司所有客戶)"),
        (SCOPE_FOLLOWED, "僅限已關注客戶 (業務隔離模式：僅能查閱自己關注/負責之客戶)"),
    ]

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="profile"
    )
    distinguished_name = models.CharField(max_length=500, blank=True, verbose_name="AD DN")
    ou_path = models.CharField(max_length=500, blank=True, verbose_name="OU 路徑")
    ad_groups = models.TextField(blank=True, verbose_name="AD 群組清單")
    role_obj = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        verbose_name="指派自訂角色",
    )
    role = models.CharField(
        max_length=30, choices=ROLE_CHOICES, default=ROLE_VIEW, verbose_name="系統角色"
    )
    can_view_crm = models.BooleanField(
        default=True, verbose_name="可瀏覽 CRM 客戶/授權/合約選單"
    )
    can_edit_customers = models.BooleanField(
        default=False, verbose_name="可編輯客戶基本資料與聯絡人"
    )
    can_edit_licenses = models.BooleanField(
        default=False, verbose_name="可開立/編輯授權書與保固合約"
    )
    can_edit_products = models.BooleanField(
        default=False, verbose_name="可維護軟體產品主檔"
    )
    can_edit_crm = models.BooleanField(default=False, verbose_name="可編輯 CRM/合約 (相容旗標)")
    can_edit_probes = models.BooleanField(default=False, verbose_name="可編輯硬體設備")
    can_manage_system = models.BooleanField(default=False, verbose_name="可管理系統設定")
    can_approve_licenses = models.BooleanField(default=False, verbose_name="可覆核軟體授權書")
    can_approve_contracts = models.BooleanField(default=False, verbose_name="可覆核維護保固合約")
    can_approve_hardware = models.BooleanField(default=False, verbose_name="可覆核硬體設備出庫")
    can_edit_orders = models.BooleanField(default=False, verbose_name="可建立與編輯訂單/報價與回報收款 (業務)")
    can_approve_orders = models.BooleanField(default=False, verbose_name="可審核訂單與核准開單 (業務主管)")
    can_ship_orders = models.BooleanField(default=False, verbose_name="可執行訂單出貨與交付確認 (工程師)")
    can_close_orders = models.BooleanField(default=False, verbose_name="可確認訂單款項入帳與結案 (管理部)")
    customer_scope = models.CharField(
        max_length=20,
        choices=CUSTOMER_SCOPE_CHOICES,
        default=SCOPE_ALL,
        verbose_name="客戶資料視野範圍",
    )
    receive_email_notifications = models.BooleanField(
        default=True, verbose_name="接收關注客戶 Email 通知 (總開關)"
    )
    notify_on_new_comment = models.BooleanField(
        default=True, verbose_name="當關注客戶有新留言/執行軌跡時通知我"
    )
    notify_on_data_change = models.BooleanField(
        default=True, verbose_name="當關注客戶有資料/合約/資產變更時通知我"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "使用者權限設定"
        verbose_name_plural = "使用者權限設定"

    def __str__(self) -> str:
        return f"{self.user.username} ({self.get_role_display()})"

    @classmethod
    def get_role_default_flags(cls, role: str, is_superuser: bool = False) -> dict:
        """根據所選系統角色回傳預設的細粒度權限旗標字典"""
        if is_superuser or role == cls.ROLE_ALL:
            return {
                "can_view_crm": True,
                "can_edit_customers": True,
                "can_edit_licenses": True,
                "can_edit_products": True,
                "can_edit_crm": True,
                "can_edit_probes": True,
                "can_manage_system": True,
                "can_approve_licenses": True,
                "can_approve_contracts": True,
                "can_approve_hardware": True,
                "can_edit_orders": True,
                "can_approve_orders": True,
                "can_ship_orders": True,
                "can_close_orders": True,
            }
        base_false_approvals = {
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit_orders": False,
            "can_approve_orders": False,
            "can_ship_orders": False,
            "can_close_orders": False,
        }
        if role == cls.ROLE_SYS_ADMIN:
            return {
                "can_view_crm": True,
                "can_edit_customers": False,
                "can_edit_licenses": False,
                "can_edit_products": True,
                "can_edit_crm": False,
                "can_edit_probes": False,
                "can_manage_system": True,
                **base_false_approvals,
            }
        if role == cls.ROLE_CUSTOMER_ADMIN:
            return {
                "can_view_crm": True,
                "can_edit_customers": True,
                "can_edit_licenses": False,
                "can_edit_products": False,
                "can_edit_crm": False,
                "can_edit_probes": False,
                "can_manage_system": False,
                **base_false_approvals,
            }
        if role == cls.ROLE_LICENSE_ADMIN:
            return {
                "can_view_crm": True,
                "can_edit_customers": False,
                "can_edit_licenses": True,
                "can_edit_products": False,
                "can_edit_crm": False,
                "can_edit_probes": False,
                "can_manage_system": False,
                **base_false_approvals,
            }
        if role == cls.ROLE_PROBE:
            return {
                "can_view_crm": True,
                "can_edit_customers": False,
                "can_edit_licenses": False,
                "can_edit_products": False,
                "can_edit_crm": False,
                "can_edit_probes": True,
                "can_manage_system": False,
                **base_false_approvals,
                "can_ship_orders": True,
            }
        if role == cls.ROLE_SALES_REP:
            return {
                "can_view_crm": True,
                "can_edit_customers": True,
                "can_edit_licenses": False,
                "can_edit_products": False,
                "can_edit_crm": False,
                "can_edit_probes": False,
                "can_manage_system": False,
                **base_false_approvals,
                "can_edit_orders": True,
            }
        if role == cls.ROLE_SALES_MANAGER:
            return {
                "can_view_crm": True,
                "can_edit_customers": True,
                "can_edit_licenses": False,
                "can_edit_products": False,
                "can_edit_crm": False,
                "can_edit_probes": False,
                "can_manage_system": False,
                **base_false_approvals,
                "can_approve_licenses": True,
                "can_approve_contracts": True,
                "can_edit_orders": True,
                "can_approve_orders": True,
            }
        if role == cls.ROLE_FINANCE:
            return {
                "can_view_crm": True,
                "can_edit_customers": False,
                "can_edit_licenses": False,
                "can_edit_products": False,
                "can_edit_crm": False,
                "can_edit_probes": False,
                "can_manage_system": False,
                **base_false_approvals,
                "can_close_orders": True,
            }
        if role == cls.ROLE_HW_VIEWER:
            return {
                "can_view_crm": False,
                "can_edit_customers": False,
                "can_edit_licenses": False,
                "can_edit_products": False,
                "can_edit_crm": False,
                "can_edit_probes": False,
                "can_manage_system": False,
                **base_false_approvals,
            }
        if role == cls.ROLE_CRM:
            return {
                "can_view_crm": True,
                "can_edit_customers": True,
                "can_edit_licenses": True,
                "can_edit_products": True,
                "can_edit_crm": True,
                "can_edit_probes": False,
                "can_manage_system": False,
                **base_false_approvals,
                "can_edit_orders": True,
            }
        if role == cls.ROLE_CUSTOM:
            return {
                "can_view_crm": True,
                "can_edit_customers": False,
                "can_edit_licenses": False,
                "can_edit_products": False,
                "can_edit_crm": False,
                "can_edit_probes": False,
                "can_manage_system": False,
                **base_false_approvals,
            }
        # ROLE_VIEW (系統操作 / 資源查詢人員)
        return {
            "can_view_crm": True,
            "can_edit_customers": False,
            "can_edit_licenses": False,
            "can_edit_products": False,
            "can_edit_crm": False,
            "can_edit_probes": False,
            "can_manage_system": False,
            **base_false_approvals,
        }

    @classmethod
    def match_role_from_flags(cls, flags: dict) -> str:
        """
        比對給定的細部權限旗標字典是否完全符合某個標準角色範本。
        若符合則回傳該角色代碼；若為自訂或跨角色兼任組合，則回傳 ROLE_CUSTOM。
        """
        standard_roles = [
            cls.ROLE_ALL,
            cls.ROLE_SYS_ADMIN,
            cls.ROLE_CUSTOMER_ADMIN,
            cls.ROLE_LICENSE_ADMIN,
            cls.ROLE_PROBE,
            cls.ROLE_SALES_REP,
            cls.ROLE_SALES_MANAGER,
            cls.ROLE_FINANCE,
            cls.ROLE_HW_VIEWER,
            cls.ROLE_CRM,
            cls.ROLE_VIEW,
        ]
        comparison_keys = [
            "can_view_crm",
            "can_edit_customers",
            "can_edit_licenses",
            "can_edit_products",
            "can_edit_probes",
            "can_manage_system",
            "can_approve_licenses",
            "can_approve_contracts",
            "can_approve_hardware",
            "can_edit_orders",
            "can_approve_orders",
            "can_ship_orders",
            "can_close_orders",
        ]
        for r in standard_roles:
            r_defaults = cls.get_role_default_flags(r)
            if all(bool(flags.get(k)) == bool(r_defaults.get(k)) for k in comparison_keys):
                return r
        return cls.ROLE_CUSTOM

    @property
    def is_custom_role(self) -> bool:
        return self.role == self.ROLE_CUSTOM

    def apply_role_defaults(self):
        flags = self.get_role_default_flags(self.role, getattr(self.user, "is_superuser", False))
        for k, v in flags.items():
            setattr(self, k, v)

    @property
    def can_edit_hardware(self) -> bool:
        return self.can_edit_probes

    @property
    def has_crm_view_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.role_obj:
            return self.role_obj.can_view_crm
        if self.role == self.ROLE_HW_VIEWER and not any([
            self.can_edit_customers,
            self.can_edit_licenses,
            self.can_edit_products,
            self.can_edit_crm,
            self.can_manage_system,
        ]):
            return False
        return self.can_view_crm

    @property
    def has_customer_edit_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.role_obj:
            return self.role_obj.can_edit_customers
        if self.role in [self.ROLE_ALL, self.ROLE_CRM, self.ROLE_CUSTOMER_ADMIN]:
            return True
        return self.can_edit_customers or self.can_edit_crm

    @property
    def has_license_edit_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.role_obj:
            return self.role_obj.can_edit_licenses
        if self.role in [self.ROLE_ALL, self.ROLE_CRM, self.ROLE_LICENSE_ADMIN]:
            return True
        return self.can_edit_licenses or self.can_edit_crm

    @property
    def has_product_edit_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.role_obj:
            return self.role_obj.can_edit_products or self.role_obj.can_manage_system
        if self.role in [self.ROLE_ALL, self.ROLE_CRM, self.ROLE_SYS_ADMIN]:
            return True
        return self.can_edit_products or self.can_manage_system or self.can_edit_crm

    @property
    def has_crm_edit_access(self) -> bool:
        return self.has_customer_edit_access or self.has_license_edit_access or self.has_product_edit_access

    @property
    def has_probe_edit_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.role_obj:
            return self.role_obj.can_edit_probes
        if self.role in [self.ROLE_ALL, self.ROLE_PROBE]:
            return True
        return self.can_edit_probes

    @property
    def has_hardware_edit_access(self) -> bool:
        return self.has_probe_edit_access

    @property
    def has_hardware_model_edit_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.role_obj:
            return self.role_obj.can_edit_probes or self.role_obj.can_manage_system
        if self.role in [self.ROLE_ALL, self.ROLE_PROBE, self.ROLE_SYS_ADMIN]:
            return True
        return self.can_edit_probes or self.can_manage_system

    @property
    def has_system_manage_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.role_obj:
            return self.role_obj.can_manage_system
        if self.role in [self.ROLE_ALL, self.ROLE_SYS_ADMIN]:
            return True
        return self.can_manage_system

    @property
    def has_license_approve_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.can_approve_licenses:
            return True
        if self.role_obj:
            return self.role_obj.can_approve_licenses
        return self.role == self.ROLE_ALL

    @property
    def has_contract_approve_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.can_approve_contracts:
            return True
        if self.role_obj:
            return self.role_obj.can_approve_contracts
        return self.role == self.ROLE_ALL

    @property
    def has_hardware_approve_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.can_approve_hardware:
            return True
        if self.role_obj:
            return self.role_obj.can_approve_hardware
        return self.role == self.ROLE_ALL

    @property
    def has_order_edit_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.can_edit_orders:
            return True
        if self.role_obj:
            return self.role_obj.can_edit_orders
        return self.role in [self.ROLE_ALL, self.ROLE_CRM, self.ROLE_SALES_REP, self.ROLE_SALES_MANAGER]

    @property
    def has_order_approve_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.can_approve_orders:
            return True
        if self.role_obj:
            return self.role_obj.can_approve_orders
        return self.role in [self.ROLE_ALL, self.ROLE_SALES_MANAGER]

    @property
    def has_order_ship_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.can_ship_orders or self.has_probe_edit_access:
            return True
        if self.role_obj:
            return self.role_obj.can_ship_orders or self.role_obj.can_edit_probes
        return self.role in [self.ROLE_ALL, self.ROLE_PROBE]

    @property
    def has_order_close_access(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return True
        if self.can_close_orders:
            return True
        if self.role_obj:
            return self.role_obj.can_close_orders
        return self.role in [self.ROLE_ALL, self.ROLE_FINANCE]

    @property
    def is_followed_only_scope(self) -> bool:
        if self.user.is_superuser or self.user.is_staff:
            return False
        if self.role_obj:
            if self.role_obj.can_manage_system:
                return False
            return self.role_obj.customer_scope == self.SCOPE_FOLLOWED
        if self.role in [self.ROLE_ALL, self.ROLE_SYS_ADMIN]:
            return False
        return self.customer_scope == self.SCOPE_FOLLOWED

    @property
    def active_permission_labels(self) -> list:
        """回傳目前啟用的模組權限中文標籤清單，方便前端列表展示"""
        labels = []
        if self.role_obj:
            labels = list(self.role_obj.active_permission_labels)
            if self.can_approve_licenses and "授權覆核" not in labels:
                labels.append("授權覆核")
            if self.can_approve_contracts and "保固覆核" not in labels:
                labels.append("保固覆核")
            if self.can_approve_hardware and "硬體出庫覆核" not in labels:
                labels.append("硬體出庫覆核")
            if self.can_edit_orders and "訂單報價" not in labels:
                labels.append("訂單報價")
            if self.can_approve_orders and "訂單審核" not in labels:
                labels.append("訂單審核")
            if self.can_ship_orders and "訂單出貨" not in labels:
                labels.append("訂單出貨")
            if self.can_close_orders and "收款結案" not in labels:
                labels.append("收款結案")
            return labels
        if self.can_edit_customers:
            labels.append("客戶資料")
        if self.can_edit_licenses:
            labels.append("授權與保固")
        if self.can_edit_products:
            labels.append("軟體主檔")
        if self.can_edit_probes:
            labels.append("硬體庫存")
        if self.can_manage_system:
            labels.append("系統設定")
        if self.can_approve_licenses:
            labels.append("授權覆核")
        if self.can_approve_contracts:
            labels.append("保固覆核")
        if self.can_approve_hardware:
            labels.append("硬體出庫覆核")
        if self.can_edit_orders:
            labels.append("訂單報價")
        if self.can_approve_orders:
            labels.append("訂單審核")
        if self.can_ship_orders:
            labels.append("訂單出貨")
        if self.can_close_orders:
            labels.append("收款結案")
        if not labels and self.can_view_crm:
            labels.append("一般操作查詢")
        elif not labels and not self.can_view_crm:
            labels.append("硬體專用查詢")
        return labels

    def get_role_display_name(self) -> str:
        if self.role_obj:
            return self.role_obj.name
        return self.get_role_display()


class OuPermission(models.Model):
    ROLE_ALL = UserProfile.ROLE_ALL
    ROLE_SYS_ADMIN = UserProfile.ROLE_SYS_ADMIN
    ROLE_CUSTOMER_ADMIN = UserProfile.ROLE_CUSTOMER_ADMIN
    ROLE_LICENSE_ADMIN = UserProfile.ROLE_LICENSE_ADMIN
    ROLE_PROBE = UserProfile.ROLE_PROBE
    ROLE_VIEW = UserProfile.ROLE_VIEW
    ROLE_HW_VIEWER = UserProfile.ROLE_HW_VIEWER
    ROLE_CRM = UserProfile.ROLE_CRM

    ROLE_CHOICES = UserProfile.ROLE_CHOICES

    ou_path = models.CharField(max_length=500, unique=True, verbose_name="AD 群組或 OU 路徑")
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default=ROLE_VIEW, verbose_name="指派角色")
    note = models.CharField(max_length=200, blank=True, verbose_name="備註說明")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["ou_path"]
        verbose_name = "AD/OU 權限映射"
        verbose_name_plural = "AD/OU 權限映射"

    def __str__(self) -> str:
        return f"{self.ou_path} -> {self.get_role_display()}"


class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="操作人員")
    action = models.CharField(max_length=100, verbose_name="操作動作")
    target_type = models.CharField(max_length=100, blank=True, verbose_name="目標模組/型別")
    target_identifier = models.CharField(max_length=150, blank=True, verbose_name="目標代號/名稱")
    detail = models.TextField(blank=True, verbose_name="詳細資訊")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP 位址")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="發生時間")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "審計日誌"
        verbose_name_plural = "審計日誌"

    def __str__(self) -> str:
        username = self.user.username if self.user else "system"
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {username}: {self.action}"


class SystemSetting(models.Model):
    ad_ip = models.CharField(
        max_length=100,
        blank=True,
        help_text="AD 伺服器 IP 或 URI (例如: ldap://192.168.10.201)",
        verbose_name="AD 伺服器連線網址",
    )
    ldap_bind_dn = models.CharField(
        max_length=500,
        blank=True,
        help_text="AD Bind DN (例如: CN=admin,CN=Users,DC=example,DC=com)",
        verbose_name="LDAP Bind DN",
    )
    ldap_bind_password = models.CharField(
        max_length=200,
        blank=True,
        help_text="AD 密碼",
        verbose_name="LDAP 密碼",
    )
    ldap_base_dn = models.CharField(
        max_length=500,
        blank=True,
        help_text="AD Base DN (例如: DC=example,DC=com)",
        verbose_name="LDAP Base DN",
    )
    admin_ou = models.CharField(
        max_length=500, blank=True, help_text="管理員 OU 或群組路徑 (例如: IT)", verbose_name="預設管理員 OU/群組"
    )
    smtp_host = models.CharField(
        max_length=255, blank=True, verbose_name="SMTP 伺服器主機", help_text="例如: smtp.office365.com 或 smtp.gmail.com"
    )
    smtp_port = models.PositiveIntegerField(
        default=587, verbose_name="SMTP 連接埠 (Port)", help_text="常見為 587 (TLS)、465 (SSL) 或 25"
    )
    smtp_use_tls = models.BooleanField(default=True, verbose_name="啟用 STARTTLS 加密")
    smtp_use_ssl = models.BooleanField(default=False, verbose_name="啟用 SSL/TLS 加密")
    smtp_user = models.CharField(
        max_length=255, blank=True, verbose_name="SMTP 驗證帳號", help_text="寄件伺服器登入帳號"
    )
    smtp_password = models.CharField(
        max_length=255, blank=True, verbose_name="SMTP 驗證密碼", help_text="寄件伺服器登入密碼或應用程式專用密碼"
    )
    smtp_from_email = models.EmailField(
        blank=True, verbose_name="預設寄件者信箱 (From Email)", help_text="例如: crm-notify@example.com (留空則預設使用 SMTP 驗證帳號)"
    )
    site_base_url = models.CharField(
        max_length=255, blank=True, default="http://127.0.0.1:8000", verbose_name="系統外部網址 (Base URL)", help_text="用於產生通知信內的客戶點擊連結，例如 http://crm.company.com:8000"
    )
    customer_custom_field_schema = models.JSONField(
        default=list,
        blank=True,
        verbose_name="客戶全域自訂欄位結構清單",
        help_text="JSON 格式設定，例: [{'key': 'industry', 'label': '產業別', 'type': 'select', 'options': ['金融', '製造'], 'default': '', 'required': False, 'show_in_list': True}]",
    )
    audit_log_retention_days = models.PositiveIntegerField(
        default=0,
        verbose_name="審計日誌保留天數",
        help_text="0 代表永久保留，超過設定天數的日誌將自動刪除",
    )
    idle_timeout_minutes = models.PositiveIntegerField(
        default=10,
        verbose_name="閒置自動登出時間 (分鐘)",
        help_text="使用者無操作或關閉瀏覽器超過此時間後自動登出 (設定範圍 1 ~ 1440 分鐘)",
    )
    license_cert_bg = models.FileField(
        upload_to="cert_templates/",
        null=True,
        blank=True,
        verbose_name="軟體授權證書自訂底圖",
        help_text="可上傳空白無文字之 PNG/JPG/WEBP/SVG 橫式 A4 底圖，留空則使用內建預設底圖",
    )
    warranty_cert_bg = models.FileField(
        upload_to="cert_templates/",
        null=True,
        blank=True,
        verbose_name="軟體保固證書自訂底圖",
        help_text="可上傳空白無文字之 PNG/JPG/WEBP/SVG 橫式 A4 底圖，留空則使用內建預設底圖",
    )
    require_license_approval = models.BooleanField(
        default=False,
        verbose_name="啟用「軟體授權書」簽核覆核流程",
        help_text="開啟後，無覆核權限之人員開立或修改軟體授權書核心條件時，需經具備授權覆核權限之人員核准後才正式成立",
    )
    require_warranty_approval = models.BooleanField(
        default=False,
        verbose_name="啟用「維護保固合約」簽核覆核流程",
        help_text="開啟後，無覆核權限之人員開立或修改維護保固合約核心條件時，需經具備保固覆核權限之人員核准後才正式成立",
    )
    require_hardware_approval = models.BooleanField(
        default=False,
        verbose_name="啟用「硬體設備出庫」簽核覆核流程",
        help_text="開啟後，無覆核權限之人員執行正式出貨、客戶借測或合約換機出庫時，設備將預先鎖定為待審狀態，待核准後才正式出庫",
    )
    seller_company_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="報價單賣方公司全銜",
    )
    seller_tax_id = models.CharField(
        max_length=30,
        blank=True,
        default="",
        verbose_name="賣方統一編號",
    )
    seller_phone = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="賣方聯絡電話",
    )
    seller_address = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="賣方公司地址",
    )
    seller_bank_info = models.TextField(
        blank=True,
        default="",
        verbose_name="匯款銀行帳戶資訊",
    )
    quotation_default_terms = models.TextField(
        blank=True,
        default="1. 本報價單有效期限為開立日起 30 日內有效。\n2. 客戶確認購買請於下方簽章回傳，以利安排出貨與授權開立作業。",
        verbose_name="報價單預設條款與注意事項",
    )
    seller_logo = models.FileField(
        upload_to="quotation_templates/",
        null=True,
        blank=True,
        verbose_name="報價單公司 Logo 圖檔",
    )
    seller_stamp = models.FileField(
        upload_to="quotation_templates/",
        null=True,
        blank=True,
        verbose_name="報價單公司報價章/大小章圖檔",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "系統全域設定"
        verbose_name_plural = "系統全域設定"

    def __str__(self) -> str:
        return "系統全域設定"

    @classmethod
    def get_settings(cls):
        settings, _ = cls.objects.get_or_create(id=1)
        return settings
