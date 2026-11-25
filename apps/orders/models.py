import os
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def _today_tw():
    return timezone.now().astimezone(TAIPEI_TZ).date()


def generate_order_no() -> str:
    """產生唯一預設訂單單號，格式: ORD-YYYYMMDD-XXXX"""
    today_str = timezone.now().astimezone(TAIPEI_TZ).strftime("%Y%m%d")
    prefix = f"ORD-{today_str}-"
    existing_nos = Order.objects.filter(order_no__startswith=prefix).values_list("order_no", flat=True)
    max_seq = 0
    for code in existing_nos:
        tail = code[len(prefix):]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:04d}"


class Order(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PENDING_APPROVAL = "pending_approval"
    STATUS_REJECTED = "rejected"
    STATUS_PENDING_SHIPMENT = "pending_shipment"
    STATUS_PENDING_PAYMENT = "pending_payment"
    STATUS_CLOSED = "closed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "草稿 / 報價議價中"),
        (STATUS_PENDING_APPROVAL, "待主管審核"),
        (STATUS_REJECTED, "審核退回 (待修正)"),
        (STATUS_PENDING_SHIPMENT, "待出貨 / 待交付"),
        (STATUS_PENDING_PAYMENT, "待收款"),
        (STATUS_CLOSED, "已結案"),
        (STATUS_CANCELLED, "已作廢"),
    ]

    TAX_EXCLUSIVE = "tax_exclusive"
    TAX_INCLUSIVE = "tax_inclusive"
    TAX_EXEMPT = "tax_exempt"
    TAX_MODE_CHOICES = [
        (TAX_EXCLUSIVE, "未稅外加 (營業稅 5%)"),
        (TAX_INCLUSIVE, "含稅 (內含 5% 營業稅)"),
        (TAX_EXEMPT, "免稅 / 零稅率"),
    ]

    order_no = models.CharField(
        max_length=50,
        unique=True,
        default=generate_order_no,
        verbose_name="訂單 / 報價單號",
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="採購客戶",
    )
    contact_person = models.ForeignKey(
        "customers.ContactPerson",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="客戶聯絡窗口",
    )
    contact_name = models.CharField(max_length=100, blank=True, default="", verbose_name="聯絡人姓名")
    contact_phone = models.CharField(max_length=50, blank=True, default="", verbose_name="聯絡電話")
    contact_email = models.EmailField(blank=True, default="", verbose_name="聯絡信箱")
    title = models.CharField(max_length=150, verbose_name="專案 / 訂單主旨")
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        verbose_name="訂單狀態",
    )
    current_version = models.PositiveIntegerField(default=1, verbose_name="目前報價版次")
    quote_date = models.DateField(default=_today_tw, verbose_name="報價日期")
    valid_until = models.DateField(null=True, blank=True, verbose_name="報價有效期限")

    tax_mode = models.CharField(
        max_length=20,
        choices=TAX_MODE_CHOICES,
        default=TAX_EXCLUSIVE,
        verbose_name="計稅方式",
    )
    subtotal = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="明細小計")
    discount_amount = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="議價折讓金額")
    tax_amount = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="營業稅額")
    total_amount = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="應收總計金額")

    payment_terms = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name="付款條件說明",
        help_text="例如：簽約後月結 30 天 / 訂金 30% + 驗收尾款 70%",
    )
    terms_note = models.TextField(blank=True, default="", verbose_name="報價單條款與備註")
    internal_note = models.TextField(blank=True, default="", verbose_name="內部業務備註 (不印於報價單)")
    no_signed_quote_reason = models.TextField(
        blank=True,
        default="",
        verbose_name="未附回簽報價單說明",
        help_text="若未上傳客戶回簽報價單附件即送審，請說明原因 (例如：急單已由客戶 Email 確認)",
    )

    salesperson = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_orders",
        verbose_name="負責業務",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_orders",
        verbose_name="建立人員",
    )
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name="送審時間")

    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_orders",
        verbose_name="審核主管",
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="審核時間")
    rejection_reason = models.TextField(blank=True, default="", verbose_name="退回修正原因")

    shipped_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipped_orders",
        verbose_name="出貨交付工程師",
    )
    shipped_at = models.DateTimeField(null=True, blank=True, verbose_name="出貨交付完成時間")
    shipment_note = models.TextField(blank=True, default="", verbose_name="工程師出貨/交付備註")

    closed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_orders",
        verbose_name="入帳結案人員 (管理部)",
    )
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="結案時間")
    closing_note = models.TextField(blank=True, default="", verbose_name="結案說明備註")

    cancelled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_orders",
        verbose_name="作廢操作人員",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name="作廢時間")
    cancel_reason = models.TextField(blank=True, default="", verbose_name="作廢原因")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="建立時間")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新時間")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "客戶訂單"
        verbose_name_plural = "客戶訂單"

    def __str__(self) -> str:
        return f"{self.order_no} - {self.title} ({self.customer.name})"

    @property
    def version_label(self) -> str:
        return f"v{self.current_version}"

    @property
    def full_quote_no(self) -> str:
        return f"{self.order_no}-v{self.current_version}"

    @property
    def is_editable_by_sales(self) -> bool:
        return self.status in (self.STATUS_DRAFT, self.STATUS_REJECTED)

    @property
    def badge_class(self) -> str:
        mapping = {
            self.STATUS_DRAFT: "badge-secondary",
            self.STATUS_PENDING_APPROVAL: "badge-loaned",
            self.STATUS_REJECTED: "badge-scrapped",
            self.STATUS_PENDING_SHIPMENT: "badge-repair",
            self.STATUS_PENDING_PAYMENT: "badge-shipped",
            self.STATUS_CLOSED: "badge-available",
            self.STATUS_CANCELLED: "badge-secondary",
        }
        return mapping.get(self.status, "badge-secondary")

    @property
    def has_signed_quote_attachment(self) -> bool:
        return self.attachments.filter(attachment_type=OrderAttachment.TYPE_SIGNED_QUOTE).exists()

    @property
    def confirmed_paid_amount(self) -> Decimal:
        total = Decimal("0")
        for p in self.payments.all():
            if p.status == OrderPayment.STATUS_CONFIRMED:
                total += p.amount or Decimal("0")
        return total

    @property
    def reported_pending_amount(self) -> Decimal:
        total = Decimal("0")
        for p in self.payments.all():
            if p.status == OrderPayment.STATUS_PENDING:
                total += p.amount or Decimal("0")
        return total

    @property
    def has_pending_payment_review(self) -> bool:
        return self.payments.filter(status=OrderPayment.STATUS_PENDING).exists()

    @property
    def unpaid_balance(self) -> Decimal:
        bal = (self.total_amount or Decimal("0")) - self.confirmed_paid_amount
        return bal if bal > Decimal("0") else Decimal("0")

    @property
    def paid_progress_percent(self) -> int:
        if not self.total_amount or self.total_amount <= 0:
            return 100 if self.status == self.STATUS_CLOSED else 0
        pct = int((self.confirmed_paid_amount * Decimal("100")) / self.total_amount)
        return min(max(pct, 0), 100)

    def recalculate_totals(self, save: bool = True) -> None:
        """依據目前明細列、折讓金額與計稅方式重新計算金額總計"""
        items = list(self.items.all())
        sub = Decimal("0")
        for item in items:
            qty = Decimal(str(item.quantity or 0))
            price = Decimal(str(item.unit_price or 0))
            line_tot = (qty * price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            if item.line_total != line_tot:
                item.line_total = line_tot
                item.save(update_fields=["line_total"])
            sub += line_tot

        discount = Decimal(str(self.discount_amount or 0))
        if discount < Decimal("0"):
            discount = Decimal("0")
        net = sub - discount
        if net < Decimal("0"):
            net = Decimal("0")

        if self.tax_mode == self.TAX_EXCLUSIVE:
            tax = (net * Decimal("0.05")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            total = net + tax
        elif self.tax_mode == self.TAX_INCLUSIVE:
            # 含稅總額即為 net，內含稅額 = total - round(total / 1.05)
            total = net
            pre_tax = (total / Decimal("1.05")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            tax = total - pre_tax
        else:  # TAX_EXEMPT
            tax = Decimal("0")
            total = net

        self.subtotal = sub
        self.discount_amount = discount
        self.tax_amount = tax
        self.total_amount = total
        if save and self.pk:
            self.save(
                update_fields=[
                    "subtotal",
                    "discount_amount",
                    "tax_amount",
                    "total_amount",
                    "updated_at",
                ]
            )


class OrderItem(models.Model):
    TYPE_LICENSE = "license"
    TYPE_WARRANTY = "warranty"
    TYPE_HARDWARE = "hardware"
    TYPE_SERVICE = "service"

    ITEM_TYPE_CHOICES = [
        (TYPE_LICENSE, "軟體授權"),
        (TYPE_WARRANTY, "維護保固"),
        (TYPE_HARDWARE, "硬體設備"),
        (TYPE_SERVICE, "專業服務 / 其他客製項目"),
    ]

    LICENSE_TYPE_CHOICES = [
        ("perpetual", "永久買斷"),
        ("subscription", "定期訂閱"),
        ("trial", "測試授權"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="所屬訂單",
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="排序")
    item_type = models.CharField(
        max_length=20,
        choices=ITEM_TYPE_CHOICES,
        default=TYPE_LICENSE,
        verbose_name="品項類型",
    )
    name = models.CharField(max_length=150, verbose_name="品項名稱")
    description = models.CharField(max_length=300, blank=True, default="", verbose_name="規格 / 期間與說明")

    product = models.ForeignKey(
        "licenses.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        verbose_name="關聯軟體產品",
    )
    license_type = models.CharField(
        max_length=20,
        choices=LICENSE_TYPE_CHOICES,
        blank=True,
        default="perpetual",
        verbose_name="授權類型",
    )
    hardware_model = models.ForeignKey(
        "hardware.HardwareModel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        verbose_name="關聯硬體型號",
    )
    start_date = models.DateField(null=True, blank=True, verbose_name="起始日期")
    end_date = models.DateField(null=True, blank=True, verbose_name="到期日期")
    sla_level = models.CharField(max_length=50, blank=True, default="", verbose_name="服務等級 (SLA)")

    quantity = models.PositiveIntegerField(default=1, verbose_name="數量")
    unit = models.CharField(max_length=20, default="式", verbose_name="單位")
    unit_price = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="未稅/單價")
    line_total = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="小計")

    generated_license = models.ForeignKey(
        "licenses.CustomerLicense",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="origin_order_items",
        verbose_name="已開立軟體授權書",
    )
    generated_contract = models.ForeignKey(
        "customers.MaintenanceContract",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="origin_order_items",
        verbose_name="已開立維護合約",
    )
    allocated_devices = models.ManyToManyField(
        "hardware.HardwareDevice",
        blank=True,
        related_name="allocated_order_items",
        verbose_name="實際配發硬體設備序號",
    )

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "訂單品項明細"
        verbose_name_plural = "訂單品項明細"

    def __str__(self) -> str:
        return f"[{self.get_item_type_display()}] {self.name} x{self.quantity}"

    def save(self, *args, **kwargs):
        qty = Decimal(str(self.quantity or 0))
        price = Decimal(str(self.unit_price or 0))
        self.line_total = (qty * price).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        super().save(*args, **kwargs)


class OrderVersion(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name="所屬訂單",
    )
    version_no = models.PositiveIntegerField(verbose_name="報價版次")
    change_note = models.CharField(max_length=300, blank=True, default="", verbose_name="版次變更 / 議價說明")
    snapshot_data = models.JSONField(default=dict, blank=True, verbose_name="報價單快照內容")
    subtotal = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="小計金額")
    discount_amount = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="折讓金額")
    tax_amount = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="營業稅額")
    total_amount = models.DecimalField(max_digits=14, decimal_places=0, default=0, verbose_name="總計金額")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_versions",
        verbose_name="版本建立人員",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="版本建立時間")

    class Meta:
        unique_together = ("order", "version_no")
        ordering = ["-version_no"]
        verbose_name = "報價版本快照"
        verbose_name_plural = "報價版本快照"

    def __str__(self) -> str:
        return f"{self.order.order_no} - v{self.version_no} (NT$ {self.total_amount:,})"


