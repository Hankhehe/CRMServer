from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import JsonResponse
from django.utils import timezone

from django.views.decorators.http import require_POST
from django.db import transaction

from apps.core.permissions import (
    user_can_edit_hardware,
    user_can_edit_hardware_models,
    user_can_approve_hardware,
    is_hardware_approval_required_for_user,
    log_action,
)
from apps.core.notification_service import notify_customer_followers
from apps.customers.models import Customer
from .models import HardwareDevice, HardwareModel, Supplier, InventoryTransaction, HardwareOutboundApproval
from .approval_service import (
    create_hardware_outbound_approval,
    approve_hardware_outbound_request,
    reject_hardware_outbound_request,
)
from .forms import (
    HardwareDeviceForm,
    HardwareModelForm,
    SupplierForm,
    TransactionForm,
)


@login_required
def hardware_dashboard(request):
    today = timezone.localdate()

    # KPI counts
    device_counts = HardwareDevice.objects.filter(is_deleted=False).aggregate(
        total=Count("id"),
        available=Count("id", filter=Q(status=HardwareDevice.STATUS_AVAILABLE)),
        shipped=Count("id", filter=Q(status=HardwareDevice.STATUS_SHIPPED)),
        loaned=Count("id", filter=Q(status=HardwareDevice.STATUS_LOANED)),
        pending_outbound=Count(
            "id",
            filter=Q(
                status__in=[HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN]
            ),
        ),
        scrapped=Count("id", filter=Q(status=HardwareDevice.STATUS_SCRAPPED)),
    )
    overdue_loan_count = HardwareDevice.objects.filter(
        status=HardwareDevice.STATUS_LOANED,
        is_deleted=False,
        expected_return_at__lt=today,
    ).count()

    # Model inventory summary
    models_summary = (
        HardwareModel.objects.annotate(
            count_available=Count(
                "devices",
                filter=Q(
                    devices__status=HardwareDevice.STATUS_AVAILABLE, devices__is_deleted=False
                ),
            ),
            count_loaned=Count(
                "devices",
                filter=Q(
                    devices__status=HardwareDevice.STATUS_LOANED, devices__is_deleted=False
                ),
            ),
            count_shipped=Count(
                "devices",
                filter=Q(
                    devices__status=HardwareDevice.STATUS_SHIPPED, devices__is_deleted=False
                ),
            ),
            count_scrapped=Count(
                "devices",
                filter=Q(
                    devices__status=HardwareDevice.STATUS_SCRAPPED, devices__is_deleted=False
                ),
            ),
            count_total=Count("devices", filter=Q(devices__is_deleted=False)),
        )
        .select_related("supplier")
        .order_by("name")
    )

    # Loaned devices tracking
    loan_search = request.GET.get("q_loan", "").strip()
    filter_overdue = request.GET.get("overdue", "").strip()

    loaned_devices_qs = HardwareDevice.objects.filter(
        status=HardwareDevice.STATUS_LOANED, is_deleted=False
    ).select_related("model", "customer")

    if loan_search:
        loaned_devices_qs = loaned_devices_qs.filter(
            Q(code__icontains=loan_search)
            | Q(model__name__icontains=loan_search)
            | Q(model__model_code__icontains=loan_search)
            | Q(customer__name__icontains=loan_search)
            | Q(customer__code__icontains=loan_search)
        )

    if filter_overdue == "1":
        loaned_devices_qs = loaned_devices_qs.filter(expected_return_at__lt=today)

    loaned_devices = list(loaned_devices_qs.order_by("expected_return_at", "code"))

    for p in loaned_devices:
        if p.expected_return_at:
            delta = (p.expected_return_at - today).days
            p.days_diff = delta
            p.is_overdue = delta < 0
            p.abs_days_diff = abs(delta)
        else:
            p.days_diff = None
            p.is_overdue = False
            p.abs_days_diff = None

    context = {
        "today": today,
        "device_counts": device_counts,
        "probe_counts": device_counts,
        "overdue_loan_count": overdue_loan_count,
        "models_summary": models_summary,
        "loaned_devices": loaned_devices,
        "loaned_probes": loaned_devices,
        "loan_search": loan_search,
        "filter_overdue": filter_overdue,
        "can_edit_hardware": user_can_edit_hardware(request.user),
        "can_edit_probe": user_can_edit_hardware(request.user),
    }
    return render(request, "hardware/hardware_dashboard.html", context)


@login_required
def device_list(request):
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    approval_filter = request.GET.get("approval", "").strip()
    model_filter = request.GET.get("model", "").strip()
    supplier_filter = request.GET.get("supplier", "").strip()
    order = request.GET.get("order", "code")

    base_devices = HardwareDevice.objects.filter(is_deleted=False).select_related(
        "model__supplier", "supplier", "customer"
    ).prefetch_related("outbound_approvals__requested_by", "outbound_approvals__reviewed_by")

    pending_outbound_count = HardwareDevice.objects.filter(
        is_deleted=False,
        status__in=[HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN],
    ).count()

    devices = base_devices

    if query:
        devices = devices.filter(
            Q(code__icontains=query)
            | Q(note__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__code__icontains=query)
            | Q(model__name__icontains=query)
        )

    if approval_filter == "pending":
        devices = devices.filter(
            status__in=[HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN]
        )

    if status_filter:
        if status_filter == "pending_outbound":
            devices = devices.filter(
                status__in=[HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN]
            )
        else:
            devices = devices.filter(status=status_filter)

    if model_filter:
        devices = devices.filter(model_id=model_filter)

    if supplier_filter:
        devices = devices.filter(
            Q(supplier_id=supplier_filter) | (Q(supplier__isnull=True) & Q(model__supplier_id=supplier_filter))
        )

    if order:
        valid_orders = ["code", "-code", "acquired_at", "-acquired_at", "status", "-status"]
        if order in valid_orders:
            devices = devices.order_by(order)
        else:
            devices = devices.order_by("code")

    paginator = Paginator(devices, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "hardware/device_list.html",
        {
            "page_obj": page_obj,
            "query": query,
            "status_filter": status_filter,
            "approval_filter": approval_filter,
            "pending_outbound_count": pending_outbound_count,
            "model_filter": model_filter,
            "supplier_filter": supplier_filter,
            "order": order,
            "models": HardwareModel.objects.all(),
            "suppliers": Supplier.objects.all(),
            "can_edit_hardware": user_can_edit_hardware(request.user),
            "can_edit_probe": user_can_edit_hardware(request.user),
            "can_approve_hardware": user_can_approve_hardware(request.user),
            "total_count": paginator.count,
        },
    )


