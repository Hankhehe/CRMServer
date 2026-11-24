import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


def generate_supplier_code() -> str:
    return uuid.uuid4().hex[:8].upper()


def generate_model_code() -> str:
    return uuid.uuid4().hex[:6].upper()


class HardwareModel(models.Model):
    model_code = models.CharField(
        max_length=20, unique=True, default=generate_model_code, verbose_name="型號代碼"
    )
    name = models.CharField(max_length=50, unique=True, verbose_name="型號名稱")
    port_count = models.PositiveIntegerField(default=0, blank=True, verbose_name="網路連接埠數")
    cpu = models.CharField(max_length=50, blank=True, verbose_name="CPU 規格")
    memory = models.CharField(max_length=50, blank=True, verbose_name="記憶體")
    hdd = models.CharField(max_length=50, blank=True, verbose_name="硬碟空間")
    custom_field_schema = models.JSONField(
        default=list,
        blank=True,
        verbose_name="自訂規格欄位清單",
        help_text="JSON 格式設定，例: [{'key': 'power_supply', 'label': '電源規格', 'type': 'text', 'default': '雙電源 550W'}]",
    )
    supplier = models.ForeignKey(
        "Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="models",
        verbose_name="原廠/供應商",
    )
    note = models.CharField(max_length=200, null=True, blank=True, verbose_name="規格備註")

    class Meta:
        ordering = ["name"]
        verbose_name = "硬體規格主檔"
        verbose_name_plural = "硬體規格主檔"

    def __str__(self) -> str:
        return self.name

    @property
    def available_count(self):
        return self.devices.filter(status="available", is_deleted=False).count()

    @property
    def shipped_count(self):
        return self.devices.filter(status="shipped", is_deleted=False).count()

    @property
    def loaned_count(self):
        return self.devices.filter(status="loaned", is_deleted=False).count()

    @property
    def scrapped_count(self):
        return self.devices.filter(status="scrapped", is_deleted=False).count()

    @property
    def pending_outbound_count(self):
        return self.devices.filter(
            status__in=["pending_ship", "pending_loan"], is_deleted=False
        ).count()


class Supplier(models.Model):
    supplier_code = models.CharField(
        max_length=20, unique=True, default=generate_supplier_code, verbose_name="供應商代號"
    )
    name = models.CharField(max_length=100, unique=True, verbose_name="供應商名稱")
    contact = models.CharField(max_length=100, blank=True, verbose_name="聯絡人")
    email = models.EmailField(blank=True, verbose_name="電子郵件")
    phone = models.CharField(max_length=50, blank=True, verbose_name="電話")
    address = models.CharField(max_length=200, blank=True, verbose_name="地址")
    note = models.CharField(max_length=200, null=True, blank=True, verbose_name="備註")

    class Meta:
        ordering = ["name"]
        verbose_name = "供應商"
        verbose_name_plural = "供應商"

    def __str__(self) -> str:
        return f"{self.name} ({self.supplier_code})"


