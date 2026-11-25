import datetime
from django.db import models
from django.utils import timezone


class Customer(models.Model):
    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="客戶代號/統編",
        help_text="可填寫公司統一編號或系統自訂代號，全系統唯一",
    )
    name = models.CharField(max_length=100, verbose_name="客戶名稱")
    contact = models.CharField(max_length=100, blank=True, verbose_name="主要聯絡人")
    email = models.EmailField(blank=True, verbose_name="電子郵件")
    phone = models.CharField(max_length=50, blank=True, verbose_name="電話")
    address = models.CharField(max_length=200, blank=True, verbose_name="地址")
    tax_id = models.CharField(max_length=20, blank=True, verbose_name="統一編號")
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
        verbose_name="上層客戶",
        help_text="若此客戶由經銷商或代理商管轄，可指向其上層客戶",
    )
    note = models.TextField(max_length=500, null=True, blank=True, verbose_name="備註")
    custom_values = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="自訂欄位數值",
        help_text="依系統全域客戶自訂欄位結構填寫之參數 (JSON)",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新時間")

    class Meta:
        ordering = ["code"]
        verbose_name = "客戶資料"
        verbose_name_plural = "客戶資料"

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                raise ValidationError({"parent": "上層客戶不可指向自己。"})
            if self.pk:
                # 往上追溯檢查是否會產生循環指向
                visited = {self.pk}
                curr = self.parent
                while curr is not None:
                    if curr.pk in visited:
                        raise ValidationError({"parent": f"不可將「{self.parent.name}」設為上層客戶，這會造成上下游循環指向。"})
                    visited.add(curr.pk)
                    curr = curr.parent

    def get_ancestors(self) -> list["Customer"]:
        """回傳由最頂層上層客戶至直屬上層客戶之有序清單 (Root -> ... -> Direct Parent)"""
        ancestors = []
        visited = {self.pk} if self.pk else set()
        curr = self.parent
        while curr is not None and curr.pk not in visited:
            visited.add(curr.pk)
            ancestors.append(curr)
            curr = curr.parent
        ancestors.reverse()
        return ancestors

    def get_descendant_ids(self) -> set[int]:
        """回傳此客戶底下所有直屬與間接下層客戶的 PK 集合"""
        if not self.pk:
            return set()
        all_pairs = Customer.objects.exclude(parent_id__isnull=True).values_list("id", "parent_id")
        children_map: dict[int, list[int]] = {}
        for cid, pid in all_pairs:
            children_map.setdefault(pid, []).append(cid)
        descendants: set[int] = set()
        stack = list(children_map.get(self.pk, []))
        while stack:
            curr_id = stack.pop()
            if curr_id not in descendants and curr_id != self.pk:
                descendants.add(curr_id)
                stack.extend(children_map.get(curr_id, []))
        return descendants

    @property
    def maintenance_expiry(self):
        """相容舊版屬性：取得最新已核准合約之到期日"""
        latest = self.contracts.filter(
            is_terminated=False,
            approval_status="approved",
        ).order_by("-end_date").first()
        if latest and latest.end_date:
            return latest.end_date
        return None

    @property
    def contract_status_display(self):
        expiry = self.maintenance_expiry
        if not expiry:
            return "無維護合約"
        today = timezone.localdate()
        diff = (expiry - today).days
        if diff < 0:
            return f"已過期 (逾期 {abs(diff)} 天)"
        elif diff <= 30:
            return f"即將到期 (剩餘 {diff} 天)"
        elif diff <= 60:
            return f"注意續約 (剩餘 {diff} 天)"
        return f"有效 (剩餘 {diff} 天)"

    @property
    def hardware_devices(self):
        return self.probes

    @property
    def contract_badge_class(self):
        expiry = self.maintenance_expiry
        if not expiry:
            return "badge-secondary"
        today = timezone.localdate()
        diff = (expiry - today).days
        if diff < 0:
            return "badge-scrapped"  # 紅色
        elif diff <= 30:
            return "badge-loaned"    # 橘色警告
        elif diff <= 60:
            return "badge-repair"    # 紫色提醒
        return "badge-available"     # 綠色正常

    @staticmethod
    def _format_custom_datetime_display(val_str: str) -> str:
        s = str(val_str or "").strip()
        if not s:
            return ""
        # 轉為台灣時區習慣顯示格式 YYYY-MM-DD HH:MM
        if "T" in s:
            s = s.replace("T", " ")
        # 若為 YYYY-MM-DD HH:MM:SS 截斷至分或保留
        if len(s) >= 16 and s[4:5] == "-" and s[7:8] == "-" and s[10:11] == " ":
            return s[:16]
        return s

    def get_custom_specs_display(self, schema=None) -> list:
        """取得結合全域客戶 Schema 與此客戶 custom_values 之完整自訂欄位清單"""
        if schema is None:
            from apps.core.models import SystemSetting
            schema = SystemSetting.get_settings().customer_custom_field_schema or []
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
                display_val = ""
            elif isinstance(raw_val, list):
                display_val = ", ".join(str(x) for x in raw_val if str(x).strip())
            elif f_type == "boolean":
                display_val = "是" if raw_val in (True, "true", "True", 1, "1", "是") else "否"
            elif f_type == "datetime":
                display_val = self._format_custom_datetime_display(raw_val)
            else:
                display_val = str(raw_val)

            result.append({
                "key": key,
                "label": label,
                "type": f_type,
                "value": display_val,
                "raw_value": raw_val,
                "has_value": bool(display_val != ""),
                "show_in_list": show_in_list,
            })
        for k, v in vals.items():
            if k not in schema_keys and v not in (None, "", []):
                display_val = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
                result.append({
                    "key": k,
                    "label": k,
                    "type": "text",
                    "value": display_val,
                    "raw_value": v,
                    "has_value": True,
                    "show_in_list": False,
                })
        return result

    @property
    def custom_specs_display(self) -> list:
        """回傳所有已定義或有值的自訂欄位清單（供客戶詳情頁顯示）"""
        schema = getattr(self, "_cached_customer_schema", None)
        return self.get_custom_specs_display(schema=schema)

    @property
    def list_custom_specs(self) -> list:
        """取得設定為在列表中顯示 (show_in_list=True) 且有值之自訂欄位清單"""
        return [
            item
            for item in self.custom_specs_display
            if item.get("show_in_list") and item.get("has_value")
        ]