@login_required
def device_export(request):
    from apps.core.excel_utils import export_to_excel

    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    approval_filter = request.GET.get("approval", "").strip()
    model_filter = request.GET.get("model", "").strip()
    supplier_filter = request.GET.get("supplier", "").strip()
    order = request.GET.get("order", "code")

    devices = HardwareDevice.objects.filter(is_deleted=False).select_related(
        "model__supplier", "supplier", "customer"
    )

    if query:
        devices = devices.filter(
            Q(code__icontains=query)
            | Q(note__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__code__icontains=query)
            | Q(model__name__icontains=query)
        )

    if approval_filter == "pending":
        devices = devices.filter(
            status__in=[HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN]
        )

    if status_filter:
        if status_filter == "pending_outbound":
            devices = devices.filter(
                status__in=[HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN]
            )
        else:
            devices = devices.filter(status=status_filter)

    if model_filter:
        devices = devices.filter(model_id=model_filter)

    if supplier_filter:
        devices = devices.filter(
            Q(supplier_id=supplier_filter) | (Q(supplier__isnull=True) & Q(model__supplier_id=supplier_filter))
        )

    if order:
        valid_orders = ["code", "-code", "acquired_at", "-acquired_at", "status", "-status"]
        if order in valid_orders:
            devices = devices.order_by(order)
        else:
            devices = devices.order_by("code")

    headers = [
        "設備序號/資產編號",
        "設備型號代碼",
        "設備型號名稱",
        "進銷存狀態",
        "購買/關聯客戶代號",
        "購買/關聯客戶名稱",
        "進貨供應商",
        "購入/進貨日",
        "預計歸還日",
        "網路連接埠數",
        "CPU 規格",
        "記憶體",
        "硬碟空間",
        "備註說明",
        "建立時間",
    ]

    # 掃描當次匯出資料中所有型號規格 Schema 與 custom_values 出現過的 key
    custom_field_keys = []
    custom_field_label_map = {}

    for d in devices:
        schema = (d.model.custom_field_schema or []) if d.model else []
        for item in schema:
            k = item.get("key")
            if k and k not in custom_field_keys:
                custom_field_keys.append(k)
                custom_field_label_map[k] = item.get("label") or k
        overrides = d.custom_values or {}
        for k in overrides.keys():
            if k and k not in custom_field_keys:
                custom_field_keys.append(k)
                custom_field_label_map[k] = k

    # 動態展開自訂規格表頭
    for k in custom_field_keys:
        headers.append(f"自訂: {custom_field_label_map[k]}")

    status_dict = dict(HardwareDevice.STATUS_CHOICES)

    rows = []
    for d in devices:
        supplier_name = ""
        eff_sup = d.effective_supplier
        if eff_sup:
            supplier_name = eff_sup.name

        spec_dict = {item.get("key"): item.get("value") for item in d.effective_custom_specs}

        row = [
            d.code,
            d.model.model_code if d.model else "",
            d.model.name if d.model else "",
            status_dict.get(d.status, d.status),
            d.customer.code if d.customer else "",
            d.customer.name if d.customer else "",
            supplier_name,
            d.acquired_at or "",
            d.expected_return_at or "",
            d.effective_port_count,
            d.effective_cpu,
            d.effective_memory,
            d.effective_hdd,
            d.note or "",
            d.created_at,
        ]

        # 填入各動態自訂規格
        for k in custom_field_keys:
            val = spec_dict.get(k, "")
            if val in (None, ""):
                display_val = ""
            elif isinstance(val, list):
                display_val = ", ".join(str(x) for x in val if str(x).strip())
            else:
                display_val = str(val)
            row.append(display_val)

        rows.append(row)

    return export_to_excel("hardware_devices", "硬體設備庫存清單", headers, rows)


def _parse_date_str(val_str: str):
    """解析日期字串，支援 YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD 等常見格式"""
    import datetime as dt
    val_str = (val_str or "").strip()
    if not val_str:
        return None
    # 若帶有時間部分 (如 '2026-10-23 00:00:00')，取前半段日期
    if " " in val_str:
        val_str = val_str.split(" ")[0].strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(val_str, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"日期格式不正確：「{val_str}」，請使用 YYYY-MM-DD 格式（例如 2026-10-23）")


@login_required
def device_import_template(request):
    from apps.core.excel_utils import generate_import_template_excel

    all_models = list(HardwareModel.objects.select_related("supplier").all().order_by("name"))
    all_suppliers = list(Supplier.objects.all().order_by("name"))

    # 收集所有型號定義過的自訂欄位 Schema
    custom_field_keys = []
    custom_field_meta = {}
    for m in all_models:
        for item in (m.custom_field_schema or []):
            k = item.get("key")
            if k and k not in custom_field_keys:
                custom_field_keys.append(k)
                custom_field_meta[k] = {
                    "key": k,
                    "label": item.get("label") or k,
                    "type": item.get("type", "text"),
                    "options": item.get("options", []),
                    "default": item.get("default", ""),
                    "models": [m.name],
                }
            elif k in custom_field_meta and m.name not in custom_field_meta[k]["models"]:
                custom_field_meta[k]["models"].append(m.name)

    headers = [
        "設備序號/資產編號*",
        "設備型號名稱*",
        "進銷存狀態",
        "購買/關聯客戶代號",
        "進貨供應商",
        "購入/進貨日",
        "預計歸還日",
        "網路連接埠數",
        "CPU 規格",
        "記憶體",
        "硬碟空間",
        "備註說明",
    ]
    for k in custom_field_keys:
        headers.append(f"自訂: {custom_field_meta[k]['label']}")

    required_indices = {0, 1}

    sample_model_name = all_models[0].name if all_models else "範例型號名稱"
    sample_supplier_name = all_suppliers[0].name if all_suppliers else ""
    sample_cust = Customer.objects.order_by("code").first()
    sample_cust_code = sample_cust.code if sample_cust else "80123456"

    row1 = [
        "SN-DEMO-0001",
        sample_model_name,
        "庫存中",
        "",
        sample_supplier_name,
        "2026-10-01",
        "",
        "",
        "",
        "",
        "",
        "範例：在庫設備 (正式匯入前請刪除此範例列)",
    ] + [""] * len(custom_field_keys)

    row2 = [
        "SN-DEMO-0002",
        sample_model_name,
        "已出貨",
        sample_cust_code,
        sample_supplier_name,
        "2026-10-01",
        "",
        "",
        "",
        "",
        "",
        "範例：已出貨給客戶之設備 (正式匯入前請刪除此範例列)",
    ] + [""] * len(custom_field_keys)

    sample_rows = [row1, row2]

    # 下拉選單設定
    dropdown_configs = [
        {"col_idx": 3, "options": ["庫存中", "已出貨", "借測中", "已報廢"]},
    ]
    if all_models:
        model_names = [m.name for m in all_models]
        if len(",".join(model_names)) <= 240:
            dropdown_configs.append({"col_idx": 2, "options": model_names})
    if all_suppliers:
        sup_names = [s.name for s in all_suppliers]
        if len(",".join(sup_names)) <= 240:
            dropdown_configs.append({"col_idx": 5, "options": sup_names})

    # 說明頁區塊
    field_rules_rows = [
        ["設備序號/資產編號", "必填 (*)", "全系統唯一序號。若序號已存在於系統，將更新該設備資料；若為新序號則自動建立並產生進貨交易紀錄。", "SN-2026-0001"],
        ["設備型號名稱", "必填 (*)", "必須填寫系統「硬體規格主檔」中已存在的「型號名稱」或「型號代碼」（請參考下方型號對照表）。若型號不存在將報錯提醒先建立型號。", sample_model_name],
        ["進銷存狀態", "選填 (預設: 庫存中)", "可填寫：庫存中 (available)、已出貨 (shipped)、借測中 (loaned)、已報廢 (scrapped)。", "庫存中 / 已出貨"],
        ["購買/關聯客戶代號", "條件必填", "當狀態為「已出貨」或「借測中」時必填！可填寫系統已存在的「客戶代號」或「客戶名稱」。庫存中或已報廢時請留空。", sample_cust_code],
        ["進貨供應商", "選填", "若填寫，必須為系統「供應商管理」中已存在的「供應商名稱」或「供應商代號」。若留空則自動繼承該型號的預設供應商。", sample_supplier_name or "原廠供應商"],
        ["購入/進貨日", "選填", "日期格式：YYYY-MM-DD（例如 2026-10-23）。", "2026-10-23"],
        ["預計歸還日", "條件必填", "當狀態為「借測中」時必填！日期格式：YYYY-MM-DD。", "2026-12-31"],
        ["網路連接埠數 / CPU / 記憶體 / 硬碟", "選填 (單機覆寫)", "留空即自動繼承該設備型號的標準規格；僅在單機有客製升級或與型號標準不同時填寫。", "留空繼承預設"],
        ["自訂: <規格名稱>", "選填 (動態規格覆寫)", "對應各型號的自訂擴充規格欄位。留空則繼承型號預設值。", "留空繼承預設"],
        ["備註說明", "選填", "設備位置、專案編號或其他補充說明。", "機房備品"],
    ]

    model_ref_rows = []
    for m in all_models:
        custom_str = ", ".join([f"{c.get('label')}({c.get('key')})" for c in (m.custom_field_schema or [])]) or "無"
        model_ref_rows.append([
            m.name,
            m.model_code,
            m.supplier.name if m.supplier else "(未指定)",
            str(m.port_count),
            m.cpu or "-",
            m.memory or "-",
            m.hdd or "-",
            custom_str,
        ])
    if not model_ref_rows:
        model_ref_rows.append(["(目前系統尚無硬體規格型號，請先至「硬體規格主檔」新增型號後再匯入)", "-", "-", "-", "-", "-", "-", "-"])

    supplier_ref_rows = [
        [s.name, s.supplier_code, s.contact or "-", s.phone or "-"]
        for s in all_suppliers
    ]
    if not supplier_ref_rows:
        supplier_ref_rows.append(["(目前系統尚無供應商資料)", "-", "-", "-"])

    guide_sections = [
        {
            "title": "一、硬體設備匯入欄位說明與驗證規則",
            "headers": ["欄位名稱", "是否必填", "格式與填寫規則說明", "範例值"],
            "rows": field_rules_rows,
        },
        {
            "title": "二、系統現有「硬體規格型號」對照表 (匯入時請填寫型號名稱或型號代碼)",
            "headers": ["型號名稱 (建議填此欄)", "型號代碼 (亦可填此欄)", "預設供應商", "預設埠數", "預設 CPU", "預設記憶體", "預設硬碟", "自訂擴充規格欄位"],
            "rows": model_ref_rows,
        },
        {
            "title": "三、系統現有「供應商」對照表 (選填，留空則繼承型號預設供應商)",
            "headers": ["供應商名稱", "供應商代號", "聯絡人", "電話"],
            "rows": supplier_ref_rows,
        },
    ]

    return generate_import_template_excel(
        filename_prefix="hardware_import_template",
        main_sheet_name="硬體設備匯入資料填寫",
        headers=headers,
        required_indices=required_indices,
        sample_rows=sample_rows,
        guide_sections=guide_sections,
        dropdown_configs=dropdown_configs,
    )