class HardwareDevice(models.Model):
    STATUS_AVAILABLE = "available"
    STATUS_SHIPPED = "shipped"
    STATUS_LOANED = "loaned"
    STATUS_SCRAPPED = "scrapped"
    STATUS_PENDING_SHIP = "pending_ship"
    STATUS_PENDING_LOAN = "pending_loan"
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "庫存中"),
        (STATUS_SHIPPED, "已出貨"),
        (STATUS_LOANED, "借測中"),
        (STATUS_PENDING_SHIP, "出貨待審"),
        (STATUS_PENDING_LOAN, "借測待審"),
        (STATUS_SCRAPPED, "已報廢"),
    ]

    code = models.CharField(max_length=50, unique=True, verbose_name="設備序號/資產編號")
    model = models.ForeignKey(
        HardwareModel, on_delete=models.PROTECT, related_name="devices", verbose_name="設備型號"
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="devices",
        verbose_name="進貨供應商",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_AVAILABLE, verbose_name="設備狀態"
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="probes",
        verbose_name="關聯客戶",
    )
    acquired_at = models.DateField(null=True, blank=True, verbose_name="購入/進貨日")
    expected_return_at = models.DateField(null=True, blank=True, verbose_name="預計歸還日")
    note = models.CharField(max_length=200, null=True, blank=True, verbose_name="備註")

    # Specification Overrides
    port_count = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="連接埠數覆寫"
    )
    cpu = models.CharField(max_length=50, blank=True, null=True, verbose_name="CPU 覆寫")
    memory = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="記憶體覆寫"
    )
    hdd = models.CharField(max_length=50, blank=True, null=True, verbose_name="硬碟覆寫")
    custom_values = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="動態規格覆寫數值",
        help_text="依型號自訂欄位規格填寫之單機覆寫參數 (JSON)",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="最後更新")
    is_deleted = models.BooleanField(default=False, verbose_name="已刪除")

    class Meta:
        ordering = ["code"]
        verbose_name = "硬體設備庫存"
        verbose_name_plural = "硬體設備庫存"

    def __str__(self) -> str:
        return f"{self.code} ({self.model.name if self.model else '未知型號'})"

    @property
    def effective_port_count(self) -> int:
        if self.port_count is not None:
            return self.port_count
        return self.model.port_count if self.model else 0

    @property
    def effective_cpu(self) -> str:
        if self.cpu:
            return self.cpu
        return self.model.cpu if self.model else ""

    @property
    def effective_memory(self) -> str:
        if self.memory:
            return self.memory
        return self.model.memory if self.model else ""

    @property
    def effective_hdd(self) -> str:
        if self.hdd:
            return self.hdd
        return self.model.hdd if self.model else ""

    @property
    def effective_supplier(self):
        if self.supplier:
            return self.supplier
        return self.model.supplier if self.model else None

    @property
    def is_specs_overridden(self) -> bool:
        return any(
            [
                self.port_count is not None,
                bool(self.cpu),
                bool(self.memory),
                bool(self.hdd),
                bool(self.custom_values),
            ]
        )

    @property
    def effective_custom_specs(self) -> list:
        """取得結合型號預設值與單機覆寫值之完整動態自訂規格清單"""
        schema = (self.model.custom_field_schema or []) if self.model else []
        overrides = self.custom_values or {}
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
            default_val = item.get("default", "")
            is_overridden = key in overrides and overrides[key] not in (None, "", [])
            raw_val = overrides[key] if is_overridden else default_val

            if isinstance(raw_val, list):
                val = ", ".join(str(x) for x in raw_val if str(x).strip())
            elif f_type == "boolean" and raw_val not in (None, ""):
                val = "是" if raw_val in (True, "true", "True", 1, "1", "是") else "否"
            elif f_type == "datetime" and raw_val not in (None, ""):
                s = str(raw_val).strip().replace("T", " ")
                val = s[:16] if len(s) >= 16 and s[4:5] == "-" and s[10:11] == " " else s
            else:
                val = raw_val

            if isinstance(default_val, list):
                default_display = ", ".join(str(x) for x in default_val if str(x).strip())
            elif f_type == "datetime" and default_val not in (None, ""):
                s_def = str(default_val).strip().replace("T", " ")
                default_display = s_def[:16] if len(s_def) >= 16 and s_def[4:5] == "-" and s_def[10:11] == " " else s_def
            else:
                default_display = default_val

            result.append({
                "key": key,
                "label": label,
                "value": val,
                "default": default_display,
                "is_overridden": is_overridden,
                "show_in_list": show_in_list,
            })
        # 若有不在 schema 內的歷史 custom_values 也保留呈現
        for k, v in overrides.items():
            if k not in schema_keys and v not in (None, "", []):
                val = ", ".join(str(x) for x in v) if isinstance(v, list) else v
                result.append({
                    "key": k,
                    "label": k,
                    "value": val,
                    "default": "",
                    "is_overridden": True,
                    "show_in_list": False,
                })
        return result

    @property
    def list_custom_specs(self) -> list:
        """取得設定為在列表中顯示 (show_in_list=True) 且有值之自訂規格清單"""
        return [
            item
            for item in self.effective_custom_specs
            if item.get("show_in_list") and item.get("value") not in (None, "", [])
        ]

    @property
    def active_maintenance_contracts(self):
        today = timezone.localdate()
        return [
            c for c in self.maintenance_contracts.all()
            if c.approval_status == "approved" and c.status == "active" and (not c.end_date or c.end_date >= today)
        ]

    @property
    def is_pending_outbound(self) -> bool:
        return self.status in (self.STATUS_PENDING_SHIP, self.STATUS_PENDING_LOAN)

    @property
    def pending_approval_request(self):
        return (
            self.outbound_approvals.filter(approval_status="pending")
            .select_related("customer", "requested_by", "contract", "old_probe")
            .order_by("-requested_at")
            .first()
        )

    @property
    def latest_rejected_approval_request(self):
        return (
            self.outbound_approvals.filter(approval_status="rejected")
            .select_related("customer", "requested_by", "reviewed_by", "contract")
            .order_by("-reviewed_at", "-id")
            .first()
        )

    @property
    def is_loan_overdue(self) -> bool:
        if self.status == self.STATUS_LOANED and self.expected_return_at:
            return self.expected_return_at < timezone.localdate()
        return False

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None
        old_customer = None

        if not self.supplier and self.model and self.model.supplier:
            self.supplier = self.model.supplier

        if not is_new:
            try:
                old_obj = HardwareDevice.objects.get(pk=self.pk)
                old_status = old_obj.status
                old_customer = old_obj.customer
            except HardwareDevice.DoesNotExist:
                pass

        super().save(*args, **kwargs)

        created_by = getattr(self, "_created_by", None)

        if is_new:
            occurred_at = timezone.now()
            if self.acquired_at:
                import datetime

                occurred_at = timezone.make_aware(
                    datetime.datetime.combine(self.acquired_at, datetime.time.min),
                    timezone.get_current_timezone(),
                )
            InventoryTransaction.objects.create(
                device=self,
                customer=None,
                type=InventoryTransaction.TYPE_IN,
                quantity=1,
                note=getattr(self, "_transaction_note", None)
                or self.note
                or "系統自動產生進貨紀錄",
                created_by=created_by,
                occurred_at=occurred_at,
            )
            if self.status == HardwareDevice.STATUS_SHIPPED:
                InventoryTransaction.objects.create(
                    device=self,
                    customer=self.customer,
                    type=InventoryTransaction.TYPE_OUT,
                    quantity=1,
                    note=getattr(self, "_transaction_note", None)
                    or f"自動交易：新增並出貨給客戶 {self.customer.name if self.customer else '未知'}",
                    created_by=created_by,
                )
            elif self.status == HardwareDevice.STATUS_LOANED:
                InventoryTransaction.objects.create(
                    device=self,
                    customer=self.customer,
                    type=InventoryTransaction.TYPE_LOAN,
                    quantity=1,
                    note=getattr(self, "_transaction_note", None)
                    or f"自動交易：新增並借測給客戶 {self.customer.name if self.customer else '未知'}",
                    created_by=created_by,
                    expected_return_at=self.expected_return_at,
                )
            elif self.status == HardwareDevice.STATUS_SCRAPPED:
                InventoryTransaction.objects.create(
                    device=self,
                    customer=None,
                    type=InventoryTransaction.TYPE_SCRAP,
                    quantity=1,
                    note=getattr(self, "_transaction_note", None) or "自動交易：新增並標記報廢",
                    created_by=created_by,
                )
        elif not getattr(self, "_skip_auto_txn", False):
            new_status = self.status
            new_customer = self.customer

            status_changed = old_status != new_status
            customer_changed = old_customer != new_customer
            has_customer_context = old_status in [
                HardwareDevice.STATUS_SHIPPED,
                HardwareDevice.STATUS_LOANED,
            ] or new_status in [HardwareDevice.STATUS_SHIPPED, HardwareDevice.STATUS_LOANED]

            if status_changed or (has_customer_context and customer_changed):
                transactions = []

                if old_status == HardwareDevice.STATUS_SHIPPED:
                    transactions.append(
                        {
                            "type": InventoryTransaction.TYPE_RETURN_OUT,
                            "customer": old_customer,
                            "note": f"自動交易：從客戶 {old_customer.name if old_customer else '未知'} 自動退貨以進行狀態/客戶變更",
                        }
                    )
                elif old_status == HardwareDevice.STATUS_LOANED:
                    transactions.append(
                        {
                            "type": InventoryTransaction.TYPE_RETURN_LOAN,
                            "customer": old_customer,
                            "note": f"自動交易：從客戶 {old_customer.name if old_customer else '未知'} 自動歸還以進行狀態/客戶變更",
                        }
                    )

                if new_status == HardwareDevice.STATUS_SHIPPED:
                    transactions.append(
                        {
                            "type": InventoryTransaction.TYPE_OUT,
                            "customer": new_customer,
                            "note": f"自動交易：自動出貨給客戶 {new_customer.name if new_customer else '未知'}",
                        }
                    )
                elif new_status == HardwareDevice.STATUS_LOANED:
                    transactions.append(
                        {
                            "type": InventoryTransaction.TYPE_LOAN,
                            "customer": new_customer,
                            "note": f"自動交易：自動借測給客戶 {new_customer.name if new_customer else '未知'}",
                        }
                    )
                elif new_status == HardwareDevice.STATUS_SCRAPPED:
                    transactions.append(
                        {
                            "type": InventoryTransaction.TYPE_SCRAP,
                            "customer": None,
                            "note": "自動交易：自庫存自動報廢",
                        }
                    )

                for txn_data in transactions:
                    txn_note = (
                        getattr(self, "_transaction_note", None) or txn_data["note"]
                    )
                    InventoryTransaction.objects.create(
                        device=self,
                        customer=txn_data["customer"],
                        type=txn_data["type"],
                        quantity=1,
                        note=txn_note,
                        created_by=created_by,
                        expected_return_at=self.expected_return_at
                        if txn_data["type"] == InventoryTransaction.TYPE_LOAN
                        else None,
                    )