class ContactPerson(models.Model):
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="contacts", verbose_name="所屬客戶"
    )
    name = models.CharField(max_length=100, verbose_name="姓名")
    title = models.CharField(max_length=100, blank=True, verbose_name="職稱/部門")
    phone = models.CharField(max_length=50, blank=True, verbose_name="聯絡電話/手機")
    email = models.EmailField(blank=True, verbose_name="電子信箱")
    is_primary = models.BooleanField(default=False, verbose_name="主要聯絡人")
    note = models.CharField(max_length=200, blank=True, verbose_name="備註")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")

    class Meta:
        ordering = ["-is_primary", "name"]
        verbose_name = "客戶聯絡人"
        verbose_name_plural = "客戶聯絡人"

    def __str__(self) -> str:
        primary_mark = " (主要)" if self.is_primary else ""
        return f"{self.name}{primary_mark} - {self.customer.name}"

    def save(self, *args, **kwargs):
        if self.is_primary:
            # 將同客戶之其他聯絡人取消主要
            ContactPerson.objects.filter(customer=self.customer, is_primary=True).exclude(pk=self.pk).update(is_primary=False)
            # 同步更新 customer.contact, email, phone
            Customer.objects.filter(pk=self.customer.pk).update(
                contact=self.name,
                email=self.email or self.customer.email,
                phone=self.phone or self.customer.phone,
            )
        super().save(*args, **kwargs)


