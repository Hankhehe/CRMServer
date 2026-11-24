from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class Product(models.Model):
    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="產品代碼",
        help_text="全系統唯一，例如: FLOW-INSPECTOR",
    )
    name = models.CharField(max_length=100, verbose_name="產品名稱")
    category = models.CharField(
        max_length=50,
        blank=True,
        default="軟體產品",
        verbose_name="產品分類",
        help_text="例如: 核心軟體、擴充套件、分析系統",
    )
    default_unit = models.CharField(
        max_length=20,
        default="台",
        verbose_name="預設計量單位",
        help_text="例如: 台、節點、核心、授權數",
    )
    custom_field_schema = models.JSONField(
        default=list,
        blank=True,
        verbose_name="自訂欄位規格清單",
        help_text="JSON 格式設定，例: [{'key': 'ha_mode', 'label': 'HA 架構', 'type': 'text'}]",
    )
    is_active = models.BooleanField(default=True, verbose_name="是否啟用")
    note = models.TextField(blank=True, verbose_name="產品說明與規格備註")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新時間")

    class Meta:
        ordering = ["code"]
        verbose_name = "軟體產品主檔"
        verbose_name_plural = "軟體產品主檔"

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    @property
    def active_license_count(self) -> int:
        today = timezone.localdate()
        return (
            self.licenses.filter(
                is_disabled=False,
                approval_status=CustomerLicense.APPROVAL_APPROVED,
            )
            .filter(models.Q(start_date__isnull=True) | models.Q(start_date__lte=today))
            .filter(
                models.Q(is_perpetual=True)
                | models.Q(license_type=CustomerLicense.TYPE_PERPETUAL)
                | models.Q(end_date__isnull=True)
                | models.Q(end_date__gte=today)
            )
            .count()
        )