@login_required
def device_import(request):
    if not user_can_edit_hardware(request.user):
        messages.error(request, "權限不足：您沒有匯入硬體設備的權限。")
        return redirect("hardware-list")

    from django.db import transaction
    from apps.core.excel_utils import parse_uploaded_tabular_file
    from .forms import HardwareImportForm

    error_list = []

    all_models = list(HardwareModel.objects.select_related("supplier").all())
    all_suppliers = list(Supplier.objects.all())
    all_customers = list(Customer.objects.all())

    # 建立型號查找表 (支援以型號名稱或型號代碼不分大小寫查找)
    model_by_name = {m.name.upper(): m for m in all_models}
    model_by_code = {m.model_code.upper(): m for m in all_models}

    # 建立供應商查找表 (支援以名稱或代號查找)
    supplier_lookup = {}
    for s in all_suppliers:
        supplier_lookup[s.name.upper()] = s
        supplier_lookup[s.supplier_code.upper()] = s

    # 建立客戶查找表 (支援以客戶代號或客戶名稱查找)
    customer_by_code = {c.code.upper(): c for c in all_customers}
    customer_by_name = {c.name.upper(): c for c in all_customers}

    # 建立自訂規格欄位名稱/Key 對應
    custom_label_to_key = {}
    for m in all_models:
        for item in (m.custom_field_schema or []):
            k = item.get("key")
            lbl = item.get("label")
            if k:
                custom_label_to_key[k.lower()] = k
            if lbl and k:
                custom_label_to_key[lbl.lower()] = k

    header_alias_map = {
        "設備序號/資產編號": "code",
        "設備序號": "code",
        "資產編號": "code",
        "序號": "code",
        "code": "code",
        "設備型號名稱": "model_name",
        "設備型號": "model_name",
        "規格型號": "model_name",
        "型號名稱": "model_name",
        "型號": "model_name",
        "model_name": "model_name",
        "model": "model_name",
        "設備型號代碼": "model_code",
        "型號代碼": "model_code",
        "model_code": "model_code",
        "進銷存狀態": "status",
        "設備狀態": "status",
        "狀態": "status",
        "status": "status",
        "購買/關聯客戶代號": "customer_code",
        "關聯客戶代號": "customer_code",
        "客戶代號": "customer_code",
        "關聯客戶": "customer_code",
        "customer_code": "customer_code",
        "customer": "customer_code",
        "購買/關聯客戶名稱": "customer_name",
        "關聯客戶名稱": "customer_name",
        "客戶名稱": "customer_name",
        "customer_name": "customer_name",
        "進貨供應商": "supplier_name",
        "供應商": "supplier_name",
        "原廠/供應商": "supplier_name",
        "supplier_name": "supplier_name",
        "supplier": "supplier_name",
        "購入/進貨日": "acquired_at",
        "進貨日": "acquired_at",
        "進貨購入日期": "acquired_at",
        "購入日期": "acquired_at",
        "acquired_at": "acquired_at",
        "預計歸還日": "expected_return_at",
        "歸還日": "expected_return_at",
        "expected_return_at": "expected_return_at",
        "網路連接埠數": "port_count",
        "連接埠數": "port_count",
        "連接埠數覆寫": "port_count",
        "port_count": "port_count",
        "cpu 規格": "cpu",
        "cpu": "cpu",
        "cpu 規格覆寫": "cpu",
        "cpu 覆寫": "cpu",
        "記憶體": "memory",
        "記憶體規格覆寫": "memory",
        "記憶體覆寫": "memory",
        "memory": "memory",
        "硬碟空間": "hdd",
        "硬碟": "hdd",
        "硬碟規格覆寫": "hdd",
        "硬碟覆寫": "hdd",
        "hdd": "hdd",
        "備註說明": "note",
        "設備備註說明": "note",
        "備註": "note",
        "note": "note",
    }

    status_map = {
        "available": HardwareDevice.STATUS_AVAILABLE,
        "庫存中": HardwareDevice.STATUS_AVAILABLE,
        "在庫": HardwareDevice.STATUS_AVAILABLE,
        "在庫可用": HardwareDevice.STATUS_AVAILABLE,
        "shipped": HardwareDevice.STATUS_SHIPPED,
        "已出貨": HardwareDevice.STATUS_SHIPPED,
        "出貨": HardwareDevice.STATUS_SHIPPED,
        "出貨中": HardwareDevice.STATUS_SHIPPED,
        "loaned": HardwareDevice.STATUS_LOANED,
        "借測中": HardwareDevice.STATUS_LOANED,
        "借測": HardwareDevice.STATUS_LOANED,
        "借出": HardwareDevice.STATUS_LOANED,
        "scrapped": HardwareDevice.STATUS_SCRAPPED,
        "已報廢": HardwareDevice.STATUS_SCRAPPED,
        "報廢": HardwareDevice.STATUS_SCRAPPED,
    }

    if request.method == "POST":
        form = HardwareImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["file"]
            try:
                raw_headers, data_rows = parse_uploaded_tabular_file(uploaded_file)
            except ValueError as e:
                error_list.append({"row": "-", "code": "-", "message": str(e)})
                raw_headers, data_rows = [], []

            if raw_headers and not error_list:
                col_map = {}
                custom_col_map = {}  # col_idx -> custom_field_key

                for idx, h in enumerate(raw_headers):
                    h_clean = h.strip()
                    h_lower = h_clean.lower()
                    if h_clean.startswith("自訂:") or h_clean.startswith("自訂："):
                        c_label = h_clean.split(":", 1)[-1].split("：", 1)[-1].strip()
                        c_key = custom_label_to_key.get(c_label.lower(), c_label)
                        custom_col_map[idx] = c_key
                    else:
                        mapped_key = header_alias_map.get(h_lower, header_alias_map.get(h_clean))
                        if mapped_key and mapped_key not in col_map:
                            col_map[mapped_key] = idx

                missing_cols = []
                if "code" not in col_map:
                    missing_cols.append("設備序號/資產編號")
                if "model_name" not in col_map and "model_code" not in col_map:
                    missing_cols.append("設備型號名稱")

                if missing_cols:
                    error_list.append({
                        "row": "表頭 (第 1 列)",
                        "code": "-",
                        "message": f"找不到必要欄位：{', '.join(missing_cols)}。請確認使用標準匯入範本或匯出格式。",
                    })
                elif not data_rows:
                    error_list.append({
                        "row": "-",
                        "code": "-",
                        "message": "檔案中沒有任何資料列可供匯入。",
                    })
                else:
                    seen_codes_in_file = {}
                    parsed_records = []

                    for row_num, row_vals in data_rows:
                        def get_val(field_key):
                            c_idx = col_map.get(field_key)
                            if c_idx is not None and c_idx < len(row_vals):
                                return row_vals[c_idx].strip()
                            return ""

                        code = get_val("code")
                        model_name_val = get_val("model_name")
                        model_code_val = get_val("model_code")
                        status_raw = get_val("status")
                        cust_code_val = get_val("customer_code")
                        cust_name_val = get_val("customer_name")
                        supplier_val = get_val("supplier_name")
                        acquired_raw = get_val("acquired_at")
                        return_raw = get_val("expected_return_at")
                        port_raw = get_val("port_count")
                        cpu_val = get_val("cpu")
                        memory_val = get_val("memory")
                        hdd_val = get_val("hdd")
                        note_val = get_val("note")

                        row_errors = []

                        # 1. 驗證設備序號
                        if not code:
                            row_errors.append("「設備序號/資產編號」為必填欄位，不可為空白")
                        else:
                            code_upper = code.upper()
                            if code_upper in seen_codes_in_file:
                                prev_row = seen_codes_in_file[code_upper]
                                row_errors.append(f"設備序號「{code}」在檔案中重複出現（與第 {prev_row} 列重複）")
                            else:
                                seen_codes_in_file[code_upper] = row_num

                        # 2. 驗證設備型號 (必須已存在於系統中)
                        hw_model = None
                        model_input = model_name_val or model_code_val
                        if not model_input:
                            row_errors.append("「設備型號名稱」為必填欄位，不可為空白")
                        else:
                            if model_name_val:
                                hw_model = model_by_name.get(model_name_val.upper()) or model_by_code.get(model_name_val.upper())
                            if not hw_model and model_code_val:
                                hw_model = model_by_code.get(model_code_val.upper()) or model_by_name.get(model_code_val.upper())
                            if not hw_model:
                                row_errors.append(
                                    f"系統中查無硬體規格型號「{model_input}」，請先至「硬體規格主檔」建立該型號後再行匯入"
                                )

                        # 3. 驗證進銷存狀態
                        status_val = HardwareDevice.STATUS_AVAILABLE
                        if status_raw:
                            mapped_st = status_map.get(status_raw.lower(), status_map.get(status_raw))
                            if not mapped_st:
                                row_errors.append(
                                    f"不支援的設備狀態「{status_raw}」（僅允許：庫存中、已出貨、借測中、已報廢）"
                                )
                            else:
                                status_val = mapped_st

                        # 4. 驗證關聯客戶
                        customer_obj = None
                        cust_input = cust_code_val or cust_name_val
                        if cust_code_val:
                            customer_obj = customer_by_code.get(cust_code_val.upper()) or customer_by_name.get(cust_code_val.upper())
                        if not customer_obj and cust_name_val:
                            customer_obj = customer_by_name.get(cust_name_val.upper()) or customer_by_code.get(cust_name_val.upper())

                        if status_val in (HardwareDevice.STATUS_SHIPPED, HardwareDevice.STATUS_LOANED):
                            if not cust_input:
                                st_label = "已出貨" if status_val == HardwareDevice.STATUS_SHIPPED else "借測中"
                                row_errors.append(f"狀態為「{st_label}」時，必須填寫「購買/關聯客戶代號」")
                            elif not customer_obj:
                                row_errors.append(
                                    f"系統中查無關聯客戶「{cust_input}」，請確認客戶代號/名稱是否正確，或先至「客戶名冊」匯入/新增該客戶"
                                )
                        else:
                            # 庫存中或報廢狀態下，不綁定客戶
                            customer_obj = None

                        # 5. 驗證供應商 (若有填寫，必須已存在於系統中)
                        supplier_obj = None
                        if supplier_val:
                            supplier_obj = supplier_lookup.get(supplier_val.upper())
                            if not supplier_obj:
                                row_errors.append(
                                    f"系統中查無進貨供應商「{supplier_val}」，請先至「供應商管理」建立該供應商，或留空以繼承型號預設供應商"
                                )

                        # 6. 驗證購入日與預計歸還日
                        acquired_date = None
                        if acquired_raw:
                            try:
                                acquired_date = _parse_date_str(acquired_raw)
                            except ValueError as e:
                                row_errors.append(f"購入/進貨日錯誤：{e}")

                        expected_return_date = None
                        if return_raw:
                            try:
                                expected_return_date = _parse_date_str(return_raw)
                            except ValueError as e:
                                row_errors.append(f"預計歸還日錯誤：{e}")

                        if status_val == HardwareDevice.STATUS_LOANED and not expected_return_date:
                            row_errors.append("狀態為「借測中」時，必須填寫「預計歸還日」")
                        if status_val != HardwareDevice.STATUS_LOANED:
                            expected_return_date = None

                        # 7. 驗證連接埠數覆寫
                        port_count_val = None
                        if port_raw != "":
                            try:
                                p_int = int(float(port_raw))
                                if p_int < 0:
                                    raise ValueError()
                                port_count_val = p_int
                            except ValueError:
                                row_errors.append(f"「網路連接埠數」必須為非負整數（目前填入：{port_raw}）")

                        # 8. 解析並檢核自訂擴充規格欄位
                        custom_overrides = {}
                        if hw_model and custom_col_map:
                            schema_dict = {item.get("key"): item for item in (hw_model.custom_field_schema or []) if item.get("key")}
                            for c_idx, c_key in custom_col_map.items():
                                raw_c_val = row_vals[c_idx].strip() if c_idx < len(row_vals) else ""
                                if raw_c_val == "":
                                    continue
                                schema_item = schema_dict.get(c_key)
                                if not schema_item:
                                    # 若該型號沒有定義此自訂欄位則忽略
                                    continue
                                f_type = schema_item.get("type", "text")
                                f_label = schema_item.get("label", c_key)
                                default_val = schema_item.get("default", "")

                                # 若填入值與型號預設值相同，則不需寫入單機覆寫 (保持繼承)
                                if str(raw_c_val) == str(default_val):
                                    continue

                                if f_type == "number":
                                    try:
                                        num_v = float(raw_c_val)
                                        custom_overrides[c_key] = int(num_v) if num_v.is_integer() else num_v
                                    except ValueError:
                                        row_errors.append(f"自訂欄位「{f_label}」必須為數值（目前填入：{raw_c_val}）")
                                elif f_type == "boolean":
                                    if raw_c_val.lower() in ("是", "true", "1", "yes", "y"):
                                        bool_v = "true"
                                    elif raw_c_val.lower() in ("否", "false", "0", "no", "n"):
                                        bool_v = "false"
                                    else:
                                        row_errors.append(f"自訂欄位「{f_label}」必須為「是」或「否」（目前填入：{raw_c_val}）")
                                        continue
                                    def_bool = "true" if default_val in (True, "true", "True", 1, "1", "是") else "false"
                                    if bool_v != def_bool:
                                        custom_overrides[c_key] = bool_v
                                elif f_type == "select":
                                    opts = schema_item.get("options", [])
                                    if opts and raw_c_val not in opts:
                                        row_errors.append(f"自訂欄位「{f_label}」的值「{raw_c_val}」不在允許選項內（允許值：{', '.join(opts)}）")
                                    else:
                                        custom_overrides[c_key] = raw_c_val
                                elif f_type == "multiselect":
                                    opts = schema_item.get("options", [])
                                    parts = [p.strip() for p in raw_c_val.split(",") if p.strip()]
                                    invalid_parts = [p for p in parts if opts and p not in opts]
                                    if invalid_parts:
                                        row_errors.append(f"自訂欄位「{f_label}」包含不允許的選項「{', '.join(invalid_parts)}」（允許值：{', '.join(opts)}）")
                                    else:
                                        custom_overrides[c_key] = parts
                                elif f_type == "datetime":
                                    from apps.customers.views import _parse_custom_datetime_str
                                    try:
                                        dt_formatted = _parse_custom_datetime_str(raw_c_val)
                                        def_dt = str(default_val or "").strip().replace("T", " ")[:16]
                                        if dt_formatted != def_dt:
                                            custom_overrides[c_key] = dt_formatted
                                    except ValueError as e:
                                        row_errors.append(f"自訂欄位「{f_label}」錯誤：{e}")
                                else:
                                    custom_overrides[c_key] = raw_c_val

                        if row_errors:
                            error_list.append({
                                "row": f"第 {row_num} 列",
                                "code": code or "(未填序號)",
                                "message": "；".join(row_errors),
                            })
                        else:
                            # 判斷基礎規格是否與型號預設值相同，若相同則設為 None 以保持繼承型號預設
                            eff_port = port_count_val
                            if eff_port is not None and hw_model and eff_port == hw_model.port_count:
                                eff_port = None
                            eff_cpu = cpu_val if (cpu_val and hw_model and cpu_val != (hw_model.cpu or "")) else None
                            eff_mem = memory_val if (memory_val and hw_model and memory_val != (hw_model.memory or "")) else None
                            eff_hdd = hdd_val if (hdd_val and hw_model and hdd_val != (hw_model.hdd or "")) else None

                            parsed_records.append({
                                "row_num": row_num,
                                "code": code,
                                "model": hw_model,
                                "status": status_val,
                                "customer": customer_obj,
                                "supplier": supplier_obj,
                                "acquired_at": acquired_date,
                                "expected_return_at": expected_return_date,
                                "port_count": eff_port,
                                "cpu": eff_cpu,
                                "memory": eff_mem,
                                "hdd": eff_hdd,
                                "note": note_val,
                                "custom_overrides": custom_overrides,
                            })

                    if not error_list and parsed_records:
                        created_count = 0
                        updated_count = 0
                        pending_approval_count_import = 0
                        requires_hw_approval = is_hardware_approval_required_for_user(request.user)
                        ip_addr = request.META.get("REMOTE_ADDR")

                        with transaction.atomic():
                            existing_devices = {d.code.upper(): d for d in HardwareDevice.objects.all()}

                            for rec in parsed_records:
                                code_key = rec["code"].upper()
                                dev = existing_devices.get(code_key)

                                if dev:
                                    old_st = dev.status
                                    old_cust_id = dev.customer_id
                                    target_st = rec["status"]
                                    target_cust = rec["customer"]

                                    # 更新既有設備 (保留未填寫之選填欄位原值)
                                    dev.is_deleted = False
                                    dev.model = rec["model"]

                                    if rec["supplier"]:
                                        dev.supplier = rec["supplier"]
                                    elif not dev.supplier and rec["model"].supplier:
                                        dev.supplier = rec["model"].supplier

                                    if rec["acquired_at"]:
                                        dev.acquired_at = rec["acquired_at"]
                                    if rec["port_count"] is not None:
                                        dev.port_count = rec["port_count"]
                                    if rec["cpu"] is not None:
                                        dev.cpu = rec["cpu"]
                                    if rec["memory"] is not None:
                                        dev.memory = rec["memory"]
                                    if rec["hdd"] is not None:
                                        dev.hdd = rec["hdd"]
                                    if rec["note"]:
                                        dev.note = rec["note"]

                                    if rec["custom_overrides"]:
                                        merged_custom = dict(dev.custom_values or {})
                                        merged_custom.update(rec["custom_overrides"])
                                        dev.custom_values = merged_custom

                                    is_new_outbound = target_st in (
                                        HardwareDevice.STATUS_SHIPPED,
                                        HardwareDevice.STATUS_LOANED,
                                    ) and (old_st != target_st or old_cust_id != (target_cust.id if target_cust else None))

                                    if requires_hw_approval and is_new_outbound:
                                        req_type = (
                                            HardwareOutboundApproval.REQUEST_LOAN
                                            if target_st == HardwareDevice.STATUS_LOANED
                                            else HardwareOutboundApproval.REQUEST_SHIP
                                        )
                                        create_hardware_outbound_approval(
                                            device=dev,
                                            customer=target_cust,
                                            request_type=req_type,
                                            applicant=request.user,
                                            expected_return_at=rec["expected_return_at"],
                                            note=rec["note"] or "由 Excel 批次匯入發起出庫申請",
                                            ip_address=ip_addr,
                                        )
                                        pending_approval_count_import += 1
                                    else:
                                        dev.status = target_st
                                        if target_st in (HardwareDevice.STATUS_AVAILABLE, HardwareDevice.STATUS_SCRAPPED):
                                            dev.customer = None
                                        else:
                                            dev.customer = target_cust
                                        if target_st == HardwareDevice.STATUS_LOANED:
                                            dev.expected_return_at = rec["expected_return_at"]
                                        else:
                                            dev.expected_return_at = None

                                        dev._created_by = request.user
                                        dev._transaction_note = "由 Excel 批次匯入更新"
                                        dev.save()
                                    updated_count += 1
                                else:
                                    # 新增設備：先以 AVAILABLE 建立以產生進貨紀錄，若目標狀態為出貨/借測/報廢再推進狀態
                                    init_status = rec["status"]
                                    dev = HardwareDevice(
                                        code=rec["code"],
                                        model=rec["model"],
                                        supplier=rec["supplier"] or rec["model"].supplier,
                                        status=HardwareDevice.STATUS_AVAILABLE,
                                        customer=None,
                                        acquired_at=rec["acquired_at"],
                                        expected_return_at=None,
                                        port_count=rec["port_count"],
                                        cpu=rec["cpu"],
                                        memory=rec["memory"],
                                        hdd=rec["hdd"],
                                        custom_values=rec["custom_overrides"],
                                        note=rec["note"],
                                    )
                                    dev._created_by = request.user
                                    dev._transaction_note = rec["note"] or "由 Excel 批次匯入建立進貨"
                                    dev.save()

                                    if init_status in (HardwareDevice.STATUS_SHIPPED, HardwareDevice.STATUS_LOANED) and requires_hw_approval:
                                        req_type = (
                                            HardwareOutboundApproval.REQUEST_LOAN
                                            if init_status == HardwareDevice.STATUS_LOANED
                                            else HardwareOutboundApproval.REQUEST_SHIP
                                        )
                                        create_hardware_outbound_approval(
                                            device=dev,
                                            customer=rec["customer"],
                                            request_type=req_type,
                                            applicant=request.user,
                                            expected_return_at=rec["expected_return_at"],
                                            note=rec["note"] or "由 Excel 批次匯入發起出庫申請",
                                            ip_address=ip_addr,
                                        )
                                        pending_approval_count_import += 1
                                    elif init_status != HardwareDevice.STATUS_AVAILABLE:
                                        dev.status = init_status
                                        dev.customer = rec["customer"]
                                        dev.expected_return_at = rec["expected_return_at"]
                                        dev._created_by = request.user
                                        dev._transaction_note = rec["note"] or "由 Excel 批次匯入自動設定狀態"
                                        dev.save()

                                    existing_devices[code_key] = dev
                                    created_count += 1

                            log_action(
                                user=request.user,
                                action="批次匯入硬體設備",
                                target_type="HardwareDevice",
                                target_identifier=uploaded_file.name,
                                detail=f"新增 {created_count} 台，更新 {updated_count} 台，轉入出庫待審 {pending_approval_count_import} 台",
                                ip_address=ip_addr,
                            )

                        if pending_approval_count_import > 0:
                            messages.warning(
                                request,
                                f"硬體設備匯入完成！共處理 {len(parsed_records)} 台（新增 {created_count} 台、更新 {updated_count} 台），其中 {pending_approval_count_import} 台出庫/借測已預先鎖定為「待覆核」狀態，待主管核准後正式出庫。",
                            )
                        else:
                            messages.success(
                                request,
                                f"硬體設備匯入成功！共處理 {len(parsed_records)} 台設備（新增 {created_count} 台、更新 {updated_count} 台）。",
                            )
                        return redirect("hardware-list")
    else:
        form = HardwareImportForm()

    return render(
        request,
        "hardware/device_import.html",
        {
            "form": form,
            "error_list": error_list,
            "models": all_models,
            "suppliers": all_suppliers,
            "title": "匯入硬體設備庫存",
        },
    )