class CustomerInfoSection(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="info_sections",
        verbose_name="所屬客戶",
    )
    title = models.CharField(
        max_length=100,
        verbose_name="區塊名稱",
        help_text="例如: 系統環境架構、網路拓樸與 IP 規劃、部署主機清單、機房進場須知",
    )
    content = models.TextField(verbose_name="區塊內容")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="排序順序")
    updated_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="最後更新人員",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新時間")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "客戶自訂環境與資訊區塊"
        verbose_name_plural = "客戶自訂環境與資訊區塊"

    def __str__(self) -> str:
        return f"[{self.customer.name}] {self.title}"


class MaintenanceContract(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_PENDING = "pending"
    STATUS_TERMINATED = "terminated"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "生效中"),
        (STATUS_EXPIRED, "已到期"),
        (STATUS_PENDING, "待生效"),
        (STATUS_TERMINATED, "已終止"),
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
        Customer, on_delete=models.CASCADE, related_name="contracts", verbose_name="簽約客戶"
    )
    contract_no = models.CharField(max_length=50, unique=True, verbose_name="合約編號")
    name = models.CharField(max_length=150, blank=True, verbose_name="合約名稱")
    start_date = models.DateField(null=True, blank=True, verbose_name="合約起日")
    end_date = models.DateField(null=True, blank=True, verbose_name="合約訖日(到期日)")
    is_terminated = models.BooleanField(
        default=False,
        verbose_name="終止此合約",
        help_text="勾選後此合約將強制變更為已終止狀態",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, verbose_name="合約狀態"
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True, verbose_name="合約金額"
    )
    sla_level = models.CharField(max_length=50, blank=True, verbose_name="服務等級 (SLA)")
    licenses = models.ManyToManyField(
        "licenses.CustomerLicense",
        blank=True,
        related_name="maintenance_contracts",
        verbose_name="涵蓋軟體授權",
    )
    probes = models.ManyToManyField(
        "hardware.HardwareDevice",
        blank=True,
        related_name="maintenance_contracts",
        verbose_name="涵蓋設備",
    )
    note = models.TextField(blank=True, verbose_name="合約備註")
    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_CHOICES,
        default=APPROVAL_APPROVED,
        verbose_name="簽核覆核狀態",
    )
    created_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_contracts",
        verbose_name="開單人員",
    )
    reviewed_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_contracts",
        verbose_name="覆核人員",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="覆核時間")
    rejection_reason = models.TextField(blank=True, default="", verbose_name="退回原因說明")
    source_order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_contracts",
        verbose_name="來源訂單",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="最後更新")

    class Meta:
        ordering = ["-end_date"]
        verbose_name = "維護合約"
        verbose_name_plural = "維護合約"

    def __str__(self) -> str:
        return f"{self.contract_no} ({self.customer.name})"

    def calculate_status(self) -> str:
        if self.is_terminated or self.status == self.STATUS_TERMINATED:
            self.is_terminated = True
            return self.STATUS_TERMINATED
        today = timezone.localdate()
        if self.start_date and self.start_date > today:
            return self.STATUS_PENDING
        if self.end_date and self.end_date < today:
            return self.STATUS_EXPIRED
        return self.STATUS_ACTIVE

    @property
    def dynamic_status(self) -> str:
        return self.calculate_status()

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
    def badge_class(self) -> str:
        if self.approval_status == self.APPROVAL_PENDING:
            return "badge-loaned"
        if self.approval_status == self.APPROVAL_REJECTED:
            return "badge-scrapped"
        st = self.dynamic_status
        if st == self.STATUS_TERMINATED:
            return "badge-secondary"
        if st == self.STATUS_PENDING:
            return "badge-secondary"
        if st == self.STATUS_EXPIRED:
            return "badge-scrapped"
        return "badge-available"

    @property
    def status_display(self) -> str:
        if self.approval_status == self.APPROVAL_PENDING:
            return "待覆核 (尚未生效)"
        if self.approval_status == self.APPROVAL_REJECTED:
            return "簽核退回 (待修正)"
        st = self.dynamic_status
        if st == self.STATUS_TERMINATED:
            return "已終止"
        if st == self.STATUS_PENDING:
            today = timezone.localdate()
            diff = (self.start_date - today).days if self.start_date else 0
            return f"待生效 ({diff} 天後開始)"
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
    def days_remaining(self) -> int:
        if not self.end_date:
            return 9999
        today = timezone.localdate()
        return (self.end_date - today).days

    @property
    def is_expired(self) -> bool:
        if not self.end_date:
            return False
        return self.days_remaining < 0

    @property
    def is_expiring_soon(self) -> bool:
        if not self.end_date:
            return False
        days = self.days_remaining
        return 0 <= days <= 30

    def save(self, *args, **kwargs):
        self.status = self.calculate_status()
        super().save(*args, **kwargs)


