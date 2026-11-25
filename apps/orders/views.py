import datetime
import json
import mimetypes
import os
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.core.models import SystemSetting
from apps.core.permissions import (
    filter_by_customer_scope,
    log_action,
    user_can_access_customer,
    user_can_approve_orders,
    user_can_close_orders,
    user_can_edit_orders,
    user_can_manage_system,
    user_can_ship_orders,
    user_can_view_crm,
)
from apps.customers.models import Customer, CustomerFollow, MaintenanceContract
from apps.hardware.models import HardwareDevice, HardwareModel
from apps.licenses.models import CustomerLicense, Product
from .forms import OrderAttachmentForm, OrderForm, OrderPaymentForm, QuotationSettingForm
from .models import Order, OrderAttachment, OrderItem, OrderPayment, OrderVersion
from .services import (
    approve_order,
    build_order_snapshot_dict,
    cancel_order,
    close_order_manually,
    confirm_order_payment,
    confirm_order_shipment,
    create_order_version_snapshot,
    get_warranty_target_labels,
    reject_order,
    reject_order_payment,
    report_order_payment,
    submit_order_for_approval,
)

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
ALLOWED_ASSET_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
MAX_ASSET_SIZE = 10 * 1024 * 1024  # 10 MB


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


def _number_to_zh_currency(amount: int) -> str:
    """將新台幣整數金額轉為正式報價單繁體中文大寫金額（例：12345 -> 壹萬貳仟參佰肆拾伍元整）"""
    try:
        n = int(amount or 0)
    except (TypeError, ValueError):
        return "零元整"
    if n <= 0:
        return "零元整"

    digits = "零壹貳參肆伍陸柒捌玖"
    units = ["", "拾", "佰", "仟"]
    big_units = ["", "萬", "億", "兆"]

    s = str(n)
    groups = []
    while s:
        groups.append(s[-4:])
        s = s[:-4]

    parts = []
    for g_idx in range(len(groups) - 1, -1, -1):
        grp = groups[g_idx].zfill(4)
        grp_str = ""
        zero_flag = False
        for i, ch in enumerate(grp):
            d = int(ch)
            pos = 3 - i
            if d == 0:
                zero_flag = True
            else:
                if zero_flag and grp_str:
                    grp_str += "零"
                elif zero_flag and parts and not grp_str:
                    parts.append("零")
                grp_str += digits[d] + units[pos]
                zero_flag = False
        if grp_str:
            parts.append(grp_str + big_units[g_idx])

    result = "".join(parts)
    while "零零" in result:
        result = result.replace("零零", "零")
    return f"{result}元整"


def _build_warranty_item_name(target_labels: list[str], warranty_days: int | None) -> str:
    """依據維護保固涵蓋標的與保固天數自動組合品項名稱"""
    days_suffix = f" ({warranty_days}天)" if warranty_days and warranty_days > 0 else ""
    if not target_labels:
        return f"維護保固服務合約{days_suffix}"[:150]
    if len(target_labels) == 1:
        summary = target_labels[0]
    elif len(target_labels) == 2:
        summary = f"{target_labels[0]}、{target_labels[1]}"
    else:
        summary = f"{target_labels[0]}、{target_labels[1]} 等 {len(target_labels)} 項"
    return f"維護保固 - {summary}{days_suffix}"[:150]


def _parse_csv_tokens(raw_val: str) -> list[str]:
    if not raw_val:
        return []
    tokens = []
    for part in str(raw_val).split(","):
        tok = part.strip()
        if tok and tok not in tokens:
            tokens.append(tok)
    return tokens