class OrderAttachment(models.Model):
    TYPE_SIGNED_QUOTE = "signed_quote"
    TYPE_PO_CONTRACT = "po_contract"
    TYPE_PAYMENT_PROOF = "payment_proof"
    TYPE_DELIVERY_DOC = "delivery_doc"
    TYPE_OTHER = "other"

    ATTACHMENT_TYPE_CHOICES = [
        (TYPE_SIGNED_QUOTE, "客戶回簽報價單"),
        (TYPE_PO_CONTRACT, "客戶採購單 (PO) / 合約書"),
        (TYPE_PAYMENT_PROOF, "匯款水單 / 收款憑證"),
        (TYPE_DELIVERY_DOC, "出貨單 / 簽收單"),
        (TYPE_OTHER, "其他佐證附件"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="所屬訂單",
    )
    attachment_type = models.CharField(
        max_length=30,
        choices=ATTACHMENT_TYPE_CHOICES,
        default=TYPE_SIGNED_QUOTE,
        verbose_name="附件類型",
    )
    file = models.FileField(upload_to="orders/%Y/%m/", verbose_name="附件檔案")
    filename = models.CharField(max_length=255, verbose_name="原始檔名")
    file_size = models.PositiveIntegerField(default=0, verbose_name="檔案大小 (Bytes)")
    note = models.CharField(max_length=200, blank=True, default="", verbose_name="附件備註")
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_attachments",
        verbose_name="上傳人員",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="上傳時間")

    class Meta:
        ordering = ["-uploaded_at", "-id"]
        verbose_name = "訂單佐證附件"
        verbose_name_plural = "訂單佐證附件"

    def __str__(self) -> str:
        return f"[{self.get_attachment_type_display()}] {self.filename}"

    @property
    def formatted_size(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size} B"
        if self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        return f"{self.file_size / (1024 * 1024):.1f} MB"

    @property
    def file_ext(self) -> str:
        return os.path.splitext(self.filename)[1].lower()

    @property
    def is_image(self) -> bool:
        return self.file_ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

    @property
    def is_pdf(self) -> bool:
        return self.file_ext == ".pdf"