class CustomerActivityLog(models.Model):
    TYPE_MEETING = "meeting"
    TYPE_CALL = "call"
    TYPE_REQUIREMENT = "requirement"
    TYPE_SUPPORT = "support"
    TYPE_QUOTATION = "quotation"
    TYPE_OTHER = "other"

    ACTIVITY_TYPE_CHOICES = [
        (TYPE_MEETING, "會議記錄"),
        (TYPE_CALL, "電話拜訪"),
        (TYPE_REQUIREMENT, "需求溝通"),
        (TYPE_SUPPORT, "售後/技術支援"),
        (TYPE_QUOTATION, "報價討論"),
        (TYPE_OTHER, "一般備註"),
    ]

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="activity_logs", verbose_name="所屬客戶"
    )
    activity_type = models.CharField(
        max_length=20,
        choices=ACTIVITY_TYPE_CHOICES,
        default=TYPE_CALL,
        verbose_name="紀錄類型",
    )
    content = models.TextField(verbose_name="紀錄與討論內容")
    contact = models.ForeignKey(
        ContactPerson,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity_logs",
        verbose_name="關聯窗口",
    )
    follow_up_date = models.DateField(
        null=True, blank=True, verbose_name="下次跟進日期"
    )
    created_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="customer_activity_logs",
        verbose_name="建立同仁",
    )
    is_edited = models.BooleanField(default=False, verbose_name="是否已編輯")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="最後更新")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "客戶執行軌跡"
        verbose_name_plural = "客戶執行軌跡"

    def __str__(self) -> str:
        return f"{self.customer.name} - {self.get_activity_type_display()} ({self.created_at.strftime('%Y-%m-%d')})"

    @property
    def badge_class(self) -> str:
        classes = {
            self.TYPE_MEETING: "badge-primary",
            self.TYPE_CALL: "badge-loaned",
            self.TYPE_REQUIREMENT: "badge-repair",
            self.TYPE_SUPPORT: "badge-available",
            self.TYPE_QUOTATION: "badge-shipped",
            self.TYPE_OTHER: "badge-secondary",
        }
        return classes.get(self.activity_type, "badge-secondary")

    @property
    def icon_class(self) -> str:
        icons = {
            self.TYPE_MEETING: "fa-solid fa-users",
            self.TYPE_CALL: "fa-solid fa-phone",
            self.TYPE_REQUIREMENT: "fa-solid fa-clipboard-list",
            self.TYPE_SUPPORT: "fa-solid fa-headset",
            self.TYPE_QUOTATION: "fa-solid fa-file-invoice-dollar",
            self.TYPE_OTHER: "fa-solid fa-note-sticky",
        }
        return icons.get(self.activity_type, "fa-solid fa-comment-dots")