@login_required
def device_detail(request, pk):
    device = get_object_or_404(
        HardwareDevice.objects.select_related("model__supplier", "supplier", "customer"),
        pk=pk,
    )
    transactions = device.transactions.select_related("customer", "created_by").order_by("-occurred_at")
    approval_history = device.outbound_approvals.select_related(
        "customer", "requested_by", "reviewed_by", "contract", "old_probe"
    ).order_by("-requested_at")

    return render(
        request,
        "hardware/device_detail.html",
        {
            "device": device,
            "probe": device,
            "transactions": transactions,
            "approval_history": approval_history,
            "pending_approval": device.pending_approval_request,
            "can_edit_hardware": user_can_edit_hardware(request.user),
            "can_edit_probe": user_can_edit_hardware(request.user),
            "can_approve_hardware": user_can_approve_hardware(request.user),
        },
    )


@login_required
def device_create(request):
    if not user_can_edit_hardware(request.user):
        messages.error(request, "權限不足：您沒有新增硬體設備的權限。")
        return redirect("hardware-list")

    next_url = request.GET.get("next") or request.POST.get("next")
    customer_id = request.GET.get("customer")
    requires_approval = is_hardware_approval_required_for_user(request.user)

    if request.method == "POST":
        form = HardwareDeviceForm(request.POST)
        if form.is_valid():
            device = form.save(commit=False)
            target_status = device.status
            target_customer = device.customer
            target_expected_return = device.expected_return_at
            ip_addr = request.META.get("REMOTE_ADDR")

            if requires_approval and target_status in (
                HardwareDevice.STATUS_SHIPPED,
                HardwareDevice.STATUS_LOANED,
            ):
                # 先建立進貨入庫，再進入出庫預鎖待覆核
                device.status = HardwareDevice.STATUS_AVAILABLE
                device.customer = None
                device.expected_return_at = None
                device._created_by = request.user
                device.save()

                req_type = (
                    HardwareOutboundApproval.REQUEST_LOAN
                    if target_status == HardwareDevice.STATUS_LOANED
                    else HardwareOutboundApproval.REQUEST_SHIP
                )
                create_hardware_outbound_approval(
                    device=device,
                    customer=target_customer,
                    request_type=req_type,
                    applicant=request.user,
                    expected_return_at=target_expected_return,
                    note=device.note or "新增設備同時發起出庫申請",
                    ip_address=ip_addr,
                )
                messages.warning(
                    request,
                    f"已建立設備「{device.code}」入庫，並送出出庫簽核申請！該設備已預先鎖定為「{device.get_status_display()}」，待主管覆核通過後即正式出庫。",
                )
            else:
                device._created_by = request.user
                device.save()
                log_action(
                    user=request.user,
                    action="新增硬體設備",
                    target_type="HardwareDevice",
                    target_identifier=device.code,
                    ip_address=ip_addr,
                )
                if device.customer:
                    notify_customer_followers(
                        customer=device.customer,
                        actor=request.user,
                        event_category="data_change",
                        event_title=f"新增並配發硬體設備：{device.code}",
                        event_detail=f"設備序號：{device.code}\n型號：{device.model.name}\n狀態：{device.get_status_display()}",
                    )
                messages.success(request, f"成功建立設備「{device.code}」")

            if next_url:
                return redirect(next_url)
            return redirect("hardware-detail", pk=device.pk)
    else:
        initial = {}
        if customer_id:
            try:
                cust = Customer.objects.get(pk=customer_id)
                initial["customer"] = cust
                initial["status"] = HardwareDevice.STATUS_SHIPPED
            except (Customer.DoesNotExist, ValueError):
                pass
        form = HardwareDeviceForm(initial=initial)

    return render(
        request,
        "hardware/device_form.html",
        {
            "form": form,
            "title": "新增硬體設備",
            "next": next_url,
            "requires_approval": requires_approval,
        },
    )