class OrderPayment(models.Model):
    METHOD_BANK_TRANSFER = "bank_transfer"
    METHOD_CHECK = "check"
    METHOD_CASH = "cash"
    METHOD_OTHER = "other"

    METHOD_CHOICES = [
        (METHOD_BANK_TRANSFER, "銀行匯款 / 轉帳"),
        (METHOD_CHECK, "支票"),
        (METHOD_CASH, "現金"),
        (METHOD_OTHER, "其他方式"),
    ]

    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_PENDING, "待管理部確認入帳"),
        (STATUS_CONFIRMED, "已確認入帳"),
        (STATUS_REJECTED, "帳務退回修正"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="所屬訂單",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=0, verbose_name="本次回報金額")
    payment_date = models.DateField(default=_today_tw, verbose_name="收款 / 匯款日期")
    payment_method = models.CharField(
        max_length=20,
        choices=METHOD_CHOICES,
        default=METHOD_BANK_TRANSFER,
        verbose_name="付款方式",
    )
    reference_no = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="帳號後五碼 / 支票號 / 發票號",
    )
    proof_file = models.FileField(
        upload_to="order_payments/%Y/%m/",
        null=True,
        blank=True,
        verbose_name="匯款水單 / 憑證附件",
    )
    note = models.TextField(blank=True, default="", verbose_name="收款回報備註")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name="對帳狀態",
    )
    reported_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_order_payments",
        verbose_name="回報業務",
    )
    reported_at = models.DateTimeField(auto_now_add=True, verbose_name="回報時間")
    confirmed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_order_payments",
        verbose_name="入帳確認人 (管理部)",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="入帳確認時間")
    review_note = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="帳務覆核備註 / 退回原因",
    )

    class Meta:
        ordering = ["-payment_date", "-id"]
        verbose_name = "訂單收款與入帳紀錄"
        verbose_name_plural = "訂單收款與入帳紀錄"

    def __str__(self) -> str:
        return f"{self.order.order_no} - NT$ {self.amount:,} ({self.get_status_display()})"