class ActivityAttachment(models.Model):
    activity_log = models.ForeignKey(
        CustomerActivityLog,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="所屬軌跡紀錄",
    )
    file = models.FileField(
        upload_to="customer_activities/%Y/%m/", verbose_name="附件檔案"
    )
    filename = models.CharField(max_length=255, verbose_name="原始檔名")
    file_size = models.PositiveIntegerField(default=0, verbose_name="檔案大小 (Bytes)")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="上傳時間")

    class Meta:
        ordering = ["uploaded_at"]
        verbose_name = "軌跡附件"
        verbose_name_plural = "軌跡附件"

    def __str__(self) -> str:
        return self.filename

    @property
    def formatted_size(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        else:
            return f"{self.file_size / (1024 * 1024):.1f} MB"

    @property
    def file_ext(self) -> str:
        import os
        return os.path.splitext(self.filename)[1].lower()

    @property
    def is_image(self) -> bool:
        return self.file_ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]


class ContractAttachment(models.Model):
    contract = models.ForeignKey(
        MaintenanceContract,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="所屬維護合約",
    )
    file = models.FileField(
        upload_to="contracts/%Y/%m/", verbose_name="附件檔案"
    )
    filename = models.CharField(max_length=255, verbose_name="原始檔名")
    file_size = models.PositiveIntegerField(default=0, verbose_name="檔案大小 (Bytes)")
    uploaded_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="上傳人員",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="上傳時間")

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "合約附件"
        verbose_name_plural = "合約附件"

    def __str__(self) -> str:
        return self.filename

    @property
    def formatted_size(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        else:
            return f"{self.file_size / (1024 * 1024):.1f} MB"

    @property
    def file_ext(self) -> str:
        import os
        return os.path.splitext(self.filename)[1].lower()

    @property
    def is_image(self) -> bool:
        return self.file_ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]

    @property
    def is_pdf(self) -> bool:
        return self.file_ext == ".pdf"


class ContractProbeReplacement(models.Model):
    ACTION_REPLACE = "replace"
    ACTION_RETURN = "return"
    ACTION_CHOICES = [
        (ACTION_REPLACE, "故障換機"),
        (ACTION_RETURN, "設備退回"),
    ]

    contract = models.ForeignKey(
        MaintenanceContract,
        on_delete=models.CASCADE,
        related_name="replacement_logs",
        verbose_name="所屬維護合約",
    )
    action_type = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
        default=ACTION_REPLACE,
        verbose_name="異動類型",
    )
    old_probe = models.ForeignKey(
        "hardware.HardwareDevice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contract_replaced_out",
        verbose_name="換下/退回設備",
    )
    old_probe_code = models.CharField(max_length=100, verbose_name="換下設備序號快照")
    new_probe = models.ForeignKey(
        "hardware.HardwareDevice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contract_replaced_in",
        verbose_name="換上新設備",
    )
    new_probe_code = models.CharField(
        max_length=100, blank=True, verbose_name="換上設備序號快照"
    )
    reason = models.TextField(verbose_name="故障原因與更換說明")
    transferred_license = models.ForeignKey(
        "licenses.CustomerLicense",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transferred_replacements",
        verbose_name="繼承移轉之授權",
    )
    performed_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="經手人員",
    )
    replaced_at = models.DateTimeField(default=timezone.now, verbose_name="更換時間")

    class Meta:
        ordering = ["-replaced_at"]
        verbose_name = "合約設備退換紀錄"
        verbose_name_plural = "合約設備退換紀錄"

    def __str__(self) -> str:
        if self.action_type == self.ACTION_REPLACE:
            return f"[{self.get_action_type_display()}] {self.contract.contract_no}: {self.old_probe_code} -> {self.new_probe_code}"
        return f"[{self.get_action_type_display()}] {self.contract.contract_no}: {self.old_probe_code}"