@login_required
def device_update(request, pk):
    if not user_can_edit_hardware(request.user):
        messages.error(request, "權限不足：您沒有編輯硬體設備的權限。")
        return redirect("hardware-detail", pk=pk)

    device = get_object_or_404(HardwareDevice, pk=pk)
    old_status = device.status
    old_customer = device.customer
    requires_approval = is_hardware_approval_required_for_user(request.user)

    if request.method == "POST":
        form = HardwareDeviceForm(request.POST, instance=device)
        if form.is_valid():
            d = form.save(commit=False)
            target_status = d.status
            target_customer = d.customer
            target_expected_return = d.expected_return_at
            ip_addr = request.META.get("REMOTE_ADDR")

            is_outbound_change = target_status in (
                HardwareDevice.STATUS_SHIPPED,
                HardwareDevice.STATUS_LOANED,
            ) and (old_status != target_status or old_customer != target_customer)

            if requires_approval and is_outbound_change:
                # 先保留原狀態儲存規格變更，再由 approval_service 鎖為待審
                d.status = old_status
                d.customer = old_customer
                d._skip_auto_txn = True
                d.save()

                req_type = (
                    HardwareOutboundApproval.REQUEST_LOAN
                    if target_status == HardwareDevice.STATUS_LOANED
                    else HardwareOutboundApproval.REQUEST_SHIP
                )
                create_hardware_outbound_approval(
                    device=d,
                    customer=target_customer,
                    request_type=req_type,
                    applicant=request.user,
                    expected_return_at=target_expected_return,
                    note=d.note or "於設備編輯頁發起出庫申請",
                    ip_address=ip_addr,
                )
                messages.warning(
                    request,
                    f"已送出設備「{d.code}」出庫簽核申請！目前預先鎖定為「{d.get_status_display()}」，待主管覆核通過後正式生效。",
                )
                return redirect("hardware-detail", pk=device.pk)

            # 若原本是待審狀態而被手動改回庫存中，自動將待審單標記取消
            if old_status in (
                HardwareDevice.STATUS_PENDING_SHIP,
                HardwareDevice.STATUS_PENDING_LOAN,
            ) and target_status == HardwareDevice.STATUS_AVAILABLE:
                HardwareOutboundApproval.objects.filter(
                    device=d, approval_status=HardwareOutboundApproval.APPROVAL_PENDING
                ).update(
                    approval_status=HardwareOutboundApproval.APPROVAL_REJECTED,
                    rejection_reason="開單人員取消出庫改回庫存中",
                    reviewed_by=request.user,
                    reviewed_at=timezone.now(),
                )

            d._created_by = request.user
            d.save()
            log_action(
                user=request.user,
                action="修改硬體設備",
                target_type="HardwareDevice",
                target_identifier=device.code,
                ip_address=ip_addr,
            )
            if d.customer and d.status in (HardwareDevice.STATUS_SHIPPED, HardwareDevice.STATUS_LOANED):
                notify_customer_followers(
                    customer=d.customer,
                    actor=request.user,
                    event_category="data_change",
                    event_title=f"更新硬體設備：{d.code}",
                    event_detail=f"設備序號：{d.code}\n型號：{d.model.name}\n狀態：{d.get_status_display()}\n備註：{d.note or '-'}",
                )
            elif old_customer and old_customer != d.customer:
                notify_customer_followers(
                    customer=old_customer,
                    actor=request.user,
                    event_category="data_change",
                    event_title=f"硬體設備解除關聯/異動：{d.code}",
                    event_detail=f"設備序號：{d.code} 已變更狀態為「{d.get_status_display()}」",
                )
            messages.success(request, f"設備「{device.code}」已更新。")
            return redirect("hardware-detail", pk=device.pk)
    else:
        form = HardwareDeviceForm(instance=device)

    return render(
        request,
        "hardware/device_form.html",
        {
            "form": form,
            "device": device,
            "probe": device,
            "title": f"編輯設備 - {device.code}",
            "requires_approval": requires_approval,
        },
    )