def _parse_order_items_from_post(request, customer_id=None) -> tuple[list[dict], list[str]]:
    """
    從動態明細表單 POST 陣列解析訂單明細列並驗證格式。
    規則：
    - 僅「專業服務 / 其他 (service)」可自由編輯品項名稱與單位；其餘三類由系統依主檔/標的強制帶入。
    - 維護保固 (warranty) 必選至少一個標的（本單新購軟硬體明細或該客戶已購軟體授權/已出貨硬體），
      並填寫正整數保固天數 (warranty_days)，到期日於主管審核通過當日起算。
    回傳 (parsed_items, errors)
    """
    item_types = request.POST.getlist("item_type")
    row_uids = request.POST.getlist("item_row_uid")
    names = request.POST.getlist("item_name")
    descriptions = request.POST.getlist("item_description")
    product_ids = request.POST.getlist("item_product")
    license_types = request.POST.getlist("item_license_type")
    hw_model_ids = request.POST.getlist("item_hardware_model")
    start_dates = request.POST.getlist("item_start_date")
    end_dates = request.POST.getlist("item_end_date")
    warranty_days_list = request.POST.getlist("item_warranty_days")
    warranty_order_targets_list = request.POST.getlist("item_warranty_order_targets")
    warranty_existing_lic_list = request.POST.getlist("item_warranty_existing_licenses")
    warranty_existing_dev_list = request.POST.getlist("item_warranty_existing_devices")
    sla_levels = request.POST.getlist("item_sla_level")
    quantities = request.POST.getlist("item_quantity")
    units = request.POST.getlist("item_unit")
    unit_prices = request.POST.getlist("item_unit_price")

    def _get(arr, idx, default=""):
        return (arr[idx] if idx < len(arr) else default).strip()

    def _parse_date(val_str):
        if not val_str:
            return None
        try:
            return datetime.date.fromisoformat(val_str)
        except ValueError:
            return "INVALID"

    parsed = []
    errors = []
    row_count = max(len(item_types), len(names), len(product_ids), len(hw_model_ids), 0)

    products_map = {str(p.pk): p for p in Product.objects.all()}
    models_map = {str(m.pk): m for m in HardwareModel.objects.all()}

    valid_customer_id = int(customer_id) if str(customer_id or "").isdigit() else None
    cust_licenses_map: dict[str, CustomerLicense] = {}
    cust_devices_map: dict[str, HardwareDevice] = {}
    if valid_customer_id:
        cust_licenses_map = {
            str(lic.pk): lic
            for lic in CustomerLicense.objects.filter(
                customer_id=valid_customer_id,
                approval_status=CustomerLicense.APPROVAL_APPROVED,
                is_disabled=False,
            ).select_related("product")
        }
        cust_devices_map = {
            str(dev.pk): dev
            for dev in HardwareDevice.objects.filter(
                customer_id=valid_customer_id,
                status=HardwareDevice.STATUS_SHIPPED,
                is_deleted=False,
            ).select_related("model")
        }

    # Pass 1: 解析所有列的基本欄位與主檔綁定
    uid_to_row: dict[str, dict] = {}
    for i in range(row_count):
        i_type = _get(item_types, i, OrderItem.TYPE_LICENSE)
        r_uid = _get(row_uids, i) or f"row_{i + 1}"
        i_name = _get(names, i)
        i_desc = _get(descriptions, i)
        p_id = _get(product_ids, i)
        lic_type = _get(license_types, i, "perpetual")
        m_id = _get(hw_model_ids, i)
        s_raw = _get(start_dates, i)
        e_raw = _get(end_dates, i)
        w_days_raw = _get(warranty_days_list, i)
        w_order_targets_raw = _get(warranty_order_targets_list, i)
        w_exist_lics_raw = _get(warranty_existing_lic_list, i)
        w_exist_devs_raw = _get(warranty_existing_dev_list, i)
        sla = _get(sla_levels, i)
        qty_raw = _get(quantities, i, "1")
        unit = _get(units, i, "式")
        price_raw = _get(unit_prices, i, "0")

        # 若非維護保固且整列為空白（無名稱、無選產品、無選型號），跳過
        if i_type != OrderItem.TYPE_WARRANTY and not i_name and not p_id and not m_id:
            continue

        sort_idx = len(parsed) + 1
        row_label = f"第 {sort_idx} 列明細"

        product_obj = products_map.get(p_id) if p_id else None
        hw_model_obj = models_map.get(m_id) if m_id else None

        if i_type == OrderItem.TYPE_LICENSE:
            if not product_obj:
                errors.append(f"{row_label}為「軟體授權」，請選擇對應的軟體產品主檔。")
            # 強制鎖定名稱與單位為軟體產品主檔值
            i_name = product_obj.name if product_obj else i_name
            unit = (product_obj.default_unit or "台") if product_obj else (unit or "台")
        elif i_type == OrderItem.TYPE_HARDWARE:
            if not hw_model_obj:
                errors.append(f"{row_label}為「硬體設備」，請選擇對應的硬體型號規格主檔。")
            # 強制鎖定名稱與單位為硬體型號主檔值
            i_name = hw_model_obj.name if hw_model_obj else i_name
            unit = "台"
        elif i_type == OrderItem.TYPE_WARRANTY:
            unit = "式"
            s_raw = ""
            e_raw = ""
        else:
            i_type = OrderItem.TYPE_SERVICE
            if not i_name:
                errors.append(f"{row_label}請填寫品項名稱。")
            if not unit:
                unit = "式"

        if not i_name and i_type != OrderItem.TYPE_WARRANTY:
            i_name = "未命名品項"

        try:
            qty_val = int(qty_raw or 1)
            if qty_val <= 0:
                errors.append(f"{row_label}「{i_name or '維護保固'}」的數量必須大於 0。")
                qty_val = 1
        except ValueError:
            errors.append(f"{row_label}「{i_name or '維護保固'}」的數量格式不正確。")
            qty_val = 1

        try:
            price_val = Decimal(price_raw.replace(",", "") or "0")
            if price_val < Decimal("0"):
                errors.append(f"{row_label}「{i_name or '維護保固'}」的單價不可小於 0。")
                price_val = Decimal("0")
        except Exception:
            errors.append(f"{row_label}「{i_name or '維護保固'}」的單價格式不正確。")
            price_val = Decimal("0")

        s_date = _parse_date(s_raw)
        e_date = _parse_date(e_raw)
        if s_date == "INVALID" or e_date == "INVALID":
            errors.append(f"{row_label}「{i_name}」的起始或到期日期格式不正確。")
            s_date = None if s_date == "INVALID" else s_date
            e_date = None if e_date == "INVALID" else e_date
        elif s_date and e_date and e_date < s_date:
            errors.append(f"{row_label}「{i_name}」的到期日期不可早於起始日期。")

        warranty_days_val = None
        if i_type == OrderItem.TYPE_WARRANTY:
            try:
                warranty_days_val = int(w_days_raw)
                if warranty_days_val <= 0:
                    errors.append(f"{row_label}為「維護保固」，保固天數必須為大於 0 的整數。")
                    warranty_days_val = None
            except (TypeError, ValueError):
                errors.append(f"{row_label}為「維護保固」，請填寫正確的保固天數（例如 365）。")
                warranty_days_val = None

        row_dict = {
            "sort_order": sort_idx,
            "row_uid": r_uid,
            "item_type": i_type,
            "name": i_name[:150],
            "description": i_desc[:300],
            "product": product_obj if i_type == OrderItem.TYPE_LICENSE else None,
            "license_type": lic_type if i_type == OrderItem.TYPE_LICENSE else "",
            "hardware_model": hw_model_obj if i_type == OrderItem.TYPE_HARDWARE else None,
            "start_date": s_date,
            "end_date": e_date,
            "warranty_days": warranty_days_val,
            "warranty_target_item_indexes": [],
            "warranty_existing_licenses": [],
            "warranty_existing_devices": [],
            "_raw_order_target_uids": _parse_csv_tokens(w_order_targets_raw) if i_type == OrderItem.TYPE_WARRANTY else [],
            "_raw_existing_lic_ids": _parse_csv_tokens(w_exist_lics_raw) if i_type == OrderItem.TYPE_WARRANTY else [],
            "_raw_existing_dev_ids": _parse_csv_tokens(w_exist_devs_raw) if i_type == OrderItem.TYPE_WARRANTY else [],
            "sla_level": sla[:50] if i_type == OrderItem.TYPE_WARRANTY else "",
            "quantity": qty_val,
            "unit": (unit or "式")[:20],
            "unit_price": price_val,
        }
        parsed.append(row_dict)
        uid_to_row[r_uid] = row_dict
        uid_to_row[str(sort_idx)] = row_dict

    # Pass 2: 解析維護保固明細的涵蓋標的（本單新購軟硬體 + 客戶已購授權/設備）並自動組合名稱
    for row in parsed:
        if row["item_type"] != OrderItem.TYPE_WARRANTY:
            continue
        row_label = f"第 {row['sort_order']} 列明細"

        resolved_indexes: list[int] = []
        target_labels: list[str] = []
        for tok in row["_raw_order_target_uids"]:
            target_row = uid_to_row.get(tok)
            if (
                target_row
                and target_row is not row
                and target_row["item_type"] in (OrderItem.TYPE_LICENSE, OrderItem.TYPE_HARDWARE)
            ):
                if target_row["sort_order"] not in resolved_indexes:
                    resolved_indexes.append(target_row["sort_order"])
                    target_labels.append(target_row["name"])

        resolved_lics: list[CustomerLicense] = []
        for lid in row["_raw_existing_lic_ids"]:
            lic_obj = cust_licenses_map.get(lid)
            if lic_obj:
                resolved_lics.append(lic_obj)
                target_labels.append(f"{lic_obj.product.name} ({lic_obj.license_no})")

        resolved_devs: list[HardwareDevice] = []
        for did in row["_raw_existing_dev_ids"]:
            dev_obj = cust_devices_map.get(did)
            if dev_obj:
                resolved_devs.append(dev_obj)
                model_str = dev_obj.model.name if dev_obj.model_id and dev_obj.model else "硬體設備"
                target_labels.append(f"{model_str} ({dev_obj.code})")

        if not resolved_indexes and not resolved_lics and not resolved_devs:
            errors.append(f"{row_label}為「維護保固」，請至少勾選一項本單新購軟硬體或該客戶已購的產品／硬體。")

        row["warranty_target_item_indexes"] = resolved_indexes
        row["warranty_existing_licenses"] = resolved_lics
        row["warranty_existing_devices"] = resolved_devs
        row["name"] = _build_warranty_item_name(target_labels, row["warranty_days"])
        row["unit"] = "式"

    if not parsed and not errors:
        errors.append("訂單至少需填寫一筆有效的品項明細。")

    return parsed, errors