class InventoryTransaction(models.Model):
    TYPE_IN = "IN"
    TYPE_OUT = "OUT"
    TYPE_LOAN = "LOAN"
    TYPE_RETURN_LOAN = "RETURN_LOAN"
    TYPE_RETURN_OUT = "RETURN_OUT"
    TYPE_SCRAP = "SCRAP"
    TYPE_CHOICES = [
        (TYPE_IN, "進貨入庫"),
        (TYPE_OUT, "正式出貨"),
        (TYPE_LOAN, "客戶借測"),
        (TYPE_RETURN_LOAN, "借測歸還"),
        (TYPE_RETURN_OUT, "出貨退貨"),
        (TYPE_SCRAP, "設備報廢"),
    ]

    device = models.ForeignKey(
        HardwareDevice, on_delete=models.CASCADE, related_name="transactions", verbose_name="關聯設備"
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_transactions",
        verbose_name="交易客戶",
    )
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name="交易類別")
    quantity = models.PositiveIntegerField(default=1, verbose_name="數量")
    occurred_at = models.DateTimeField(default=timezone.now, verbose_name="發生時間")
    expected_return_at = models.DateField(null=True, blank=True, verbose_name="預計歸還日")
    note = models.CharField(max_length=200, blank=True, verbose_name="交易備註")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_transactions",
        verbose_name="經手人員",
    )

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "庫存交易紀錄"
        verbose_name_plural = "庫存交易紀錄"

    def __str__(self) -> str:
        return f"{self.get_type_display()} - {self.device.code}"

    @property
    def probe(self):
        return self.device