@login_required
def device_delete(request, pk):
    if not user_can_edit_hardware(request.user):
        messages.error(request, "權限不足。")
        return redirect("hardware-list")

    device = get_object_or_404(HardwareDevice, pk=pk)
    if request.method == "POST":
        code = device.code
        device.is_deleted = True
        device.save()
        log_action(
            user=request.user,
            action="軟刪除硬體設備",
            target_type="HardwareDevice",
            target_identifier=code,
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"已標記刪除設備「{code}」")
        return redirect("hardware-list")

    return render(
        request,
        "hardware/device_confirm_delete.html",
        {"device": device, "probe": device},
    )


# Hardware Models
@login_required
def model_list(request):
    models = HardwareModel.objects.select_related("supplier").all()
    can_edit_hw_models = user_can_edit_hardware_models(request.user)
    return render(
        request,
        "hardware/model_list.html",
        {
            "models": models,
            "can_edit_hardware": can_edit_hw_models,
            "can_edit_probe": can_edit_hw_models,
            "can_edit_hardware_models": can_edit_hw_models,
        },
    )


@login_required
def model_create(request):
    if not user_can_edit_hardware_models(request.user):
        messages.error(request, "權限不足：您沒有新增規格型號的權限。")
        return redirect("hardware-models")

    if request.method == "POST":
        form = HardwareModelForm(request.POST)
        if form.is_valid():
            model = form.save()
            log_action(
                user=request.user,
                action="新增規格型號",
                target_type="HardwareModel",
                target_identifier=model.name,
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"成功新增型號「{model.name}」")
            return redirect("hardware-models")
    else:
        form = HardwareModelForm()

    return render(request, "hardware/model_form.html", {"form": form, "title": "新增規格型號"})