def _can_view_orders(user) -> bool:
    return (
        user_can_view_crm(user)
        or user_can_edit_orders(user)
        or user_can_approve_orders(user)
        or user_can_ship_orders(user)
        or user_can_close_orders(user)
    )


def _can_access_order(user, order: Order) -> bool:
    """驗證客戶視野範圍（含業務隔離模式）與訂單權限"""
    if not _can_view_orders(user):
        return False
    if user.is_superuser or getattr(user, "is_staff", False) or user_can_manage_system(user):
        return True
    # 工程師與管理部若非被限制視野可全域檢視；若屬業務隔離模式則遵守 customer_scope 或為該單負責業務
    if order.salesperson_id == user.id or order.created_by_id == user.id:
        return True
    return user_can_access_customer(user, order.customer)


def _scoped_orders_qs(user):
    qs = Order.objects.select_related(
        "customer",
        "salesperson",
        "created_by",
        "approved_by",
        "shipped_by",
        "closed_by",
    ).prefetch_related("payments")
    return filter_by_customer_scope(user, qs, customer_lookup="customer")


@login_required
def order_list(request):
    if not _can_view_orders(request.user):
        messages.error(request, "權限不足：無法瀏覽訂單與報價模組。")
        return redirect("hardware-dashboard")

    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    order_sort = request.GET.get("order", "-created_at").strip()

    base_qs = _scoped_orders_qs(request.user)

    # 統計各狀態數量與待收款總額
    status_counts_raw = base_qs.values("status").annotate(cnt=Count("id"))
    status_counts = {row["status"]: row["cnt"] for row in status_counts_raw}
    pending_payment_review_count = (
        base_qs.filter(
            status=Order.STATUS_PENDING_PAYMENT,
            payments__status=OrderPayment.STATUS_PENDING,
        )
        .distinct()
        .count()
    )
    pending_payment_total = (
        base_qs.filter(status=Order.STATUS_PENDING_PAYMENT).aggregate(s=Sum("total_amount"))["s"]
        or Decimal("0")
    )

    orders = base_qs
    if query:
        orders = orders.filter(
            Q(order_no__icontains=query)
            | Q(title__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__code__icontains=query)
            | Q(salesperson__username__icontains=query)
            | Q(salesperson__first_name__icontains=query)
            | Q(salesperson__last_name__icontains=query)
        )

    if status_filter == "payment_review":
        orders = orders.filter(
            status=Order.STATUS_PENDING_PAYMENT,
            payments__status=OrderPayment.STATUS_PENDING,
        ).distinct()
    elif status_filter in dict(Order.STATUS_CHOICES):
        orders = orders.filter(status=status_filter)

    valid_sorts = [
        "order_no",
        "-order_no",
        "total_amount",
        "-total_amount",
        "quote_date",
        "-quote_date",
        "created_at",
        "-created_at",
    ]
    if order_sort in valid_sorts:
        orders = orders.order_by(order_sort, "-id")
    else:
        orders = orders.order_by("-created_at", "-id")

    paginator = Paginator(orders, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "orders/order_list.html",
        {
            "page_obj": page_obj,
            "query": query,
            "status_filter": status_filter,
            "order_sort": order_sort,
            "total_count": paginator.count,
            "status_counts": status_counts,
            "all_count": sum(status_counts.values()),
            "pending_payment_review_count": pending_payment_review_count,
            "pending_payment_total": pending_payment_total,
            "can_edit_orders": user_can_edit_orders(request.user),
            "can_approve_orders": user_can_approve_orders(request.user),
            "can_ship_orders": user_can_ship_orders(request.user),
            "can_close_orders": user_can_close_orders(request.user),
        },
    )


@login_required
def order_export(request):
    if not _can_view_orders(request.user):
        messages.error(request, "權限不足。")
        return redirect("hardware-dashboard")

    from apps.core.excel_utils import export_to_excel

    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    orders = _scoped_orders_qs(request.user)

    if query:
        orders = orders.filter(
            Q(order_no__icontains=query)
            | Q(title__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__code__icontains=query)
        )
    if status_filter == "payment_review":
        orders = orders.filter(
            status=Order.STATUS_PENDING_PAYMENT,
            payments__status=OrderPayment.STATUS_PENDING,
        ).distinct()
    elif status_filter in dict(Order.STATUS_CHOICES):
        orders = orders.filter(status=status_filter)

    headers = [
        "訂單單號",
        "版次",
        "狀態",
        "客戶代號",
        "客戶名稱",
        "專案/訂單主旨",
        "負責業務",
        "報價日期",
        "計稅方式",
        "未稅/明細小計",
        "議價折讓",
        "營業稅額",
        "應收總額",
        "已確認入帳",
        "尚欠餘額",
        "審核主管",
        "出貨工程師",
        "結案人員",
        "建立時間",
    ]
    rows = []
    for o in orders:
        sp_name = (o.salesperson.get_full_name() or o.salesperson.username) if o.salesperson else "-"
        rows.append([
            o.order_no,
            f"v{o.current_version}",
            o.get_status_display(),
            o.customer.code,
            o.customer.name,
            o.title,
            sp_name,
            o.quote_date.strftime("%Y-%m-%d") if o.quote_date else "",
            o.get_tax_mode_display(),
            int(o.subtotal or 0),
            int(o.discount_amount or 0),
            int(o.tax_amount or 0),
            int(o.total_amount or 0),
            int(o.confirmed_paid_amount or 0),
            int(o.unpaid_balance or 0),
            o.approved_by.username if o.approved_by else "-",
            o.shipped_by.username if o.shipped_by else "-",
            o.closed_by.username if o.closed_by else "-",
            o.created_at.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M"),
        ])

    return export_to_excel(
        filename_prefix="crm_orders",
        sheet_name="訂單與收款追蹤清單",
        headers=headers,
        rows=rows,
    )