class HardwareOutboundApproval(models.Model):
    REQUEST_SHIP = "ship"
    REQUEST_LOAN = "loan"
    REQUEST_RMA = "rma_replace"
    REQUEST_TYPE_CHOICES = [
        (REQUEST_SHIP, "正式出貨"),
        (REQUEST_LOAN, "客戶借測"),
        (REQUEST_RMA, "維護合約故障換機"),
    ]

    APPROVAL_PENDING = "pending"
    APPROVAL_APPROVED = "approved"
    APPROVAL_REJECTED = "rejected"
    APPROVAL_CHOICES = [
        (APPROVAL_PENDING, "待覆核"),
        (APPROVAL_APPROVED, "已核准"),
        (APPROVAL_REJECTED, "已退回"),
    ]

    device = models.ForeignKey(
        HardwareDevice,
        on_delete=models.CASCADE,
        related_name="outbound_approvals",
        verbose_name="目標出庫設備",
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hardware_outbound_approvals",
        verbose_name="目標配發客戶",
    )
    request_type = models.CharField(
        max_length=20,
        choices=REQUEST_TYPE_CHOICES,
        default=REQUEST_SHIP,
        verbose_name="出庫申請類別",
    )
    expected_return_at = models.DateField(null=True, blank=True, verbose_name="預計歸還日")
    note = models.TextField(blank=True, default="", verbose_name="出庫備註 / 換機原因")
    contract = models.ForeignKey(
        "customers.MaintenanceContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hardware_outbound_approvals",
        verbose_name="關聯維護合約 (RMA)",
    )
    old_probe = models.ForeignKey(
        HardwareDevice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rma_replaced_approvals",
        verbose_name="待換下舊機 (RMA)",
    )
    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_CHOICES,
        default=APPROVAL_PENDING,
        verbose_name="簽核狀態",
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_hardware_approvals",
        verbose_name="開單申請人員",
    )
    requested_at = models.DateTimeField(auto_now_add=True, verbose_name="申請時間")
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_hardware_approvals",
        verbose_name="覆核人員",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="覆核時間")
    rejection_reason = models.TextField(blank=True, default="", verbose_name="退回原因說明")

    class Meta:
        ordering = ["-requested_at", "-id"]
        verbose_name = "硬體出庫簽核單"
        verbose_name_plural = "硬體出庫簽核單"

    def __str__(self) -> str:
        cust_name = self.customer.name if self.customer else "未知客戶"
        return f"[{self.get_approval_status_display()}] {self.get_request_type_display()} - {self.device.code} -> {cust_name}"