@login_required
def model_update(request, pk):
    if not user_can_edit_hardware_models(request.user):
        messages.error(request, "權限不足：您沒有編輯規格型號的權限。")
        return redirect("hardware-models")

    model = get_object_or_404(HardwareModel, pk=pk)
    if request.method == "POST":
        form = HardwareModelForm(request.POST, instance=model)
        if form.is_valid():
            form.save()
            log_action(
                user=request.user,
                action="修改規格型號",
                target_type="HardwareModel",
                target_identifier=model.name,
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"規格型號「{model.name}」已更新。")
            return redirect("hardware-models")
    else:
        form = HardwareModelForm(instance=model)

    return render(
        request,
        "hardware/model_form.html",
        {"form": form, "model": model, "title": f"編輯型號 - {model.name}"},
    )


@login_required
def model_delete(request, pk):
    if not user_can_edit_hardware_models(request.user):
        messages.error(request, "權限不足：您沒有刪除規格型號的權限。")
        return redirect("hardware-models")

    model = get_object_or_404(HardwareModel, pk=pk)
    devices_count = model.devices.filter(is_deleted=False).count()

    if request.method == "POST":
        if devices_count > 0:
            messages.error(request, f"無法刪除規格型號「{model.name}」，因為仍有 {devices_count} 台硬體設備關聯至此型號！")
            return redirect("hardware-models")

        name = model.name
        code = model.model_code
        model.delete()
        log_action(
            user=request.user,
            action="刪除規格型號",
            target_type="HardwareModel",
            target_identifier=name,
            detail=f"刪除型號代碼: {code}",
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"已成功刪除規格型號「{name}」！")
        return redirect("hardware-models")

    return render(
        request,
        "hardware/model_confirm_delete.html",
        {
            "model": model,
            "devices_count": devices_count,
            "has_relations": devices_count > 0,
        },
    )


# Suppliers
@login_required
def supplier_list(request):
    suppliers = Supplier.objects.all()
    return render(
        request,
        "hardware/supplier_list.html",
        {
            "suppliers": suppliers,
            "can_edit_hardware": user_can_edit_hardware(request.user),
            "can_edit_probe": user_can_edit_hardware(request.user),
        },
    )


@login_required
def supplier_create(request):
    if not user_can_edit_hardware(request.user):
        messages.error(request, "權限不足。")
        return redirect("hardware-suppliers")

    if request.method == "POST":
        form = SupplierForm(request.POST)
        if form.is_valid():
            supplier = form.save()
            log_action(
                user=request.user,
                action="新增供應商",
                target_type="Supplier",
                target_identifier=supplier.name,
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"成功新增供應商「{supplier.name}」")
            return redirect("hardware-suppliers")
    else:
        form = SupplierForm()

    return render(request, "hardware/supplier_form.html", {"form": form, "title": "新增供應商"})


@login_required
def supplier_update(request, pk):
    if not user_can_edit_hardware(request.user):
        messages.error(request, "權限不足。")
        return redirect("hardware-suppliers")

    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            log_action(
                user=request.user,
                action="修改供應商",
                target_type="Supplier",
                target_identifier=supplier.name,
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"供應商「{supplier.name}」已更新。")
            return redirect("hardware-suppliers")
    else:
        form = SupplierForm(instance=supplier)

    return render(
        request,
        "hardware/supplier_form.html",
        {"form": form, "supplier": supplier, "title": f"編輯供應商 - {supplier.name}"},
    )


@login_required
def supplier_delete(request, pk):
    if not user_can_edit_hardware(request.user):
        messages.error(request, "權限不足：您沒有刪除供應商的權限。")
        return redirect("hardware-suppliers")

    supplier = get_object_or_404(Supplier, pk=pk)

    models_count = supplier.models.count()
    devices_count = supplier.devices.filter(is_deleted=False).count()

    if request.method == "POST":
        if models_count > 0:
            messages.error(request, f"無法刪除供應商「{supplier.name}」，因為仍有 {models_count} 個硬體規格主檔關聯至此供應商！")
            return redirect("hardware-suppliers")
        if devices_count > 0:
            messages.error(request, f"無法刪除供應商「{supplier.name}」，因為仍有 {devices_count} 台硬體設備關聯至此供應商！")
            return redirect("hardware-suppliers")

        name = supplier.name
        code = supplier.supplier_code
        supplier.delete()
        log_action(
            user=request.user,
            action="刪除供應商",
            target_type="Supplier",
            target_identifier=name,
            detail=f"刪除供應商代號: {code}",
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"已成功刪除供應商「{name}」！")
        return redirect("hardware-suppliers")

    return render(
        request,
        "hardware/supplier_confirm_delete.html",
        {
            "supplier": supplier,
            "models_count": models_count,
            "devices_count": devices_count,
            "has_relations": (models_count > 0 or devices_count > 0),
        },
    )


# Transactions
@login_required
def transaction_list(request):
    type_filter = request.GET.get("type", "").strip()
    query = request.GET.get("q", "").strip()

    transactions = InventoryTransaction.objects.select_related(
        "device", "device__model", "customer", "created_by"
    ).all()

    if type_filter:
        transactions = transactions.filter(type=type_filter)

    if query:
        transactions = transactions.filter(
            Q(device__code__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__code__icontains=query)
            | Q(note__icontains=query)
        )

    paginator = Paginator(transactions, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "hardware/transaction_list.html",
        {
            "page_obj": page_obj,
            "type_filter": type_filter,
            "query": query,
            "can_edit_hardware": user_can_edit_hardware(request.user),
            "can_edit_probe": user_can_edit_hardware(request.user),
            "total_count": paginator.count,
        },
    )


@login_required
def transaction_create(request):
    if not user_can_edit_hardware(request.user):
        messages.error(request, "權限不足：您沒有執行庫存交易的權限。")
        return redirect("hardware-transactions")

    next_url = request.GET.get("next") or request.POST.get("next")
    customer_id = request.GET.get("customer")
    type_param = request.GET.get("type")

    if request.method == "POST":
        form = TransactionForm(request.POST)
        if form.is_valid():
            ip_addr = request.META.get("REMOTE_ADDR")
            txn = form.save(user=request.user, ip_address=ip_addr)
            if getattr(txn, "_is_pending_approval", False):
                messages.warning(
                    request,
                    f"已送出設備「{txn.device.code}」之「{txn.get_request_type_display()}」簽核申請！該設備已預先鎖定為待審狀態，待主管覆核通過後即正式出庫。",
                )
                if next_url:
                    return redirect(next_url)
                return redirect("hardware-detail", pk=txn.device.pk)

            log_action(
                user=request.user,
                action=f"庫存交易: {txn.get_type_display()}",
                target_type="InventoryTransaction",
                target_identifier=f"{txn.device.code} ({txn.customer.name if txn.customer else '無客戶'})",
                detail=txn.note,
                ip_address=ip_addr,
            )
            if txn.customer:
                notify_customer_followers(
                    customer=txn.customer,
                    actor=request.user,
                    event_category="data_change",
                    event_title=f"設備庫存異動 ({txn.get_type_display()})：{txn.device.code}",
                    event_detail=f"設備序號：{txn.device.code}\n型號：{txn.device.model.name}\n交易類型：{txn.get_type_display()}\n說明：{txn.note or '-'}",
                )
            messages.success(request, f"成功建立「{txn.get_type_display()}」交易紀錄！")
            if next_url:
                return redirect(next_url)
            return redirect("hardware-transactions")
    else:
        initial = {}
        if customer_id:
            try:
                cust = Customer.objects.get(pk=customer_id)
                initial["customer"] = cust
            except (Customer.DoesNotExist, ValueError):
                pass
        if type_param:
            initial["type"] = type_param
        form = TransactionForm(initial=initial)

    return render(
        request,
        "hardware/transaction_form.html",
        {
            "form": form,
            "title": "新增庫存異動交易",
            "next": next_url,
        },
    )