class CustomerFollow(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="followers",
        verbose_name="關注客戶",
    )
    user = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="followed_customers",
        verbose_name="關注人員",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="關注時間")

    class Meta:
        unique_together = ("customer", "user")
        ordering = ["-created_at"]
        verbose_name = "客戶關注紀錄"
        verbose_name_plural = "客戶關注紀錄"

    def __str__(self) -> str:
        return f"{self.user.username} -> {self.customer.name}"


def generate_service_no() -> str:
    """產生唯一專業服務單號，格式: SRV-YYYYMMDD-XXXX"""
    from zoneinfo import ZoneInfo

    today_str = timezone.now().astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")
    prefix = f"SRV-{today_str}-"
    existing = CustomerServiceRecord.objects.filter(service_no__startswith=prefix).values_list(
        "service_no", flat=True
    )
    max_seq = 0
    for code in existing:
        tail = code[len(prefix):]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:04d}"


class CustomerServiceRecord(models.Model):
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "待執行"),
        (STATUS_IN_PROGRESS, "進行中"),
        (STATUS_COMPLETED, "已完成"),
        (STATUS_CANCELLED, "已取消"),
    ]

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="service_records",
        verbose_name="所屬客戶",
    )
    service_no = models.CharField(
        max_length=50,
        unique=True,
        default=generate_service_no,
        verbose_name="服務單號",
    )
    name = models.CharField(max_length=150, verbose_name="服務項目名稱")
    description = models.CharField(
        max_length=300,
        blank=True,
        default="",
        verbose_name="規格 / 服務內容說明",
    )
    service_date = models.DateField(null=True, blank=True, verbose_name="服務日期")
    completed_at = models.DateField(null=True, blank=True, verbose_name="實際完工日期")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name="執行狀態",
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name="數量")
    unit = models.CharField(max_length=20, default="式", verbose_name="單位")
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=0,
        null=True,
        blank=True,
        verbose_name="服務金額",
    )
    assigned_to = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_service_records",
        verbose_name="負責 / 執行同仁",
    )
    note = models.TextField(blank=True, default="", verbose_name="備註說明")
    source_order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_services",
        verbose_name="來源訂單",
    )
    created_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_service_records",
        verbose_name="建立人員",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="最後更新")

    class Meta:
        ordering = ["-service_date", "-created_at", "-id"]
        verbose_name = "客戶專業服務紀錄"
        verbose_name_plural = "客戶專業服務紀錄"

    def __str__(self) -> str:
        return f"{self.service_no} - {self.name} ({self.customer.name})"

    @property
    def badge_class(self) -> str:
        mapping = {
            self.STATUS_PENDING: "badge-loaned",
            self.STATUS_IN_PROGRESS: "badge-repair",
            self.STATUS_COMPLETED: "badge-available",
            self.STATUS_CANCELLED: "badge-secondary",
        }
        return mapping.get(self.status, "badge-secondary")


class ServiceRecordAttachment(models.Model):
    service_record = models.ForeignKey(
        CustomerServiceRecord,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="所屬專業服務紀錄",
    )
    file = models.FileField(
        upload_to="service_records/%Y/%m/",
        verbose_name="附件檔案",
    )
    filename = models.CharField(max_length=255, verbose_name="原始檔名")
    file_size = models.PositiveIntegerField(default=0, verbose_name="檔案大小 (Bytes)")
    uploaded_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="上傳人員",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="上傳時間")

    class Meta:
        ordering = ["-uploaded_at", "-id"]
        verbose_name = "專業服務附件"
        verbose_name_plural = "專業服務附件"

    def __str__(self) -> str:
        return self.filename

    @property
    def formatted_size(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        else:
            return f"{self.file_size / (1024 * 1024):.1f} MB"

    @property
    def file_ext(self) -> str:
        import os

        return os.path.splitext(self.filename)[1].lower()

    @property
    def is_image(self) -> bool:
        return self.file_ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]

    @property
    def is_pdf(self) -> bool:
        return self.file_ext == ".pdf"