def _prepare_form_catalog_json(customer_qs) -> dict:
    """為 order_form.html 準備客戶聯絡人、已購軟體/硬體、軟體產品主檔與硬體型號規格 JSON"""
    customers_data = {}
    for c in customer_qs.prefetch_related("contacts", "licenses__product", "probes__model"):
        contacts_list = [
            {
                "id": cp.pk,
                "name": cp.name,
                "title": cp.title,
                "phone": cp.phone,
                "email": cp.email,
                "is_primary": cp.is_primary,
            }
            for cp in c.contacts.all()
        ]
        existing_licenses = [
            {
                "id": lic.pk,
                "license_no": lic.license_no,
                "product_name": lic.product.name if lic.product_id else "",
                "quantity": lic.quantity,
                "unit": lic.unit,
                "label": f"[{lic.license_no}] {lic.product.name if lic.product_id else ''} ({lic.quantity}{lic.unit})",
                "short_name": f"{lic.product.name if lic.product_id else ''} ({lic.license_no})",
            }
            for lic in c.licenses.all()
            if lic.approval_status == CustomerLicense.APPROVAL_APPROVED and not lic.is_disabled
        ]
        existing_devices = [
            {
                "id": dev.pk,
                "code": dev.code,
                "model_name": dev.model.name if dev.model_id else "未知型號",
                "label": f"[{dev.code}] {dev.model.name if dev.model_id else '未知型號'}",
                "short_name": f"{dev.model.name if dev.model_id else '硬體設備'} ({dev.code})",
            }
            for dev in c.probes.all()
            if dev.status == HardwareDevice.STATUS_SHIPPED and not dev.is_deleted
        ]
        customers_data[str(c.pk)] = {
            "contact": c.contact,
            "phone": c.phone,
            "email": c.email,
            "contacts": contacts_list,
            "existing_licenses": existing_licenses,
            "existing_devices": existing_devices,
        }

    products = list(Product.objects.filter(is_active=True).order_by("code"))
    products_data = [
        {"id": p.pk, "code": p.code, "name": p.name, "unit": p.default_unit or "台"}
        for p in products
    ]
    hw_models = list(HardwareModel.objects.all().order_by("name"))
    hw_models_data = [
        {
            "id": m.pk,
            "code": m.model_code,
            "name": m.name,
            "available_count": m.available_count,
        }
        for m in hw_models
    ]

    return {
        "customers_json": json.dumps(customers_data, ensure_ascii=False),
        "products_list": products,
        "products_json": json.dumps(products_data, ensure_ascii=False),
        "hw_models_list": hw_models,
        "hw_models_json": json.dumps(hw_models_data, ensure_ascii=False),
    }