@login_required
def device_return(request, pk):
    """設備退貨/歸還入庫快捷處理"""
    if not user_can_edit_hardware(request.user):
        messages.error(request, "權限不足：您沒有執行硬體設備退貨/歸還的權限。")
        return redirect("hardware-list")

    device = get_object_or_404(HardwareDevice, pk=pk)
    if device.status not in [HardwareDevice.STATUS_SHIPPED, HardwareDevice.STATUS_LOANED]:
        messages.error(request, f"設備「{device.code}」目前狀態為「{device.get_status_display()}」，無法辦理退貨/歸還。")
        return redirect("hardware-detail", pk=device.pk)

    customer = device.customer
    customer_name = customer.name if customer else "未知客戶"
    old_status = device.status
    next_url = request.GET.get("next") or request.POST.get("next")
    if not next_url and customer:
        next_url = reverse("customer-detail", kwargs={"pk": customer.pk})

    if request.method == "POST":
        note = request.POST.get("note", "").strip()

        is_shipped = (old_status == HardwareDevice.STATUS_SHIPPED)
        action_name = "出貨退貨" if is_shipped else "借測歸還"
        default_note = f"客戶 {customer_name} 辦理{action_name}入庫"
        final_note = note or default_note

        device.status = HardwareDevice.STATUS_AVAILABLE
        device.customer = None
        device.expected_return_at = None
        device._created_by = request.user
        device._transaction_note = final_note
        device.save()

        log_action(
            user=request.user,
            action=f"設備{action_name}",
            target_type="HardwareDevice",
            target_identifier=device.code,
            detail=f"原客戶: {customer_name} ({customer.code if customer else ''})，備註: {final_note}",
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        if customer:
            notify_customer_followers(
                customer=customer,
                actor=request.user,
                event_category="data_change",
                event_title=f"設備{action_name}入庫：{device.code}",
                event_detail=f"設備序號：{device.code}\n型號：{device.model.name}\n動作：{action_name}\n備註：{final_note}",
            )
        messages.success(request, f"設備「{device.code}」已成功辦理{action_name}入庫！")
        if next_url:
            return redirect(next_url)
        return redirect("hardware-detail", pk=device.pk)

    return render(
        request,
        "hardware/device_confirm_return.html",
        {
            "device": device,
            "customer": customer,
            "old_status": old_status,
            "next": next_url,
        },
    )


@login_required
def api_customer_devices(request, customer_id):
    """AJAX helper: 取得某客戶目前出貨或借測中的硬體設備清單"""
    status_type = request.GET.get("status", "")
    qs = HardwareDevice.objects.filter(customer_id=customer_id, is_deleted=False)
    if status_type:
        qs = qs.filter(status=status_type)

    data = [{"id": p.id, "code": p.code, "model": p.model.name if p.model else ""} for p in qs]
    return JsonResponse({"devices": data, "probes": data})


@login_required
def api_hardware_model_meta(request, pk):
    """AJAX helper: 取得硬體型號之標準規格與自訂欄位 Schema"""
    hw_model = get_object_or_404(HardwareModel.objects.select_related("supplier"), pk=pk)
    return JsonResponse(
        {
            "id": hw_model.id,
            "model_code": hw_model.model_code,
            "name": hw_model.name,
            "port_count": hw_model.port_count,
            "cpu": hw_model.cpu,
            "memory": hw_model.memory,
            "hdd": hw_model.hdd,
            "supplier_id": hw_model.supplier_id,
            "supplier_name": hw_model.supplier.name if hw_model.supplier else "",
            "custom_field_schema": hw_model.custom_field_schema or [],
        }
    )


@login_required
@require_POST
def hardware_approve(request, pk):
    """核准特定硬體設備目前的待審出庫簽核單 (pk 為 HardwareDevice ID)"""
    if not user_can_approve_hardware(request.user):
        messages.error(request, "權限不足：您沒有覆核硬體設備出庫的權限。")
        return redirect("hardware-detail", pk=pk)

    device = get_object_or_404(HardwareDevice, pk=pk)
    req_obj = (
        HardwareOutboundApproval.objects.filter(
            device=device, approval_status=HardwareOutboundApproval.APPROVAL_PENDING
        )
        .select_related("device", "device__model", "customer", "contract", "old_probe", "requested_by")
        .order_by("-requested_at")
        .first()
    )
    next_url = request.POST.get("next") or request.GET.get("next")

    if not req_obj:
        # 若無對應簽核單但設備卡在 pending_ship / pending_loan，安全復原為正式出庫
        if device.status in (HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN):
            is_loan = device.status == HardwareDevice.STATUS_PENDING_LOAN
            device.status = HardwareDevice.STATUS_LOANED if is_loan else HardwareDevice.STATUS_SHIPPED
            device._created_by = request.user
            device.save()
            messages.success(request, f"已核准設備「{device.code}」正式出庫！")
        else:
            messages.info(request, f"設備「{device.code}」目前沒有待覆核的出庫申請。")
        return redirect(next_url or reverse("hardware-detail", kwargs={"pk": pk}))

    ok, msg = approve_hardware_outbound_request(
        req_obj, request.user, ip_address=request.META.get("REMOTE_ADDR")
    )
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect(next_url or reverse("hardware-detail", kwargs={"pk": pk}))


@login_required
@require_POST
def hardware_batch_approve(request):
    if not user_can_approve_hardware(request.user):
        messages.error(request, "權限不足：您沒有覆核硬體設備出庫的權限。")
        return redirect("hardware-list")

    raw_ids = request.POST.getlist("selected_ids")
    valid_ids = [int(x) for x in raw_ids if str(x).isdigit()]
    next_url = request.POST.get("next") or "hardware-list"
    if not valid_ids:
        messages.warning(request, "請先勾選至少一台出庫待審的硬體設備。")
        return redirect(next_url)

    pending_reqs = list(
        HardwareOutboundApproval.objects.filter(
            device_id__in=valid_ids,
            approval_status=HardwareOutboundApproval.APPROVAL_PENDING,
        ).select_related("device", "device__model", "customer", "contract", "old_probe", "requested_by")
    )

    approved_cnt = 0
    skipped_self = 0
    ip_addr = request.META.get("REMOTE_ADDR")
    for req_obj in pending_reqs:
        if req_obj.requested_by_id and req_obj.requested_by_id == request.user.id and not request.user.is_superuser:
            skipped_self += 1
            continue
        ok, _ = approve_hardware_outbound_request(req_obj, request.user, ip_address=ip_addr)
        if ok:
            approved_cnt += 1

    if approved_cnt > 0:
        messages.success(request, f"已批次覆核放行 {approved_cnt} 台硬體設備出庫！")
    if skipped_self > 0:
        messages.warning(request, f"略過 {skipped_self} 筆由您本人送審的出庫申請（需由其他覆核主管審核）。")
    if approved_cnt == 0 and skipped_self == 0:
        messages.info(request, "所選設備中沒有可核准之待審出庫單。")

    return redirect(next_url)


@login_required
@require_POST
def hardware_reject(request, pk):
    """退回特定硬體設備目前的待審出庫簽核單並自動解鎖回庫存中"""
    if not user_can_approve_hardware(request.user):
        messages.error(request, "權限不足：您沒有退回硬體出庫申請的權限。")
        return redirect("hardware-detail", pk=pk)

    device = get_object_or_404(HardwareDevice, pk=pk)
    req_obj = (
        HardwareOutboundApproval.objects.filter(
            device=device, approval_status=HardwareOutboundApproval.APPROVAL_PENDING
        )
        .select_related("device", "device__model", "customer", "requested_by")
        .order_by("-requested_at")
        .first()
    )
    reason = (request.POST.get("rejection_reason") or "").strip()
    next_url = request.POST.get("next") or request.GET.get("next")

    if not reason:
        messages.error(request, "退回出庫申請時必須填寫「退回原因說明」。")
        return redirect(next_url or reverse("hardware-detail", kwargs={"pk": pk}))

    if req_obj:
        ok, msg = reject_hardware_outbound_request(
            req_obj, request.user, reason, ip_address=request.META.get("REMOTE_ADDR")
        )
        if ok:
            messages.warning(request, msg)
        else:
            messages.error(request, msg)
    elif device.status in (HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN):
        device.status = HardwareDevice.STATUS_AVAILABLE
        device.customer = None
        device.expected_return_at = None
        device._skip_auto_txn = True
        device.save()
        messages.warning(request, f"已退回並解鎖設備「{device.code}」回庫存中。")

    return redirect(next_url or reverse("hardware-detail", kwargs={"pk": pk}))


# Backward compatibility aliases
probe_dashboard = hardware_dashboard
probe_list = device_list
probe_detail = device_detail
probe_create = device_create
probe_update = device_update
probe_delete = device_delete
probe_model_list = model_list
probe_model_create = model_create
probe_model_update = model_update
probe_model_delete = model_delete
probe_supplier_delete = supplier_delete
api_customer_probes = api_customer_devices