class CustomerLicense(models.Model):
    TYPE_PERPETUAL = "perpetual"
    TYPE_SUBSCRIPTION = "subscription"
    TYPE_TRIAL = "trial"
    TYPE_CHOICES = [
        (TYPE_PERPETUAL, "永久買斷"),
        (TYPE_SUBSCRIPTION, "定期訂閱"),
        (TYPE_TRIAL, "測試授權"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_SUSPENDED = "suspended"
    STATUS_PENDING = "pending"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "生效中"),
        (STATUS_EXPIRED, "已到期"),
        (STATUS_SUSPENDED, "已停用"),
        (STATUS_PENDING, "待生效"),
    ]

    APPROVAL_APPROVED = "approved"
    APPROVAL_PENDING = "pending"
    APPROVAL_REJECTED = "rejected"
    APPROVAL_CHOICES = [
        (APPROVAL_APPROVED, "已核准"),
        (APPROVAL_PENDING, "待覆核"),
        (APPROVAL_REJECTED, "已退回"),
    ]

    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.CASCADE,
        related_name="licenses",
        verbose_name="採購客戶",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="licenses",
        verbose_name="授權產品",
    )
    license_no = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="授權書編號/合約號",
        help_text="全系統唯一，例如: LIC-2026-0001",
    )
    license_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_PERPETUAL,
        verbose_name="授權類型",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name="授權使用數量",
        help_text="例如: 採購 200 U (節點/授權數)",
    )
    unit = models.CharField(
        max_length=20,
        default="台",
        verbose_name="計量單位",
    )
    start_date = models.DateField(null=True, blank=True, verbose_name="授權起始日")
    end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="授權到期日",
        help_text="若為永久授權可留空",
    )
    is_perpetual = models.BooleanField(
        default=False,
        verbose_name="永久買斷授權 (不受到期日控制)",
        help_text="勾選後將不受到期日控制",
    )
    is_disabled = models.BooleanField(
        default=False,
        verbose_name="停用此授權書",
        help_text="勾選後此授權書將強制變更為已停用狀態",
    )
    license_key = models.TextField(
        blank=True,
        verbose_name="授權金鑰 / 序號字串",
        help_text="軟體啟動金鑰或憑證內容",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        verbose_name="授權狀態",
    )
    custom_values = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="動態規格數值",
        help_text="依產品動態欄位規格填寫之參數 (JSON)",
    )
    note = models.TextField(blank=True, verbose_name="授權書備註說明")
    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_CHOICES,
        default=APPROVAL_APPROVED,
        verbose_name="簽核覆核狀態",
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_licenses",
        verbose_name="開單人員",
    )
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_licenses",
        verbose_name="覆核人員",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="覆核時間")
    rejection_reason = models.TextField(blank=True, default="", verbose_name="退回原因說明")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新時間")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "客戶軟體授權書"
        verbose_name_plural = "客戶軟體授權書"

    def __str__(self) -> str:
        return f"{self.license_no} - {self.product.name} ({self.customer.name})"

    def calculate_status(self) -> str:
        if self.is_disabled:
            return self.STATUS_SUSPENDED
        today = timezone.localdate()
        if self.start_date and self.start_date > today:
            return self.STATUS_PENDING
        if not self.is_perpetual and self.license_type != self.TYPE_PERPETUAL:
            if self.end_date and self.end_date < today:
                return self.STATUS_EXPIRED
        return self.STATUS_ACTIVE

    @property
    def dynamic_status(self) -> str:
        return self.calculate_status()

    @property
    def days_remaining(self) -> int:
        if self.is_perpetual or self.license_type == self.TYPE_PERPETUAL:
            return 99999
        if not self.end_date:
            return 99999
        today = timezone.localdate()
        return (self.end_date - today).days

    @property
    def is_expired(self) -> bool:
        if self.is_perpetual or self.license_type == self.TYPE_PERPETUAL:
            return False
        if not self.end_date:
            return False
        return self.days_remaining < 0

    @property
    def is_expiring_soon(self) -> bool:
        if self.is_perpetual or self.license_type == self.TYPE_PERPETUAL:
            return False
        if not self.end_date:
            return False
        return 0 <= self.days_remaining <= 30

    @property
    def is_approved(self) -> bool:
        return self.approval_status == self.APPROVAL_APPROVED

    @property
    def is_pending_approval(self) -> bool:
        return self.approval_status == self.APPROVAL_PENDING

    @property
    def is_rejected(self) -> bool:
        return self.approval_status == self.APPROVAL_REJECTED

    @property
    def status_display(self) -> str:
        if self.approval_status == self.APPROVAL_PENDING:
            return "待覆核 (尚未生效)"
        if self.approval_status == self.APPROVAL_REJECTED:
            return "簽核退回 (待修正)"
        st = self.dynamic_status
        if st == self.STATUS_SUSPENDED:
            return "已停用"
        if st == self.STATUS_PENDING:
            today = timezone.localdate()
            diff = (self.start_date - today).days if self.start_date else 0
            return f"待生效 ({diff} 天後開始)"
        if self.is_perpetual or self.license_type == self.TYPE_PERPETUAL:
            return "永久有效"
        if not self.end_date:
            return "有效 (無到期日)"
        diff = self.days_remaining
        if diff < 0:
            return f"已過期 (逾期 {abs(diff)} 天)"
        elif diff <= 30:
            return f"即將到期 (剩餘 {diff} 天)"
        elif diff <= 60:
            return f"注意續約 (剩餘 {diff} 天)"
        return f"有效 (剩餘 {diff} 天)"

    @property
    def badge_class(self) -> str:
        if self.approval_status == self.APPROVAL_PENDING:
            return "badge-loaned"
        if self.approval_status == self.APPROVAL_REJECTED:
            return "badge-scrapped"
        st = self.dynamic_status
        if st == self.STATUS_SUSPENDED:
            return "badge-scrapped"
        if st == self.STATUS_PENDING:
            return "badge-secondary"
        if st == self.STATUS_EXPIRED:
            return "badge-scrapped"
        if self.is_perpetual or self.license_type == self.TYPE_PERPETUAL:
            return "badge-available"
        if not self.end_date:
            return "badge-available"
        diff = self.days_remaining
        if diff < 0:
            return "badge-scrapped"
        elif diff <= 30:
            return "badge-loaned"
        elif diff <= 60:
            return "badge-repair"
        return "badge-available"

    @property
    def active_maintenance_contracts(self):
        today = timezone.localdate()
        return [
            c for c in self.maintenance_contracts.all()
            if c.approval_status == "approved" and c.status == "active" and (not c.end_date or c.end_date >= today)
        ]

    @property
    def custom_specs_display(self) -> list:
        """取得結合產品 Schema 與授權書 custom_values 之完整自訂規格清單"""
        schema = (self.product.custom_field_schema or []) if self.product else []
        vals = self.custom_values or {}
        result = []
        schema_keys = set()
        for item in schema:
            key = item.get("key")
            if not key:
                continue
            schema_keys.add(key)
            label = item.get("label", key)
            f_type = item.get("type", "text")
            show_in_list = bool(item.get("show_in_list", False))
            raw_val = vals.get(key)
            if raw_val in (None, "", []):
                continue
            if isinstance(raw_val, list):
                display_val = ", ".join(str(x) for x in raw_val if str(x).strip())
            elif f_type == "boolean":
                display_val = "是" if raw_val in (True, "true", "True", 1, "1", "是") else "否"
            elif f_type == "datetime":
                s = str(raw_val).strip().replace("T", " ")
                display_val = s[:16] if len(s) >= 16 and s[4:5] == "-" and s[10:11] == " " else s
            else:
                display_val = str(raw_val)
            if not display_val:
                continue
            result.append({
                "key": key,
                "label": label,
                "value": display_val,
                "raw_value": raw_val,
                "show_in_list": show_in_list,
            })
        for k, v in vals.items():
            if k not in schema_keys and v not in (None, "", []):
                display_val = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
                result.append({
                    "key": k,
                    "label": k,
                    "value": display_val,
                    "raw_value": v,
                    "show_in_list": False,
                })
        return result

    @property
    def list_custom_specs(self) -> list:
        """取得設定為在列表中顯示 (show_in_list=True) 之自訂規格清單"""
        return [item for item in self.custom_specs_display if item.get("show_in_list")]

    def save(self, *args, **kwargs):
        if self.is_perpetual:
            self.license_type = self.TYPE_PERPETUAL
            self.end_date = None
        elif self.license_type == self.TYPE_PERPETUAL:
            self.is_perpetual = True
            self.end_date = None
        elif not self.license_type:
            self.license_type = self.TYPE_SUBSCRIPTION
        self.status = self.calculate_status()
        super().save(*args, **kwargs)