def _create_order_item_from_parsed(order: Order, row: dict) -> OrderItem:
    """從 _parse_order_items_from_post 的解析結果建立 OrderItem 並設定 M2M 維護保固標的"""
    existing_lics = row.get("warranty_existing_licenses") or []
    existing_devs = row.get("warranty_existing_devices") or []
    item = OrderItem.objects.create(
        order=order,
        sort_order=row["sort_order"],
        item_type=row["item_type"],
        name=row["name"],
        description=row["description"],
        product=row["product"],
        license_type=row["license_type"],
        hardware_model=row["hardware_model"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        warranty_days=row["warranty_days"],
        warranty_target_item_indexes=row["warranty_target_item_indexes"],
        sla_level=row["sla_level"],
        quantity=row["quantity"],
        unit=row["unit"],
        unit_price=row["unit_price"],
    )
    if existing_lics:
        item.warranty_existing_licenses.set(existing_lics)
    if existing_devs:
        item.warranty_existing_devices.set(existing_devs)
    return item


def _serialize_parsed_items_for_form(parsed_items: list[dict]) -> list[dict]:
    """將 POST 失敗回顯的解析明細轉為 order_form.html INITIAL_ITEMS 格式"""
    sort_to_uid = {row["sort_order"]: row["row_uid"] for row in parsed_items}
    result = []
    for row in parsed_items:
        target_uids = [
            sort_to_uid[idx]
            for idx in (row.get("warranty_target_item_indexes") or [])
            if idx in sort_to_uid
        ]
        if not target_uids and row.get("_raw_order_target_uids"):
            target_uids = list(row["_raw_order_target_uids"])
        result.append({
            "row_uid": row["row_uid"],
            "item_type": row["item_type"],
            "name": row["name"],
            "description": row["description"],
            "product_id": row["product"].pk if row["product"] else "",
            "license_type": row["license_type"] or "perpetual",
            "hardware_model_id": row["hardware_model"].pk if row["hardware_model"] else "",
            "start_date": row["start_date"].strftime("%Y-%m-%d") if row["start_date"] else "",
            "end_date": row["end_date"].strftime("%Y-%m-%d") if row["end_date"] else "",
            "warranty_days": row["warranty_days"] or 365,
            "warranty_order_targets": target_uids,
            "warranty_existing_license_ids": [lic.pk for lic in (row.get("warranty_existing_licenses") or [])],
            "warranty_existing_device_ids": [dev.pk for dev in (row.get("warranty_existing_devices") or [])],
            "sla_level": row["sla_level"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "unit_price": int(row["unit_price"]),
        })
    return result


@login_required
def order_create(request):
    if not user_can_edit_orders(request.user):
        messages.error(request, "權限不足：您沒有建立訂單與報價單的權限。")
        return redirect("order-list")

    sys_settings = SystemSetting.get_settings()
    customer_param = request.GET.get("customer", "").strip()
    today_tw = timezone.now().astimezone(TAIPEI_TZ).date()
    initial_data = {
        "salesperson": request.user.pk,
        "quote_date": today_tw,
        "valid_until": today_tw + datetime.timedelta(days=7),
        "terms_note": sys_settings.quotation_default_terms,
    }
    if customer_param.isdigit():
        c_obj = Customer.objects.filter(pk=int(customer_param)).first()
        if c_obj and user_can_access_customer(request.user, c_obj):
            initial_data["customer"] = c_obj.pk
            initial_data["contact_name"] = c_obj.contact
            initial_data["contact_phone"] = c_obj.phone
            initial_data["contact_email"] = c_obj.email

    item_errors = []
    submitted_items = []

    if request.method == "POST":
        form = OrderForm(request.POST, user=request.user)
        parsed_items, item_errors = _parse_order_items_from_post(
            request, customer_id=request.POST.get("customer")
        )
        if form.is_valid() and not item_errors:
            with transaction.atomic():
                order = form.save(commit=False)
                order.created_by = request.user
                if not order.salesperson:
                    order.salesperson = request.user
                order.status = Order.STATUS_DRAFT
                order.current_version = 1
                order.save()

                for row in parsed_items:
                    _create_order_item_from_parsed(order, row)

                order.recalculate_totals(save=True)
                create_order_version_snapshot(
                    order=order,
                    actor=request.user,
                    change_note="建立初始訂單報價 (v1)",
                    increment_version=False,
                    ip_address=_client_ip(request),
                )
                CustomerFollow.objects.get_or_create(customer=order.customer, user=order.salesperson)
                log_action(
                    user=request.user,
                    action="建立新訂單",
                    target_type="Order",
                    target_identifier=order.order_no,
                    detail=f"客戶: {order.customer.name} | 主旨: {order.title} | 總金額: NT$ {int(order.total_amount):,}",
                    ip_address=_client_ip(request),
                )
            messages.success(request, f"已建立訂單「{order.order_no}」（v1），可列印報價單或上傳回簽單後送交主管審核。")
            return redirect("order-detail", pk=order.pk)
        else:
            submitted_items = _serialize_parsed_items_for_form(parsed_items)
    else:
        form = OrderForm(initial=initial_data, user=request.user)

    catalog_ctx = _prepare_form_catalog_json(form.fields["customer"].queryset)
    return render(
        request,
        "orders/order_form.html",
        {
            "form": form,
            "title": "建立新訂單與報價單",
            "is_edit": False,
            "item_errors": item_errors,
            "initial_items_json": json.dumps(submitted_items, ensure_ascii=False),
            **catalog_ctx,
        },
    )


@login_required
def order_update(request, pk):
    order = get_object_or_404(Order.objects.select_related("customer"), pk=pk)
    if not _can_access_order(request.user, order) or not user_can_edit_orders(request.user):
        messages.error(request, "權限不足：無法編輯此訂單。")
        return redirect("order-detail", pk=order.pk)

    if not order.is_editable_by_sales:
        messages.error(request, f"目前訂單狀態為「{order.get_status_display()}」，內容已鎖定不可修改金額與品項。")
        return redirect("order-detail", pk=order.pk)

    item_errors = []
    submitted_items = []

    if request.method == "POST":
        form = OrderForm(request.POST, instance=order, user=request.user)
        parsed_items, item_errors = _parse_order_items_from_post(
            request, customer_id=request.POST.get("customer") or order.customer_id
        )
        save_mode = request.POST.get("save_mode", "save_draft")
        version_note = request.POST.get("version_note", "").strip()

        if form.is_valid() and not item_errors:
            with transaction.atomic():
                # 關鍵：在覆寫舊訂單與舊明細之前，若現行版次尚未存檔快照，先用修改前的資料封存舊版快照
                if save_mode == "new_version" and not order.versions.filter(version_no=order.current_version).exists():
                    old_order = Order.objects.get(pk=order.pk)
                    create_order_version_snapshot(
                        order=old_order,
                        actor=request.user,
                        change_note="改價前自動留存原版次",
                        increment_version=False,
                        ip_address=_client_ip(request),
                    )

                updated_order = form.save(commit=False)
                updated_order.save()

                updated_order.items.all().delete()
                for row in parsed_items:
                    _create_order_item_from_parsed(updated_order, row)

                updated_order.recalculate_totals(save=True)

                if save_mode == "new_version":
                    updated_order.current_version += 1
                    updated_order.save(update_fields=["current_version", "updated_at"])
                    snap = create_order_version_snapshot(
                        order=updated_order,
                        actor=request.user,
                        change_note=version_note or f"客戶議價/調整後產生新版報價 (v{updated_order.current_version})",
                        increment_version=False,
                        ip_address=_client_ip(request),
                    )
                    log_action(
                        user=request.user,
                        action=f"產生新版報價單 (v{updated_order.current_version})",
                        target_type="Order",
                        target_identifier=updated_order.order_no,
                        detail=f"總金額: NT$ {int(updated_order.total_amount):,} | 說明: {snap.change_note}",
                        ip_address=_client_ip(request),
                    )
                    messages.success(
                        request,
                        f"已儲存訂單並產生新版報價單「v{updated_order.current_version}」！歷史版次已完整留存。",
                    )
                else:
                    create_order_version_snapshot(
                        order=updated_order,
                        actor=request.user,
                        change_note="更新當前草稿報價內容",
                        increment_version=False,
                        ip_address=_client_ip(request),
                    )
                    log_action(
                        user=request.user,
                        action="更新訂單草稿",
                        target_type="Order",
                        target_identifier=updated_order.order_no,
                        detail=f"版次 v{updated_order.current_version} | 總金額: NT$ {int(updated_order.total_amount):,}",
                        ip_address=_client_ip(request),
                    )
                    messages.success(request, f"訂單「{updated_order.order_no}」草稿已更新（維持版次 v{updated_order.current_version}）。")

            return redirect("order-detail", pk=order.pk)
        else:
            submitted_items = _serialize_parsed_items_for_form(parsed_items)
    else:
        form = OrderForm(instance=order, user=request.user)
        existing_items = list(
            order.items.prefetch_related("warranty_existing_licenses", "warranty_existing_devices").all()
        )
        sort_to_uid = {item.sort_order: f"row_{item.sort_order}" for item in existing_items}
        for item in existing_items:
            target_uids = [
                sort_to_uid[int(idx)]
                for idx in (item.warranty_target_item_indexes or [])
                if str(idx).isdigit() and int(idx) in sort_to_uid
            ]
            submitted_items.append({
                "row_uid": sort_to_uid[item.sort_order],
                "item_type": item.item_type,
                "name": item.name,
                "description": item.description,
                "product_id": item.product_id or "",
                "license_type": item.license_type or "perpetual",
                "hardware_model_id": item.hardware_model_id or "",
                "start_date": item.start_date.strftime("%Y-%m-%d") if item.start_date else "",
                "end_date": item.end_date.strftime("%Y-%m-%d") if item.end_date else "",
                "warranty_days": item.warranty_days or 365,
                "warranty_order_targets": target_uids,
                "warranty_existing_license_ids": list(item.warranty_existing_licenses.values_list("id", flat=True)),
                "warranty_existing_device_ids": list(item.warranty_existing_devices.values_list("id", flat=True)),
                "sla_level": item.sla_level or "",
                "quantity": item.quantity,
                "unit": item.unit,
                "unit_price": int(item.unit_price or 0),
            })

    catalog_ctx = _prepare_form_catalog_json(form.fields["customer"].queryset)
    return render(
        request,
        "orders/order_form.html",
        {
            "form": form,
            "order": order,
            "title": f"編輯訂單報價 - {order.order_no} (v{order.current_version})",
            "is_edit": True,
            "item_errors": item_errors,
            "initial_items_json": json.dumps(submitted_items, ensure_ascii=False),
            **catalog_ctx,
        },
    )


@login_required
@require_POST
def order_new_version(request, pk):
    """在訂單詳情頁直接基於當前內容跳版號產生新版快照"""
    order = get_object_or_404(Order, pk=pk)
    if not _can_access_order(request.user, order) or not user_can_edit_orders(request.user):
        messages.error(request, "權限不足。")
        return redirect("order-detail", pk=order.pk)
    if not order.is_editable_by_sales:
        messages.error(request, "只有草稿或退回狀態的訂單可產生新版報價單。")
        return redirect("order-detail", pk=order.pk)

    note = request.POST.get("change_note", "").strip()
    ver = create_order_version_snapshot(
        order=order,
        actor=request.user,
        change_note=note or f"建立報價新版 v{order.current_version + 1}",
        increment_version=True,
        ip_address=_client_ip(request),
    )
    messages.success(request, f"已產生新版報價單 v{ver.version_no}！")
    return redirect("order-detail", pk=order.pk)


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related(
            "customer",
            "contact_person",
            "salesperson",
            "created_by",
            "approved_by",
            "shipped_by",
            "closed_by",
            "cancelled_by",
        ).prefetch_related(
            "items__product",
            "items__hardware_model",
            "items__generated_license__product",
            "items__generated_contract",
            "items__allocated_devices__model",
            "items__warranty_existing_licenses__product",
            "items__warranty_existing_devices__model",
            "versions__created_by",
            "attachments__uploaded_by",
            "payments__reported_by",
            "payments__confirmed_by",
        ),
        pk=pk,
    )
    if not _can_access_order(request.user, order):
        messages.error(request, "權限不足：您無權檢視此客戶訂單。")
        return redirect("order-list")

    items = list(order.items.all())
    items_by_sort = {it.sort_order: it for it in items}
    for it in items:
        it.warranty_target_labels = (
            get_warranty_target_labels(it, items_by_sort)
            if it.item_type == OrderItem.TYPE_WARRANTY
            else []
        )
    versions = list(order.versions.all())
    attachments = list(order.attachments.all())
    payments = list(order.payments.all())

    # 計算版次金額差異以便展示
    asc_versions = sorted(versions, key=lambda v: v.version_no)
    prev_total = None
    diff_map = {}
    for v in asc_versions:
        if prev_total is None:
            diff_map[v.version_no] = None
        else:
            diff_map[v.version_no] = v.total_amount - prev_total
        prev_total = v.total_amount
    for v in versions:
        v.diff_from_prev = diff_map.get(v.version_no)

    # 若處於「待出貨 / 待交付」，為每個硬體明細載入可供挑選的在庫設備
    hardware_pickers = []
    if order.status == Order.STATUS_PENDING_SHIPMENT:
        for item in items:
            if item.item_type == OrderItem.TYPE_HARDWARE:
                avail_qs = HardwareDevice.objects.filter(
                    is_deleted=False,
                    status=HardwareDevice.STATUS_AVAILABLE,
                ).select_related("model").order_by("code")
                if item.hardware_model_id:
                    avail_qs = avail_qs.filter(model_id=item.hardware_model_id)
                avail_list = list(avail_qs)
                hardware_pickers.append({
                    "item": item,
                    "available_devices": avail_list,
                    "available_count": len(avail_list),
                    "is_sufficient": len(avail_list) >= int(item.quantity or 0),
                })

    generated_licenses = list(
        CustomerLicense.objects.filter(source_order=order).select_related("product").order_by("license_no")
    )
    generated_contracts = list(
        MaintenanceContract.objects.filter(source_order=order).prefetch_related("licenses", "probes").order_by("contract_no")
    )
    shipped_devices = list(
        HardwareDevice.objects.filter(source_order=order, is_deleted=False).select_related("model").order_by("code")
    )

    attachment_form = OrderAttachmentForm()
    payment_form = OrderPaymentForm(
        initial={
            "amount": int(order.unpaid_balance) if order.unpaid_balance > 0 else "",
            "payment_date": timezone.now().astimezone(TAIPEI_TZ).date(),
        }
    )

    can_edit = user_can_edit_orders(request.user)
    can_approve = user_can_approve_orders(request.user)
    can_ship = user_can_ship_orders(request.user)
    can_close = user_can_close_orders(request.user)
    can_cancel = False
    if order.status in (Order.STATUS_DRAFT, Order.STATUS_REJECTED):
        can_cancel = can_edit or can_approve or request.user.is_superuser
    elif order.status in (
        Order.STATUS_PENDING_APPROVAL,
        Order.STATUS_PENDING_SHIPMENT,
        Order.STATUS_PENDING_PAYMENT,
    ):
        can_cancel = can_approve or user_can_manage_system(request.user) or request.user.is_superuser

    return render(
        request,
        "orders/order_detail.html",
        {
            "order": order,
            "items": items,
            "versions": versions,
            "attachments": attachments,
            "payments": payments,
            "hardware_pickers": hardware_pickers,
            "generated_licenses": generated_licenses,
            "generated_contracts": generated_contracts,
            "shipped_devices": shipped_devices,
            "attachment_form": attachment_form,
            "payment_form": payment_form,
            "can_edit_orders": can_edit,
            "can_approve_orders": can_approve,
            "can_ship_orders": can_ship,
            "can_close_orders": can_close,
            "can_cancel_order": can_cancel,
            "has_signed_quote": order.has_signed_quote_attachment,
        },
    )


@login_required
@require_POST
def order_submit(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if not _can_access_order(request.user, order) or not user_can_edit_orders(request.user):
        messages.error(request, "權限不足：您沒有送審此訂單的權限。")
        return redirect("order-detail", pk=order.pk)

    reason = request.POST.get("no_signed_quote_reason", "").strip()
    ok, msg = submit_order_for_approval(
        order=order,
        actor=request.user,
        no_signed_quote_reason=reason,
        ip_address=_client_ip(request),
    )
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("order-detail", pk=order.pk)


@login_required
@require_POST
def order_approve(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if not _can_access_order(request.user, order) or not user_can_approve_orders(request.user):
        messages.error(request, "權限不足：僅限具備「審核訂單」權限之業務主管執行核准。")
        return redirect("order-detail", pk=order.pk)

    ok, msg = approve_order(order=order, reviewer=request.user, ip_address=_client_ip(request))
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("order-detail", pk=order.pk)


@login_required
@require_POST
def order_reject(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if not _can_access_order(request.user, order) or not user_can_approve_orders(request.user):
        messages.error(request, "權限不足：僅限具備「審核訂單」權限之業務主管執行退回。")
        return redirect("order-detail", pk=order.pk)

    reason = request.POST.get("rejection_reason", "").strip()
    ok, msg = reject_order(
        order=order,
        reviewer=request.user,
        rejection_reason=reason,
        ip_address=_client_ip(request),
    )
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("order-detail", pk=order.pk)


@login_required
@require_POST
def order_ship(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if not _can_access_order(request.user, order) or not user_can_ship_orders(request.user):
        messages.error(request, "權限不足：僅限具備「訂單出貨與交付」權限之工程師/庫存管理員操作。")
        return redirect("order-detail", pk=order.pk)

    device_allocations: dict[int, list[int]] = {}
    for item in order.items.filter(item_type=OrderItem.TYPE_HARDWARE):
        raw_ids = request.POST.getlist(f"devices_item_{item.pk}")
        device_allocations[item.pk] = [int(x) for x in raw_ids if str(x).isdigit()]

    license_keys: dict[int, str] = {}
    for lic in CustomerLicense.objects.filter(source_order=order):
        k_val = request.POST.get(f"license_key_{lic.pk}")
        if k_val is not None:
            license_keys[lic.pk] = k_val

    shipment_note = request.POST.get("shipment_note", "").strip()
    ok, msg = confirm_order_shipment(
        order=order,
        engineer=request.user,
        device_allocations=device_allocations,
        license_keys=license_keys,
        shipment_note=shipment_note,
        ip_address=_client_ip(request),
    )
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("order-detail", pk=order.pk)


@login_required
@require_POST
def order_payment_report(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if not _can_access_order(request.user, order) or not (
        user_can_edit_orders(request.user)
        or user_can_approve_orders(request.user)
        or user_can_close_orders(request.user)
    ):
        messages.error(request, "權限不足：無法回報此訂單收款。")
        return redirect("order-detail", pk=order.pk)

    form = OrderPaymentForm(request.POST, request.FILES)
    if form.is_valid():
        cd = form.cleaned_data
        ok, msg = report_order_payment(
            order=order,
            reporter=request.user,
            amount=cd["amount"],
            payment_date=cd["payment_date"],
            payment_method=cd["payment_method"],
            reference_no=cd.get("reference_no", ""),
            proof_file=cd.get("proof_file"),
            note=cd.get("note", ""),
            ip_address=_client_ip(request),
        )
        if ok:
            messages.success(request, msg)
        else:
            messages.error(request, msg)
    else:
        err_msgs = []
        for field_errs in form.errors.values():
            err_msgs.extend(field_errs)
        messages.error(request, "收款回報格式有誤：" + "；".join(err_msgs))
    return redirect("order-detail", pk=order.pk)


@login_required
@require_POST
def order_payment_confirm(request, payment_id):
    payment = get_object_or_404(OrderPayment.objects.select_related("order__customer"), pk=payment_id)
    if not _can_access_order(request.user, payment.order) or not user_can_close_orders(request.user):
        messages.error(request, "權限不足：僅限管理部人員可確認款項入帳。")
        return redirect("order-detail", pk=payment.order_id)

    review_note = request.POST.get("review_note", "").strip()
    auto_close = request.POST.get("auto_close", "1") == "1"
    ok, msg = confirm_order_payment(
        payment=payment,
        reviewer=request.user,
        review_note=review_note,
        auto_close_if_paid_in_full=auto_close,
        ip_address=_client_ip(request),
    )
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("order-detail", pk=payment.order_id)


@login_required
@require_POST
def order_payment_reject(request, payment_id):
    payment = get_object_or_404(OrderPayment.objects.select_related("order__customer"), pk=payment_id)
    if not _can_access_order(request.user, payment.order) or not user_can_close_orders(request.user):
        messages.error(request, "權限不足：僅限管理部人員可退回收款回報。")
        return redirect("order-detail", pk=payment.order_id)

    review_note = request.POST.get("review_note", "").strip()
    ok, msg = reject_order_payment(
        payment=payment,
        reviewer=request.user,
        review_note=review_note,
        ip_address=_client_ip(request),
    )
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("order-detail", pk=payment.order_id)


@login_required
@require_GET
def order_payment_proof_download(request, payment_id):
    payment = get_object_or_404(OrderPayment.objects.select_related("order__customer"), pk=payment_id)
    if not _can_access_order(request.user, payment.order):
        raise Http404("無權限存取此附件")
    if not payment.proof_file or not payment.proof_file.storage.exists(payment.proof_file.name):
        raise Http404("找不到憑證檔案")
    content_type, _ = mimetypes.guess_type(payment.proof_file.name)
    return FileResponse(
        payment.proof_file.open("rb"),
        content_type=content_type or "application/octet-stream",
        filename=os.path.basename(payment.proof_file.name),
    )


@login_required
@require_POST
def order_close(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if not _can_access_order(request.user, order) or not user_can_close_orders(request.user):
        messages.error(request, "權限不足：僅限管理部人員可將訂單結案。")
        return redirect("order-detail", pk=order.pk)

    closing_note = request.POST.get("closing_note", "").strip()
    ok, msg = close_order_manually(
        order=order,
        reviewer=request.user,
        closing_note=closing_note,
        ip_address=_client_ip(request),
    )
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("order-detail", pk=order.pk)


@login_required
@require_POST
def order_cancel(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if not _can_access_order(request.user, order):
        messages.error(request, "權限不足。")
        return redirect("order-list")

    if order.status in (Order.STATUS_DRAFT, Order.STATUS_REJECTED):
        allowed = user_can_edit_orders(request.user) or user_can_approve_orders(request.user) or request.user.is_superuser
    else:
        allowed = user_can_approve_orders(request.user) or user_can_manage_system(request.user) or request.user.is_superuser

    if not allowed:
        messages.error(request, "權限不足：已送審或已核准之訂單僅限業務主管或系統管理員執行作廢。")
        return redirect("order-detail", pk=order.pk)

    cancel_reason = request.POST.get("cancel_reason", "").strip()
    ok, msg = cancel_order(
        order=order,
        actor=request.user,
        cancel_reason=cancel_reason,
        ip_address=_client_ip(request),
    )
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("order-detail", pk=order.pk)


@login_required
@require_POST
def order_attachment_upload(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if not _can_access_order(request.user, order) or not (
        user_can_edit_orders(request.user)
        or user_can_approve_orders(request.user)
        or user_can_ship_orders(request.user)
        or user_can_close_orders(request.user)
    ):
        messages.error(request, "權限不足：無法上傳訂單附件。")
        return redirect("order-detail", pk=order.pk)

    uploaded = request.FILES.get("file")
    if not uploaded:
        messages.error(request, "請選擇要上傳的佐證檔案。")
        return redirect("order-detail", pk=order.pk)
    if uploaded.size > 20 * 1024 * 1024:
        messages.error(request, "單一附件大小不可超過 20 MB。")
        return redirect("order-detail", pk=order.pk)

    att_type = request.POST.get("attachment_type", OrderAttachment.TYPE_SIGNED_QUOTE)
    if att_type not in dict(OrderAttachment.ATTACHMENT_TYPE_CHOICES):
        att_type = OrderAttachment.TYPE_OTHER
    note = request.POST.get("note", "").strip()

    att = OrderAttachment.objects.create(
        order=order,
        attachment_type=att_type,
        file=uploaded,
        filename=uploaded.name,
        file_size=uploaded.size,
        note=note,
        uploaded_by=request.user,
    )
    log_action(
        user=request.user,
        action="上傳訂單佐證附件",
        target_type="Order",
        target_identifier=order.order_no,
        detail=f"[{att.get_attachment_type_display()}] {att.filename}",
        ip_address=_client_ip(request),
    )
    messages.success(request, f"已上傳附件「{att.filename}」。")
    return redirect("order-detail", pk=order.pk)


@login_required
@require_GET
def order_attachment_download(request, att_id):
    att = get_object_or_404(OrderAttachment.objects.select_related("order__customer"), pk=att_id)
    if not _can_access_order(request.user, att.order):
        raise Http404("無權限下載此附件")
    if not att.file or not att.file.storage.exists(att.file.name):
        raise Http404("附件檔案不存在")

    content_type, _ = mimetypes.guess_type(att.filename)
    as_attach = request.GET.get("preview") != "1"
    return FileResponse(
        att.file.open("rb"),
        content_type=content_type or "application/octet-stream",
        as_attachment=as_attach,
        filename=att.filename,
    )


@login_required
@require_POST
def order_attachment_delete(request, att_id):
    att = get_object_or_404(OrderAttachment.objects.select_related("order__customer"), pk=att_id)
    order = att.order
    if not _can_access_order(request.user, order) or not (
        user_can_edit_orders(request.user) or user_can_approve_orders(request.user)
    ):
        messages.error(request, "權限不足：無法刪除附件。")
        return redirect("order-detail", pk=order.pk)

    if not order.is_editable_by_sales and att.attachment_type == OrderAttachment.TYPE_SIGNED_QUOTE and not request.user.is_superuser:
        messages.error(request, "訂單已送審或核准，受保護的「回簽報價單」佐證附件不可刪除。")
        return redirect("order-detail", pk=order.pk)

    fname = att.filename
    if att.file:
        try:
            att.file.delete(save=False)
        except Exception:
            pass
    att.delete()
    log_action(
        user=request.user,
        action="刪除訂單附件",
        target_type="Order",
        target_identifier=order.order_no,
        detail=fname,
        ip_address=_client_ip(request),
    )
    messages.success(request, f"已刪除附件「{fname}」。")
    return redirect("order-detail", pk=order.pk)


@login_required
def order_quotation_print(request, pk, version_no=None):
    """A4 直式商務報價單列印與檢視頁（支援當前草稿或歷史版本快照）"""
    order = get_object_or_404(
        Order.objects.select_related("customer", "salesperson"),
        pk=pk,
    )
    if not _can_access_order(request.user, order):
        messages.error(request, "權限不足：無法檢視此報價單。")
        return redirect("order-list")

    sys_settings = SystemSetting.get_settings()
    selected_version = None
    if version_no is not None:
        selected_version = get_object_or_404(OrderVersion, order=order, version_no=version_no)
        quote_data = selected_version.snapshot_data or build_order_snapshot_dict(order)
        display_version_no = selected_version.version_no
    else:
        quote_data = build_order_snapshot_dict(order)
        display_version_no = order.current_version

    total_int = int(Decimal(str(quote_data.get("total_amount", "0"))))
    discount_int = int(Decimal(str(quote_data.get("discount_amount", "0"))))
    subtotal_int = int(Decimal(str(quote_data.get("subtotal", "0"))))
    tax_int = int(Decimal(str(quote_data.get("tax_amount", "0"))))

    has_logo = bool(sys_settings.seller_logo and sys_settings.seller_logo.name and sys_settings.seller_logo.storage.exists(sys_settings.seller_logo.name))
    has_stamp = bool(sys_settings.seller_stamp and sys_settings.seller_stamp.name and sys_settings.seller_stamp.storage.exists(sys_settings.seller_stamp.name))
    ts = int(sys_settings.updated_at.timestamp()) if sys_settings.updated_at else 0

    return render(
        request,
        "orders/quotation_print.html",
        {
            "order": order,
            "quote": quote_data,
            "display_version_no": display_version_no,
            "is_historical_version": version_no is not None and version_no != order.current_version,
            "all_versions": order.versions.order_by("-version_no"),
            "subtotal_int": subtotal_int,
            "discount_int": discount_int,
            "tax_int": tax_int,
            "total_int": total_int,
            "total_zh_upper": _number_to_zh_currency(total_int),
            "sys_settings": sys_settings,
            "has_logo": has_logo,
            "has_stamp": has_stamp,
            "logo_url": f"{reverse('quotation-asset-serve', kwargs={'asset_type': 'logo'})}?v={ts}" if has_logo else "",
            "stamp_url": f"{reverse('quotation-asset-serve', kwargs={'asset_type': 'stamp'})}?v={ts}" if has_stamp else "",
        },
    )


@login_required
@require_GET
def quotation_asset_serve(request, asset_type: str):
    if asset_type not in ("logo", "stamp"):
        raise Http404("不支援的圖檔類型")
    sys_settings = SystemSetting.get_settings()
    file_field = sys_settings.seller_logo if asset_type == "logo" else sys_settings.seller_stamp
    if not file_field or not file_field.name or not file_field.storage.exists(file_field.name):
        raise Http404("尚未設定圖檔")
    content_type, _ = mimetypes.guess_type(file_field.name)
    resp = FileResponse(file_field.open("rb"), content_type=content_type or "application/octet-stream")
    resp["Cache-Control"] = "no-cache"
    return resp


@login_required
def quotation_settings_view(request):
    if not (user_can_manage_system(request.user) or user_can_approve_orders(request.user)):
        messages.error(request, "權限不足：僅限系統管理員或業務主管設定報價單賣方資訊與範本。")
        return redirect("order-list")

    sys_settings = SystemSetting.get_settings()
    if request.method == "POST":
        action = request.POST.get("action", "save_info")
        if action in ("clear_logo", "clear_stamp"):
            field_name = "seller_logo" if action == "clear_logo" else "seller_stamp"
            old_f = getattr(sys_settings, field_name)
            if old_f and old_f.name:
                try:
                    old_f.delete(save=False)
                except Exception:
                    pass
            setattr(sys_settings, field_name, None)
            sys_settings.save(update_fields=[field_name, "updated_at"])
            messages.success(request, "已移除報價單圖檔。")
            return redirect("quotation-settings")

        form = QuotationSettingForm(request.POST, instance=sys_settings)
        if form.is_valid():
            inst = form.save(commit=False)
            for field_name, label in (("seller_logo", "公司 Logo"), ("seller_stamp", "公司報價章")):
                up = request.FILES.get(field_name)
                if up:
                    ext = os.path.splitext(up.name)[1].lower()
                    if ext not in ALLOWED_ASSET_EXTS:
                        messages.error(request, f"{label} 格式不支援 ({ext})，請上傳 PNG/JPG/WEBP/SVG。")
                        return redirect("quotation-settings")
                    if up.size > MAX_ASSET_SIZE:
                        messages.error(request, f"{label} 檔案大小不可超過 10 MB。")
                        return redirect("quotation-settings")
                    old_f = getattr(sys_settings, field_name)
                    if old_f and old_f.name:
                        try:
                            old_f.delete(save=False)
                        except Exception:
                            pass
                    setattr(inst, field_name, up)
            inst.save()
            log_action(
                user=request.user,
                action="更新報價單賣方範本設定",
                target_type="SystemSetting",
                target_identifier="QuotationSettings",
                detail=f"賣方公司: {inst.seller_company_name or '-'}",
                ip_address=_client_ip(request),
            )
            messages.success(request, "報價單賣方公司資訊與範本設定已儲存！")
            return redirect("quotation-settings")
    else:
        form = QuotationSettingForm(instance=sys_settings)

    ts = int(sys_settings.updated_at.timestamp()) if sys_settings.updated_at else 0
    has_logo = bool(sys_settings.seller_logo and sys_settings.seller_logo.name and sys_settings.seller_logo.storage.exists(sys_settings.seller_logo.name))
    has_stamp = bool(sys_settings.seller_stamp and sys_settings.seller_stamp.name and sys_settings.seller_stamp.storage.exists(sys_settings.seller_stamp.name))

    return render(
        request,
        "orders/quotation_settings.html",
        {
            "form": form,
            "sys_settings": sys_settings,
            "has_logo": has_logo,
            "has_stamp": has_stamp,
            "logo_url": f"{reverse('quotation-asset-serve', kwargs={'asset_type': 'logo'})}?v={ts}" if has_logo else "",
            "stamp_url": f"{reverse('quotation-asset-serve', kwargs={'asset_type': 'stamp'})}?v={ts}" if has_stamp else "",
        },
    )


@login_required
@require_GET
def customer_contacts_api(request, customer_id: int):
    customer = get_object_or_404(Customer, pk=customer_id)
    if not user_can_access_customer(request.user, customer):
        return JsonResponse({"error": "權限不足"}, status=403)
    contacts = [
        {
            "id": cp.pk,
            "name": cp.name,
            "title": cp.title,
            "phone": cp.phone,
            "email": cp.email,
            "is_primary": cp.is_primary,
        }
        for cp in customer.contacts.all()
    ]
    return JsonResponse({
        "contact": customer.contact,
        "phone": customer.phone,
        "email": customer.email,
        "contacts": contacts,
    })
