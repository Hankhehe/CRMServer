import csv
import datetime
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.db import transaction

from django.urls import reverse
from django.views.decorators.http import require_POST
from django.contrib.auth import get_user_model
from apps.core.permissions import (
    user_can_view_crm,
    user_can_edit_crm,
    user_can_edit_customers,
    user_can_edit_licenses,
    user_can_manage_system,
    user_can_approve_licenses,
    user_can_approve_contracts,
    user_can_approve_hardware,
    is_warranty_approval_required_for_user,
    is_hardware_approval_required_for_user,
    is_user_followed_only_scope,
    filter_customers_for_user,
    filter_by_customer_scope,
    user_can_access_customer,
    log_action,
)
from apps.core.notification_service import (
    notify_customer_followers,
    notify_approval_submitted,
    notify_approval_decision,
)
from apps.hardware.models import HardwareDevice, HardwareDevice as Probe, InventoryTransaction
from apps.licenses.models import CustomerLicense
from .models import (
    Customer,
    ContactPerson,
    CustomerInfoSection,
    MaintenanceContract,
    CustomerActivityLog,
    ActivityAttachment,
    ContractAttachment,
    ContractProbeReplacement,
    CustomerFollow,
)

User = get_user_model()
from .forms import (
    CustomerForm,
    CustomerCustomFieldSchemaForm,
    ContactPersonForm,
    CustomerInfoSectionForm,
    MaintenanceContractForm,
    CustomerImportForm,
    CustomerActivityLogForm,
    ContractProbeReplacementForm,
)
from apps.core.models import SystemSetting


def _parse_custom_datetime_str(val_str: str) -> str:
    """解析自訂日期時間字串，轉為標準台灣時間習慣格式 YYYY-MM-DD HH:MM"""
    from zoneinfo import ZoneInfo
    s = str(val_str or "").strip()
    if not s:
        return ""
    s_norm = s.replace("T", " ").replace("/", "-").replace(".", "-")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d %H%M",
        "%Y%m%d",
    ):
        try:
            dt_obj = datetime.datetime.strptime(s_norm, fmt)
            # 確保以台灣時區 Asia/Taipei 驗證有效時間並輸出 YYYY-MM-DD HH:MM
            dt_aware = dt_obj.replace(tzinfo=ZoneInfo("Asia/Taipei"))
            return dt_aware.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    raise ValueError(f"日期時間格式不正確：「{val_str}」，請使用 YYYY-MM-DD HH:MM 格式（例如 2026-10-29 14:30）")


def _build_customer_custom_filter_defs(request, schema: list) -> tuple[list, dict, bool]:
    """根據客戶全域自訂欄位 Schema 建立搜尋列動態下拉篩選器定義與選取值"""
    filter_defs = []
    active_filters = {}
    for item in (schema or []):
        key = item.get("key")
        if not key or not item.get("show_in_list"):
            continue
        f_type = item.get("type", "text")
        label = item.get("label") or key
        param_name = f"cf_{key}"
        selected_val = request.GET.get(param_name, "").strip()

        if f_type in ("select", "multiselect"):
            opts = item.get("options") or []
            choices = [(str(opt), str(opt)) for opt in opts if str(opt).strip()]
            if choices:
                filter_defs.append({
                    "key": key,
                    "label": label,
                    "type": f_type,
                    "choices": choices,
                    "selected_value": selected_val,
                })
                if selected_val:
                    active_filters[key] = (f_type, selected_val)
        elif f_type == "boolean":
            choices = [("true", "是"), ("false", "否")]
            filter_defs.append({
                "key": key,
                "label": label,
                "type": f_type,
                "choices": choices,
                "selected_value": selected_val,
            })
            if selected_val in ("true", "false"):
                active_filters[key] = (f_type, selected_val)

    return filter_defs, active_filters, bool(active_filters)


def _match_customer_custom_filters_and_query(
    customer: Customer, query_lower: str, base_matched_ids: set, active_custom_filters: dict, schema: list
) -> bool:
    """檢查單筆客戶是否符合動態自訂欄位篩選器與關鍵字搜尋"""
    vals = customer.custom_values or {}

    # 1. 檢查動態下拉篩選器 (AND 條件)
    for key, (f_type, target_val) in active_custom_filters.items():
        raw_v = vals.get(key)
        if f_type == "boolean":
            is_true = raw_v in (True, "true", "True", 1, "1", "是")
            if target_val == "true" and not is_true:
                return False
            if target_val == "false" and is_true:
                return False
        elif f_type == "multiselect":
            if isinstance(raw_v, list):
                v_list = [str(x).strip() for x in raw_v]
            elif isinstance(raw_v, str):
                v_list = [x.strip() for x in raw_v.split(",") if x.strip()]
            else:
                v_list = []
            if target_val not in v_list:
                return False
        else:  # select
            if str(raw_v or "").strip() != target_val:
                return False

    # 2. 檢查關鍵字搜尋 (若已符合基本欄位搜尋則直接通過，否則比對自訂欄位顯示值)
    if query_lower and customer.pk not in base_matched_ids:
        specs = customer.get_custom_specs_display(schema=schema)
        matched_custom = any(
            query_lower in str(item.get("value") or "").lower()
            for item in specs
            if item.get("has_value")
        )
        if not matched_custom:
            return False

    return True


def _get_descendant_ids_for_query(query: str) -> set[int]:
    """當關鍵字命中某上層客戶的代號或名稱時，找出其底下所有直屬與間接下層客戶 ID"""
    if not query:
        return set()
    matched_parent_ids = set(
        Customer.objects.filter(
            Q(code__icontains=query) | Q(name__icontains=query)
        ).values_list("id", flat=True)
    )
    if not matched_parent_ids:
        return set()
    all_pairs = Customer.objects.exclude(parent_id__isnull=True).values_list("id", "parent_id")
    children_map: dict[int, list[int]] = {}
    for cid, pid in all_pairs:
        children_map.setdefault(pid, []).append(cid)
    descendant_ids: set[int] = set()
    stack = []
    for pid in matched_parent_ids:
        stack.extend(children_map.get(pid, []))
    while stack:
        curr_id = stack.pop()
        if curr_id not in descendant_ids:
            descendant_ids.add(curr_id)
            stack.extend(children_map.get(curr_id, []))
    return descendant_ids


def _build_customer_hierarchy_context(user, customer: Customer) -> dict:
    """
    建立客戶詳情頁的上下游關係鍊樹狀結構資料：
    - 往上：單一完整上層路徑 (最頂層代理商 -> ... -> 直屬上層 -> 目前客戶)
    - 往下：目前客戶底下的所有直屬與間接下層客戶樹狀圖 (直到最底層)
    - 嚴格遵守業務隔離視野 (SCOPE_FOLLOWED)：未關注之節點 can_access=False (僅顯示不可點擊)
    """
    ancestors_objs = customer.get_ancestors()
    ancestor_ids = [a.pk for a in ancestors_objs]
    ancestor_id_set = set(ancestor_ids)

    all_pairs = list(Customer.objects.exclude(parent_id__isnull=True).values_list("id", "parent_id"))
    children_id_map: dict[int, list[int]] = {}
    for cid, pid in all_pairs:
        children_id_map.setdefault(pid, []).append(cid)

    descendant_ids: set[int] = set()
    stack = list(children_id_map.get(customer.pk, []))
    while stack:
        curr_id = stack.pop()
        if curr_id not in descendant_ids and curr_id != customer.pk and curr_id not in ancestor_id_set:
            descendant_ids.add(curr_id)
            stack.extend(children_id_map.get(curr_id, []))

    chain_ids = ancestor_id_set | {customer.pk} | descendant_ids
    cust_qs = Customer.objects.filter(pk__in=chain_ids).select_related("parent").prefetch_related(
        "contracts", "probes", "licenses"
    )
    cust_map: dict[int, Customer] = {c.pk: c for c in cust_qs}

    is_followed_only = is_user_followed_only_scope(user)
    if is_followed_only:
        followed_ids = set(
            CustomerFollow.objects.filter(user=user, customer_id__in=chain_ids).values_list("customer_id", flat=True)
        )
    else:
        followed_ids = chain_ids

    def make_node_dict(c: Customer, level: int) -> dict:
        hw_count = sum(1 for p in c.probes.all() if not p.is_deleted)
        lic_count = len(c.licenses.all())
        return {
            "id": c.pk,
            "code": c.code,
            "name": c.name,
            "contract_status_display": c.contract_status_display,
            "contract_badge_class": c.contract_badge_class,
            "hardware_count": hw_count,
            "license_count": lic_count,
            "can_access": (not is_followed_only) or (c.pk in followed_ids),
            "is_current": c.pk == customer.pk,
            "is_ancestor": c.pk in ancestor_id_set,
            "is_direct_child": c.parent_id == customer.pk,
            "level": level,
            "children": [],
        }

    def build_Referrals_subtree(parent_pk: int, level: int, visited: set[int]) -> list[dict]:
        child_pks = children_id_map.get(parent_pk, [])
        child_objs = [cust_map[ pk ] for pk in child_pks if pk in cust_map and pk not in visited]
        child_objs.sort(key=lambda x: x.code)
        result = []
        for ch_obj in child_objs:
            new_visited = visited | {ch_obj.pk}
            node = make_node_dict(ch_obj, level)
            node["children"] = build_Referrals_subtree(ch_obj.pk, level + 1, new_visited)
            result.append(node)
        return result

    ancestors_nodes = []
    for idx, anc_Id in enumerate(ancestor_ids):
        anc_obj = cust_map.get(anc_Id)
        if anc_obj:
            ancestors_nodes.append(make_node_dict(anc_obj, idx))

    current_level = len(ancestors_nodes)
    current_obj = cust_map.get(customer.pk, customer)
    current_node = make_node_dict(current_obj, current_level)
    current_node["children"] = build_Referrals_subtree(
        customer.pk, current_level + 1, ancestor_id_set | {customer.pk}
    )

    if ancestors_nodes:
        for i in range(len(ancestors_nodes) - 1):
            ancestors_nodes[i]["children"] = [ancestors_nodes[i + 1]]
        ancestors_nodes[-1]["children"] = [current_node]
        hierarchy_root = ancestors_nodes[0]
    else:
        hierarchy_root = current_node

    parent_can_access = False
    if customer.parent_id:
        if not is_followed_only:
            parent_can_access = True
        else:
            parent_can_access = customer.parent_id in followed_ids

    return {
        "hierarchy_root": hierarchy_root,
        "ancestors_nodes": ancestors_nodes,
        "current_hierarchy_node": current_node,
        "ancestors_count": len(ancestors_nodes),
        "direct_children_count": len(current_node["children"]),
        "total_descendants_count": len(descendant_ids),
        "has_hierarchy": bool(ancestors_nodes or current_node["children"]),
        "parent_can_access": parent_can_access,
    }


@login_required
def customer_custom_fields_setting(request):
    if not (user_can_edit_customers(request.user) or user_can_manage_system(request.user)):
        messages.error(request, "權限不足：僅限客戶管理員或系統管理員設定客戶全域自訂欄位。")
        return redirect("customer-list")

    system_settings = SystemSetting.get_settings()
    if request.method == "POST":
        form = CustomerCustomFieldSchemaForm(request.POST, instance=system_settings)
        if form.is_valid():
            form.save()
            schema_count = len(system_settings.customer_custom_field_schema or [])
            log_action(
                user=request.user,
                action="更新客戶全域自訂欄位結構",
                target_type="SystemSetting",
                target_identifier="CustomerCustomFields",
                detail=f"共設定 {schema_count} 個全域客戶自訂欄位",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"已成功儲存客戶全域自訂欄位設定（共 {schema_count} 個欄位）！")
            return redirect("customer-list")
    else:
        form = CustomerCustomFieldSchemaForm(instance=system_settings)

    return render(
        request,
        "customers/customer_custom_fields.html",
        {
            "form": form,
            "title": "客戶全域自訂欄位設定",
        },
    )


@login_required
def customer_list(request):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")

    from django.utils import timezone
    query = request.GET.get("q", "").strip()
    contract_filter = request.GET.get("contract_status", "").strip()
    product_filter = request.GET.get("product", "").strip()
    hw_model_filter = request.GET.get("hw_model", "").strip()
    followed_filter = request.GET.get("followed", "").strip()
    order = request.GET.get("order", "code")

    customers = Customer.objects.select_related("parent").prefetch_related(
        "children", "contracts", "contacts", "probes", "licenses", "info_sections", "followers"
    ).all()
    customers = filter_customers_for_user(request.user, customers)

    custom_schema = SystemSetting.get_settings().customer_custom_field_schema or []
    custom_filter_defs, active_custom_filters, has_active_custom_filter = _build_customer_custom_filter_defs(
        request, custom_schema
    )

    today = timezone.localdate()
    if contract_filter == "active":
        customers = customers.filter(
            contracts__is_terminated=False,
            contracts__end_date__gte=today,
        ).filter(
            Q(contracts__start_date__isnull=True) | Q(contracts__start_date__lte=today)
        ).distinct()
    elif contract_filter == "expiring":
        in_30_days = today + datetime.timedelta(days=30)
        customers = customers.filter(
            contracts__is_terminated=False,
            contracts__end_date__gte=today,
            contracts__end_date__lte=in_30_days,
        ).distinct()
    elif contract_filter == "expired":
        customers = customers.filter(
            contracts__is_terminated=False,
            contracts__end_date__lt=today
        ).exclude(
            contracts__is_terminated=False, contracts__end_date__gte=today
        ).distinct()
    elif contract_filter == "pending":
        customers = customers.filter(
            contracts__is_terminated=False,
            contracts__start_date__isnull=False,
            contracts__start_date__gt=today,
        ).distinct()
    elif contract_filter == "terminated":
        customers = customers.filter(
            contracts__is_terminated=True
        ).distinct()
    elif contract_filter == "none":
        customers = customers.filter(contracts__isnull=True)

    if product_filter:
        customers = customers.filter(licenses__product_id=product_filter).distinct()

    if hw_model_filter:
        customers = customers.filter(probes__model_id=hw_model_filter, probes__is_deleted=False).distinct()

    if followed_filter == "1":
        customers = customers.filter(followers__user=request.user).distinct()

    if order:
        valid_orders = ["code", "-code", "name", "-name", "created_at", "-created_at"]
        if order in valid_orders:
            customers = customers.order_by(order)
        else:
            customers = customers.order_by("code")

    descendant_match_ids = _get_descendant_ids_for_query(query) if query else set()

    if query and not custom_schema:
        customers = customers.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(tax_id__icontains=query)
            | Q(contact__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
            | Q(parent__code__icontains=query)
            | Q(parent__name__icontains=query)
            | Q(pk__in=descendant_match_ids)
        )

    if active_custom_filters or (query and custom_schema):
        base_matched_ids = set()
        if query:
            base_matched_ids = set(
                customers.filter(
                    Q(code__icontains=query)
                    | Q(name__icontains=query)
                    | Q(tax_id__icontains=query)
                    | Q(contact__icontains=query)
                    | Q(phone__icontains=query)
                    | Q(email__icontains=query)
                    | Q(parent__code__icontains=query)
                    | Q(parent__name__icontains=query)
                    | Q(pk__in=descendant_match_ids)
                ).values_list("pk", flat=True)
            )
        query_lower = query.lower()
        filtered_list = []
        for c in customers:
            c._cached_customer_schema = custom_schema
            if _match_customer_custom_filters_and_query(
                c, query_lower, base_matched_ids, active_custom_filters, custom_schema
            ):
                filtered_list.append(c)
        customers = filtered_list

    paginator = Paginator(customers, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    for c in page_obj:
        c._cached_customer_schema = custom_schema

    can_edit_cust = user_can_edit_customers(request.user)
    can_edit_lic = user_can_edit_licenses(request.user)
    from apps.licenses.models import Product
    from apps.hardware.models import HardwareModel

    my_followed_ids = set(
        CustomerFollow.objects.filter(user=request.user).values_list("customer_id", flat=True)
    )

    return render(
        request,
        "customers/customer_list.html",
        {
            "page_obj": page_obj,
            "query": query,
            "contract_filter": contract_filter,
            "product_filter": product_filter,
            "hw_model_filter": hw_model_filter,
            "followed_filter": followed_filter,
            "custom_filter_defs": custom_filter_defs,
            "has_active_custom_filter": has_active_custom_filter,
            "my_followed_ids": my_followed_ids,
            "products": Product.objects.filter(is_active=True).order_by("name"),
            "hw_models": HardwareModel.objects.all().order_by("name"),
            "order": order,
            "can_edit_crm": can_edit_cust,
            "can_edit_customers": can_edit_cust,
            "can_edit_licenses": can_edit_lic,
            "total_count": paginator.count,
        },
    )


@login_required
def customer_export(request):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")

    from apps.core.excel_utils import export_to_excel

    query = request.GET.get("q", "").strip()
    contract_filter = request.GET.get("contract_status", "").strip()
    product_filter = request.GET.get("product", "").strip()
    hw_model_filter = request.GET.get("hw_model", "").strip()
    followed_filter = request.GET.get("followed", "").strip()
    order = request.GET.get("order", "code")

    customers = Customer.objects.select_related("parent").prefetch_related(
        "contracts", "contacts", "probes", "licenses", "info_sections", "followers"
    ).all()
    customers = filter_customers_for_user(request.user, customers)

    custom_schema = SystemSetting.get_settings().customer_custom_field_schema or []
    _, active_custom_filters, _ = _build_customer_custom_filter_defs(
        request, custom_schema
    )

    today = timezone.localdate()
    if contract_filter == "active":
        customers = customers.filter(
            contracts__is_terminated=False,
            contracts__end_date__gte=today,
        ).filter(
            Q(contracts__start_date__isnull=True) | Q(contracts__start_date__lte=today)
        ).distinct()
    elif contract_filter == "expiring":
        in_30_days = today + datetime.timedelta(days=30)
        customers = customers.filter(
            contracts__is_terminated=False,
            contracts__end_date__gte=today,
            contracts__end_date__lte=in_30_days,
        ).distinct()
    elif contract_filter == "expired":
        customers = customers.filter(
            contracts__is_terminated=False,
            contracts__end_date__lt=today
        ).exclude(
            contracts__is_terminated=False, contracts__end_date__gte=today
        ).distinct()
    elif contract_filter == "pending":
        customers = customers.filter(
            contracts__is_terminated=False,
            contracts__start_date__isnull=False,
            contracts__start_date__gt=today,
        ).distinct()
    elif contract_filter == "terminated":
        customers = customers.filter(
            contracts__is_terminated=True
        ).distinct()
    elif contract_filter == "none":
        customers = customers.filter(contracts__isnull=True)

    if product_filter:
        customers = customers.filter(licenses__product_id=product_filter).distinct()

    if hw_model_filter:
        customers = customers.filter(probes__model_id=hw_model_filter, probes__is_deleted=False).distinct()

    if followed_filter == "1":
        customers = customers.filter(followers__user=request.user).distinct()

    if order:
        valid_orders = ["code", "-code", "name", "-name", "created_at", "-created_at"]
        if order in valid_orders:
            customers = customers.order_by(order)
        else:
            customers = customers.order_by("code")

    descendant_match_ids = _get_descendant_ids_for_query(query) if query else set()

    if query and not custom_schema:
        customers = customers.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(tax_id__icontains=query)
            | Q(contact__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
            | Q(parent__code__icontains=query)
            | Q(parent__name__icontains=query)
            | Q(pk__in=descendant_match_ids)
        )

    if active_custom_filters or (query and custom_schema):
        base_matched_ids = set()
        if query:
            base_matched_ids = set(
                customers.filter(
                    Q(code__icontains=query)
                    | Q(name__icontains=query)
                    | Q(tax_id__icontains=query)
                    | Q(contact__icontains=query)
                    | Q(phone__icontains=query)
                    | Q(email__icontains=query)
                    | Q(parent__code__icontains=query)
                    | Q(parent__name__icontains=query)
                    | Q(pk__in=descendant_match_ids)
                ).values_list("pk", flat=True)
            )
        query_lower = query.lower()
        filtered_list = []
        for c in customers:
            c._cached_customer_schema = custom_schema
            if _match_customer_custom_filters_and_query(
                c, query_lower, base_matched_ids, active_custom_filters, custom_schema
            ):
                filtered_list.append(c)
        customers = filtered_list

    headers = [
        "客戶代號",
        "客戶名稱",
        "統一編號",
        "上層客戶代號",
        "上層客戶名稱",
        "主要聯絡人",
        "電子信箱",
        "聯絡電話",
        "聯絡地址",
        "維護合約狀態",
        "最新維護到期日",
        "自訂環境資訊區塊",
        "備註說明",
        "建立時間",
    ]

    # 收集全域自訂欄位與客戶資料中出現過的 custom_values key
    custom_field_keys = []
    custom_field_label_map = {}
    for item in custom_schema:
        k = item.get("key")
        if k and k not in custom_field_keys:
            custom_field_keys.append(k)
            custom_field_label_map[k] = item.get("label") or k

    customers_list = list(customers)
    for c in customers_list:
        vals = c.custom_values or {}
        for k in vals.keys():
            if k and k not in custom_field_keys:
                custom_field_keys.append(k)
                custom_field_label_map[k] = k

    for k in custom_field_keys:
        headers.append(f"自訂: {custom_field_label_map[k]}")

    rows = []
    for c in customers_list:
        c._cached_customer_schema = custom_schema
        sections = c.info_sections.all()
        sections_str = "\n".join([f"【{s.title}】{s.content}" for s in sections]) if sections else ""
        spec_dict = {item.get("key"): item.get("value", "") for item in c.custom_specs_display}

        row = [
            c.code,
            c.name,
            c.tax_id or "",
            c.parent.code if c.parent else "",
            c.parent.name if c.parent else "",
            c.contact or "",
            c.email or "",
            c.phone or "",
            c.address or "",
            c.contract_status_display,
            c.maintenance_expiry or "",
            sections_str,
            c.note or "",
            c.created_at,
        ]
        for k in custom_field_keys:
            row.append(spec_dict.get(k, ""))

        rows.append(row)

    return export_to_excel("customers", "客戶資料清單", headers, rows)


@login_required
def customer_import_template(request):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")
    from apps.core.excel_utils import generate_import_template_excel

    custom_schema = SystemSetting.get_settings().customer_custom_field_schema or []

    headers = [
        "客戶代號*",
        "客戶名稱*",
        "統一編號",
        "上層客戶代號",
        "主要聯絡人",
        "電子信箱",
        "聯絡電話",
        "聯絡地址",
        "備註說明",
    ]
    required_indices = {0, 1}
    dropdown_configs = []

    for item in custom_schema:
        k = item.get("key")
        if not k:
            continue
        lbl = item.get("label") or k
        is_req = bool(item.get("required", False))
        f_type = item.get("type", "text")
        col_idx_zero = len(headers)
        col_idx_one = col_idx_zero + 1
        headers.append(f"自訂: {lbl}{'*' if is_req else ''}")
        if is_req:
            required_indices.add(col_idx_zero)

        if f_type == "select":
            opts = [str(o) for o in (item.get("options") or []) if str(o).strip()]
            if opts and len(",".join(opts)) <= 240:
                dropdown_configs.append({"col_idx": col_idx_one, "options": opts})
        elif f_type == "boolean":
            dropdown_configs.append({"col_idx": col_idx_one, "options": ["是", "否"]})

    custom_sample_vals = []
    for item in custom_schema:
        if not item.get("key"):
            continue
        def_v = item.get("default", "")
        f_type = item.get("type", "text")
        if f_type == "boolean" and def_v not in ("", None):
            custom_sample_vals.append("是" if def_v in (True, "true", "True", 1, "1", "是") else "否")
        elif f_type == "select" and not def_v and item.get("options"):
            custom_sample_vals.append(str(item["options"][0]))
        elif f_type == "datetime" and not def_v:
            custom_sample_vals.append("2026-10-29 14:00")
        elif isinstance(def_v, list):
            custom_sample_vals.append(", ".join(str(x) for x in def_v))
        else:
            custom_sample_vals.append(str(def_v or ""))

    sample_rows = [
        [
            "80123456",
            "範例科技股份有限公司",
            "80123456",
            "",
            "王大明",
            "daming.wang@example.com",
            "02-2711-0000",
            "台北市信義區信義路五段7號",
            "上層經銷/代理商範例 (無上層客戶可留空)",
        ] + custom_sample_vals,
        [
            "CUST-002",
            "台灣創新系統有限公司",
            "91234567",
            "80123456",
            "陳小姐",
            "service@innov-tw.example.com",
            "03-550-1234",
            "新竹縣竹北市光明六路東一段",
            "下層客戶範例 (上層客戶代號填寫 80123456)",
        ] + custom_sample_vals,
    ]

    rule_rows = [
        ["客戶代號", "必填 (*)", "全系統唯一識別碼，可填寫公司 8 碼統一編號或自訂客戶編號。若與系統既有客戶代號相同，將自動更新該客戶資料。", "80123456 或 CUST-001"],
        ["客戶名稱", "必填 (*)", "客戶的公司全銜或機構組織名稱。", "範例科技股份有限公司"],
        ["統一編號", "選填", "公司 8 碼統一編號。若留空，更新既有客戶時會保留原值。", "80123456"],
        ["上層客戶代號", "選填", "若此客戶隸屬於某經銷商或代理商，可填寫該上層客戶的「客戶代號」（支援填寫同檔案內一併匯入的客戶代號或系統既有客戶代號）。若要解除既有客戶的上層關聯，可填寫「無」或「CLEAR」。", "80123456"],
        ["主要聯絡人", "選填", "主要窗口姓名。若有填寫，系統會自動同步建立或更新該客戶名下的「主要聯絡人」名冊紀錄。", "王大明"],
        ["電子信箱", "選填", "主要聯絡人的電子郵件格式 (需包含 @)。", "daming@example.com"],
        ["聯絡電話", "選填", "公司電話、分機或行動電話。建議設為文字格式避免前導 0 遺失。", "02-2711-0000"],
        ["聯絡地址", "選填", "公司通訊或裝機地址。", "台北市信義區信義路五段7號"],
        ["備註說明", "選填", "關於該客戶的其他補充說明。", "VIP 客戶"],
    ]

    type_label_map = {
        "text": "字串 (text)",
        "number": "數字 (number)",
        "datetime": "日期時間 (YYYY-MM-DD HH:MM)",
        "boolean": "布林 (是 / 否)",
        "select": "下拉選單 (單選)",
        "multiselect": "複選清單 (以逗號分隔)",
    }
    for item in custom_schema:
        k = item.get("key")
        if not k:
            continue
        lbl = item.get("label") or k
        f_type = item.get("type", "text")
        is_req = bool(item.get("required", False))
        opts = item.get("options") or []
        def_v = item.get("default", "")
        desc = f"型態：{type_label_map.get(f_type, f_type)}。"
        if f_type in ("select", "multiselect") and opts:
            desc += f" 允許選項：{', '.join(str(o) for o in opts)}。"
        elif f_type == "datetime":
            desc += " 請以 YYYY-MM-DD HH:MM 格式填寫（台灣時區）。"
        elif f_type == "boolean":
            desc += " 請填寫「是」或「否」（亦支援 true / false）。"
        if def_v != "":
            desc += f" 預設值：{def_v}。"
        rule_rows.append([
            f"自訂: {lbl}",
            "必填 (*)" if is_req else "選填",
            desc,
            str(def_v or (opts[0] if opts else ("2026-10-29 14:00" if f_type == "datetime" else ""))),
        ])

    guide_sections = [
        {
            "title": "一、客戶名冊匯入欄位說明與驗證規則",
            "headers": ["欄位名稱", "是否必填", "格式與填寫規則說明", "範例值"],
            "rows": rule_rows,
        },
        {
            "title": "二、匯入注意事項",
            "headers": ["項目", "詳細說明"],
            "rows": [
                ["支援檔案格式", "支援 Excel (.xlsx) 與 CSV (.csv，UTF-8 或 Big5 編碼)。您也可以直接將「匯出 Excel」下載的客戶清單修改後原檔匯入。"],
                ["重複代號處理 (Upsert)", "當檔案中的「客戶代號」已存在於系統時，會更新該客戶有填寫的非空白欄位（檔案中留空的選填欄位將保留系統原值，不會被清空）。"],
                ["錯誤回復機制 (All-or-Nothing)", "為確保資料完整性，只要檔案中有任何一列驗證失敗（例如缺必填、同檔案內代號重複、Email 或自訂欄位格式不合法），整批檔案皆不會寫入，畫面將列出所有錯誤列號供您修正後重傳。"],
            ],
        },
    ]

    return generate_import_template_excel(
        filename_prefix="customer_import_template",
        main_sheet_name="客戶匯入資料填寫",
        headers=headers,
        required_indices=required_indices,
        sample_rows=sample_rows,
        guide_sections=guide_sections,
        dropdown_configs=dropdown_configs,
    )


@login_required
def customer_import(request):
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足：您沒有匯入客戶名冊的權限。")
        return redirect("customer-list")

    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError as DjangoValidationError
    from apps.core.excel_utils import parse_uploaded_tabular_file

    error_list = []
    summary = None
    custom_schema = SystemSetting.get_settings().customer_custom_field_schema or []
    schema_dict = {item.get("key"): item for item in custom_schema if item.get("key")}
    custom_label_to_key = {}
    for item in custom_schema:
        k = item.get("key")
        lbl = item.get("label")
        if k:
            custom_label_to_key[k.lower()] = k
        if lbl and k:
            custom_label_to_key[lbl.lower()] = k

    # 欄位同義詞對應表
    header_alias_map = {
        "客戶代號": "code",
        "客戶代號/統編": "code",
        "代號": "code",
        "code": "code",
        "客戶名稱": "name",
        "公司名稱": "name",
        "名稱": "name",
        "name": "name",
        "統一編號": "tax_id",
        "統編": "tax_id",
        "tax_id": "tax_id",
        "上層客戶代號": "parent_code",
        "上層客戶": "parent_code",
        "上層代號": "parent_code",
        "經銷商代號": "parent_code",
        "代理商代號": "parent_code",
        "parent_code": "parent_code",
        "parent": "parent_code",
        "主要聯絡人": "contact",
        "聯絡人": "contact",
        "contact": "contact",
        "電子信箱": "email",
        "電子郵件": "email",
        "email": "email",
        "聯絡電話": "phone",
        "電話": "phone",
        "phone": "phone",
        "聯絡地址": "address",
        "地址": "address",
        "address": "address",
        "備註說明": "note",
        "備註": "note",
        "note": "note",
    }

    if request.method == "POST":
        form = CustomerImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["file"]
            try:
                raw_headers, data_rows = parse_uploaded_tabular_file(uploaded_file)
            except ValueError as e:
                error_list.append({"row": "-", "code": "-", "message": str(e)})
                raw_headers, data_rows = [], []

            if raw_headers and not error_list:
                # 建立欄位索引對應
                col_map = {}
                custom_col_map = {}  # col_idx -> custom_field_key
                for idx, h in enumerate(raw_headers):
                    h_clean = h.strip().rstrip("*").strip()
                    h_lower = h_clean.lower()
                    if h_clean.startswith("自訂:") or h_clean.startswith("自訂："):
                        c_label = h_clean.split(":", 1)[-1].split("：", 1)[-1].strip().rstrip("*").strip()
                        c_key = custom_label_to_key.get(c_label.lower(), c_label)
                        custom_col_map[idx] = c_key
                    else:
                        key = header_alias_map.get(h_lower, header_alias_map.get(h_clean))
                        if key and key not in col_map:
                            col_map[key] = idx
                        elif h_lower in custom_label_to_key:
                            custom_col_map[idx] = custom_label_to_key[h_lower]

                missing_cols = []
                if "code" not in col_map:
                    missing_cols.append("客戶代號")
                if "name" not in col_map:
                    missing_cols.append("客戶名稱")

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
                    existing_codes_map = {
                        c.code.upper(): c for c in Customer.objects.select_related("parent").all()
                    }
                    all_file_codes = set()
                    code_col_idx = col_map.get("code")
                    if code_col_idx is not None:
                        for _, r_vals in data_rows:
                            if code_col_idx < len(r_vals):
                                c_str = r_vals[code_col_idx].strip()
                                if c_str:
                                    all_file_codes.add(c_str.upper())

                    for row_num, row_vals in data_rows:
                        def get_val(field_key):
                            c_idx = col_map.get(field_key)
                            if c_idx is not None and c_idx < len(row_vals):
                                return row_vals[c_idx].strip()
                            return ""

                        code = get_val("code")
                        name = get_val("name")
                        tax_id = get_val("tax_id")
                        raw_parent_code = get_val("parent_code")
                        contact = get_val("contact")
                        email = get_val("email")
                        phone = get_val("phone")
                        address = get_val("address")
                        note = get_val("note")

                        row_errors = []
                        if not code:
                            row_errors.append("「客戶代號」為必填欄位，不可為空白")
                        else:
                            code_upper = code.upper()
                            if code_upper in seen_codes_in_file:
                                prev_row = seen_codes_in_file[code_upper]
                                row_errors.append(f"客戶代號「{code}」在檔案中重複出現（與第 {prev_row} 列重複）")
                            else:
                                seen_codes_in_file[code_upper] = row_num

                        if not name:
                            row_errors.append("「客戶名稱」為必填欄位，不可為空白")

                        clear_parent = False
                        parent_code = ""
                        if raw_parent_code:
                            if raw_parent_code.upper() in ("無", "NONE", "CLEAR", "NULL", "-"):
                                clear_parent = True
                            else:
                                parent_code = raw_parent_code
                                p_upper = parent_code.upper()
                                if code and p_upper == code.upper():
                                    row_errors.append("「上層客戶代號」不可指向自己")
                                elif p_upper not in all_file_codes and p_upper not in existing_codes_map:
                                    row_errors.append(
                                        f"找不到指定的上層客戶代號：「{parent_code}」（系統與本次匯入檔案中皆不存在此代號）"
                                    )

                        if email:
                            try:
                                validate_email(email)
                            except DjangoValidationError:
                                row_errors.append(f"電子信箱格式不正確：「{email}」")

                        # 解析與檢核自訂擴充欄位
                        custom_data = {}
                        provided_custom_keys = set()
                        for c_idx, c_key in custom_col_map.items():
                            raw_c_val = row_vals[c_idx].strip() if c_idx < len(row_vals) else ""
                            schema_item = schema_dict.get(c_key)
                            if not schema_item:
                                if raw_c_val != "":
                                    custom_data[c_key] = raw_c_val
                                    provided_custom_keys.add(c_key)
                                continue

                            f_type = schema_item.get("type", "text")
                            f_label = schema_item.get("label", c_key)
                            if raw_c_val == "":
                                continue

                            provided_custom_keys.add(c_key)
                            if f_type == "number":
                                try:
                                    num_v = float(raw_c_val)
                                    custom_data[c_key] = int(num_v) if num_v.is_integer() else num_v
                                except ValueError:
                                    row_errors.append(f"自訂欄位「{f_label}」必須為數值（目前填入：{raw_c_val}）")
                            elif f_type == "boolean":
                                if raw_c_val.lower() in ("是", "true", "1", "yes", "y"):
                                    custom_data[c_key] = True
                                elif raw_c_val.lower() in ("否", "false", "0", "no", "n"):
                                    custom_data[c_key] = False
                                else:
                                    row_errors.append(f"自訂欄位「{f_label}」必須為「是」或「否」（目前填入：{raw_c_val}）")
                            elif f_type == "datetime":
                                try:
                                    custom_data[c_key] = _parse_custom_datetime_str(raw_c_val)
                                except ValueError as e:
                                    row_errors.append(f"自訂欄位「{f_label}」錯誤：{e}")
                            elif f_type == "select":
                                opts = [str(o) for o in (schema_item.get("options") or [])]
                                if opts and raw_c_val not in opts:
                                    row_errors.append(f"自訂欄位「{f_label}」的值「{raw_c_val}」不在允許選項內（允許值：{', '.join(opts)}）")
                                else:
                                    custom_data[c_key] = raw_c_val
                            elif f_type == "multiselect":
                                opts = [str(o) for o in (schema_item.get("options") or [])]
                                parts = [p.strip() for p in raw_c_val.split(",") if p.strip()]
                                invalid_parts = [p for p in parts if opts and p not in opts]
                                if invalid_parts:
                                    row_errors.append(f"自訂欄位「{f_label}」包含不允許的選項「{', '.join(invalid_parts)}」（允許值：{', '.join(opts)}）")
                                else:
                                    custom_data[c_key] = parts
                            else:
                                custom_data[c_key] = raw_c_val

                        # 檢查必填自訂欄位與新客戶預設值
                        existing_cust = existing_codes_map.get(code.upper()) if code else None
                        for item in custom_schema:
                            c_key = item.get("key")
                            if not c_key:
                                continue
                            f_label = item.get("label", c_key)
                            f_type = item.get("type", "text")
                            is_req = bool(item.get("required", False))
                            def_v = item.get("default", "")

                            if c_key not in provided_custom_keys:
                                if existing_cust:
                                    old_v = (existing_cust.custom_values or {}).get(c_key)
                                    if is_req and old_v in (None, "", []):
                                        row_errors.append(f"自訂必填欄位「{f_label}」不可為空白")
                                else:
                                    # 新增客戶：若有設預設值則套用預設值
                                    if def_v not in (None, "", []):
                                        if f_type == "boolean":
                                            custom_data[c_key] = def_v in (True, "true", "True", 1, "1", "是")
                                        elif f_type == "multiselect" and isinstance(def_v, str):
                                            custom_data[c_key] = [p.strip() for p in def_v.split(",") if p.strip()]
                                        elif f_type == "number":
                                            try:
                                                num_d = float(def_v)
                                                custom_data[c_key] = int(num_d) if num_d.is_integer() else num_d
                                            except ValueError:
                                                custom_data[c_key] = def_v
                                        else:
                                            custom_data[c_key] = def_v
                                    elif is_req:
                                        row_errors.append(f"自訂必填欄位「{f_label}」不可為空白")

                        if row_errors:
                            error_list.append({
                                "row": f"第 {row_num} 列",
                                "code": code or "(未填代號)",
                                "message": "；".join(row_errors),
                            })
                        else:
                            parsed_records.append({
                                "row_num": row_num,
                                "code": code,
                                "name": name,
                                "tax_id": tax_id,
                                "parent_code": parent_code,
                                "clear_parent": clear_parent,
                                "contact": contact,
                                "email": email,
                                "phone": phone,
                                "address": address,
                                "note": note,
                                "custom_data": custom_data,
                            })

                    # 第二階段檢核：檢查結合系統既有關係與本次檔案設定後，是否會產生循環指向
                    if not error_list and parsed_records:
                        combined_parent_map: dict[str, str | None] = {
                            c_code: (c_obj.parent.code.upper() if c_obj.parent else None)
                            for c_code, c_obj in existing_codes_map.items()
                        }
                        for rec in parsed_records:
                            c_upper = rec["code"].upper()
                            if rec["clear_parent"]:
                                combined_parent_map[c_upper] = None
                            elif rec["parent_code"]:
                                combined_parent_map[c_upper] = rec["parent_code"].upper()
                            elif c_upper not in combined_parent_map:
                                combined_parent_map[c_upper] = None

                        for rec in parsed_records:
                            if not rec["parent_code"]:
                                continue
                            c_upper = rec["code"].upper()
                            visited_codes = {c_upper}
                            curr_p = combined_parent_map.get(c_upper)
                            while curr_p:
                                if curr_p in visited_codes:
                                    error_list.append({
                                        "row": f"第 {rec['row_num']} 列",
                                        "code": rec["code"],
                                        "message": f"上層客戶代號「{rec['parent_code']}」會造成上下游循環指向，請檢查關係鍊設定",
                                    })
                                    break
                                visited_codes.add(curr_p)
                                curr_p = combined_parent_map.get(curr_p)

                    if not error_list and parsed_records:
                        created_count = 0
                        updated_count = 0

                        with transaction.atomic():
                            # 預先載入現有客戶 (以不分大小寫代號對應)
                            existing_customers = {c.code.upper(): c for c in Customer.objects.all()}

                            for rec in parsed_records:
                                code_key = rec["code"].upper()
                                cust = existing_customers.get(code_key)

                                if cust:
                                    # 更新既有客戶（檔案有填寫的非空白欄位才覆蓋，保留原值）
                                    cust.name = rec["name"]
                                    if rec["tax_id"]:
                                        cust.tax_id = rec["tax_id"]
                                    if rec["contact"]:
                                        cust.contact = rec["contact"]
                                    if rec["email"]:
                                        cust.email = rec["email"]
                                    if rec["phone"]:
                                        cust.phone = rec["phone"]
                                    if rec["address"]:
                                        cust.address = rec["address"]
                                    if rec["note"]:
                                        cust.note = rec["note"]
                                    if rec.get("custom_data"):
                                        merged_custom = dict(cust.custom_values or {})
                                        merged_custom.update(rec["custom_data"])
                                        cust.custom_values = merged_custom
                                    cust.save()
                                    updated_count += 1
                                else:
                                    cust = Customer.objects.create(
                                        code=rec["code"],
                                        name=rec["name"],
                                        tax_id=rec["tax_id"],
                                        contact=rec["contact"],
                                        email=rec["email"],
                                        phone=rec["phone"],
                                        address=rec["address"],
                                        note=rec["note"],
                                        custom_values=rec.get("custom_data") or {},
                                    )
                                    CustomerFollow.objects.get_or_create(customer=cust, user=request.user)
                                    existing_customers[code_key] = cust
                                    created_count += 1

                                # 若有填寫主要聯絡人姓名，自動同步建立或更新 ContactPerson
                                if rec["contact"]:
                                    cp = ContactPerson.objects.filter(
                                        customer=cust, name=rec["contact"]
                                    ).first()
                                    if cp:
                                        if rec["phone"]:
                                            cp.phone = rec["phone"]
                                        if rec["email"]:
                                            cp.email = rec["email"]
                                        cp.is_primary = True
                                        cp.save()
                                    else:
                                        ContactPerson.objects.create(
                                            customer=cust,
                                            name=rec["contact"],
                                            phone=rec["phone"] or cust.phone,
                                            email=rec["email"] or cust.email,
                                            is_primary=True,
                                            note="由客戶匯入自動建立",
                                        )

                            # 第二階段：統一綁定或解除上層客戶關聯
                            for rec in parsed_records:
                                code_key = rec["code"].upper()
                                cust = existing_customers.get(code_key)
                                if not cust:
                                    continue
                                if rec["clear_parent"]:
                                    if cust.parent_id is not None:
                                        cust.parent = None
                                        cust.save(update_fields=["parent", "updated_at"])
                                elif rec["parent_code"]:
                                    target_parent = existing_customers.get(rec["parent_code"].upper())
                                    if target_parent and cust.parent_id != target_parent.pk:
                                        cust.parent = target_parent
                                        cust.save(update_fields=["parent", "updated_at"])

                            log_action(
                                user=request.user,
                                action="批次匯入客戶名冊",
                                target_type="Customer",
                                target_identifier=uploaded_file.name,
                                detail=f"新增 {created_count} 筆，更新 {updated_count} 筆 (共 {len(parsed_records)} 筆)",
                                ip_address=request.META.get("REMOTE_ADDR"),
                            )

                        messages.success(
                            request,
                            f"客戶名冊匯入成功！共處理 {len(parsed_records)} 筆資料（新增 {created_count} 筆、更新 {updated_count} 筆）。",
                        )
                        return redirect("customer-list")
    else:
        form = CustomerImportForm()

    return render(
        request,
        "customers/customer_import.html",
        {
            "form": form,
            "error_list": error_list,
            "summary": summary,
            "title": "匯入客戶名冊",
        },
    )



@login_required
def customer_detail(request, pk):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")

    customer = get_object_or_404(
        Customer.objects.select_related("parent").prefetch_related(
            "contacts",
            "info_sections__updated_by",
            "contracts__licenses__product",
            "contracts__probes__model",
            "probes__model",
            "probes__maintenance_contracts",
            "licenses__product",
            "licenses__maintenance_contracts",
            "followers__user__profile",
        ),
        pk=pk,
    )
    if not user_can_access_customer(request.user, customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")

    can_edit_cust = user_can_edit_customers(request.user)
    can_edit_lic = user_can_edit_licenses(request.user)
    can_manage_sys = user_can_manage_system(request.user)
    contracts = customer.contracts.all()
    contacts = customer.contacts.all()
    info_sections = customer.info_sections.all()
    probes = customer.hardware_devices.filter(is_deleted=False)
    licenses = customer.licenses.all()
    from apps.orders.models import Order
    customer_orders = list(
        Order.objects.filter(customer=customer)
        .select_related("salesperson")
        .prefetch_related("payments")
        .order_by("-created_at")
    )
    from .models import CustomerServiceRecord
    service_records = list(
        CustomerServiceRecord.objects.filter(customer=customer)
        .select_related("assigned_to", "source_order", "created_by")
        .prefetch_related("attachments")
        .order_by("-service_date", "-created_at", "-id")
    )
    from apps.core.permissions import user_can_edit_orders, user_can_ship_orders
    can_edit_services = (
        can_edit_cust
        or user_can_edit_orders(request.user)
        or user_can_ship_orders(request.user)
        or can_manage_sys
    )

    hierarchy_ctx = _build_customer_hierarchy_context(request.user, customer)
    bindable_customers = []
    if can_edit_cust:
        exclude_ids = {a["id"] for a in hierarchy_ctx["ancestors_nodes"]} | {customer.pk}
        bindable_customers = list(
            filter_customers_for_user(
                request.user, Customer.objects.select_related("parent").all()
            )
            .exclude(pk__in=exclude_ids)
            .exclude(parent_id=customer.pk)
            .order_by("code")
        )

    # 動態分頁載入客戶執行軌跡 (初始先載入前 10 筆，降低初次頁面渲染負擔)
    activity_paginator = Paginator(
        customer.activity_logs.select_related("created_by", "contact")
        .prefetch_related("attachments")
        .order_by("-created_at", "-id"),
        10,
    )
    initial_activity_page = activity_paginator.get_page(1)
    activity_total_count = activity_paginator.count
    activity_has_next = initial_activity_page.has_next()
    activity_next_page = initial_activity_page.next_page_number() if activity_has_next else None

    activity_form = CustomerActivityLogForm(customer=customer)
    info_section_form = CustomerInfoSectionForm()

    # 關注資訊
    follower_records = list(customer.followers.select_related("user", "user__profile").all())
    follower_user_ids = {f.user_id for f in follower_records}
    is_following = request.user.id in follower_user_ids

    non_follower_users = []
    if can_manage_sys:
        non_follower_users = list(
            User.objects.filter(is_active=True)
            .exclude(id__in=follower_user_ids)
            .select_related("profile")
            .order_by("username")
        )

    # 收集此客戶軌跡中出現過的所有留言人供前端篩選
    authors_map = {}
    author_users = (
        User.objects.filter(customer_activity_logs__customer_id=customer.pk)
        .distinct()
        .order_by("username")
    )
    for u in author_users:
        authors_map[u.username] = u.get_full_name() or u.username

    return render(
        request,
        "customers/customer_detail.html",
        {
            "customer": customer,
            "contracts": contracts,
            "contacts": contacts,
            "info_sections": info_sections,
            "probes": probes,
            "licenses": licenses,
            "customer_orders": customer_orders,
            "service_records": service_records,
            "can_edit_services": can_edit_services,
            "activity_logs": initial_activity_page,
            "activity_total_count": activity_total_count,
            "activity_has_next": activity_has_next,
            "activity_next_page": activity_next_page,
            "activity_authors": authors_map,
            "activity_form": activity_form,
            "info_section_form": info_section_form,
            "can_edit_crm": can_edit_cust,
            "can_edit_customers": can_edit_cust,
            "can_edit_licenses": can_edit_lic,
            "can_manage_system": can_manage_sys,
            "follower_records": follower_records,
            "followers_count": len(follower_records),
            "is_following": is_following,
            "non_follower_users": non_follower_users,
            "bindable_customers": bindable_customers,
            **hierarchy_ctx,
        },
    )


@login_required
def customer_create(request):
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足：您沒有新增客戶的權限。")
        return redirect("customer-list")

    preset_parent = None
    parent_param = request.GET.get("parent", "").strip()
    if parent_param.isdigit():
        candidate_parent = Customer.objects.filter(pk=int(parent_param)).first()
        if candidate_parent and user_can_access_customer(request.user, candidate_parent):
            preset_parent = candidate_parent

    if request.method == "POST":
        form = CustomerForm(request.POST, user=request.user)
        if form.is_valid():
            customer = form.save()
            # 建立者自動關注此客戶（支援負責範圍模式）
            CustomerFollow.objects.get_or_create(customer=customer, user=request.user)
            # 若填寫了主要聯絡人姓名，自動建一筆 ContactPerson
            if customer.contact:
                ContactPerson.objects.create(
                    customer=customer,
                    name=customer.contact,
                    phone=customer.phone,
                    email=customer.email,
                    is_primary=True,
                    note="由客戶主檔自動建立",
                )
            log_action(
                user=request.user,
                action="新增客戶",
                target_type="Customer",
                target_identifier=f"[{customer.code}] {customer.name}",
                detail=f"上層客戶：{customer.parent}" if customer.parent else "",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"成功新增客戶「{customer.name} ({customer.code})」")
            return redirect("customer-detail", pk=customer.pk)
    else:
        initial = {}
        if preset_parent:
            initial["parent"] = preset_parent.pk
        form = CustomerForm(initial=initial, user=request.user)

    return render(
        request,
        "customers/customer_form.html",
        {
            "form": form,
            "title": "新增客戶",
            "preset_parent": preset_parent,
        },
    )


@login_required
def customer_update(request, pk):
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足：您沒有編輯客戶的權限。")
        return redirect("customer-detail", pk=pk)

    customer = get_object_or_404(Customer, pk=pk)
    if not user_can_access_customer(request.user, customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer, user=request.user)
        if form.is_valid():
            customer = form.save()
            log_action(
                user=request.user,
                action="修改客戶",
                target_type="Customer",
                target_identifier=f"[{customer.code}] {customer.name}",
                detail=f"上層客戶：{customer.parent}" if customer.parent else "無上層客戶",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            notify_customer_followers(
                customer=customer,
                actor=request.user,
                event_category="data_change",
                event_title="更新客戶基本資料",
                event_detail=f"客戶名稱：{customer.name}\n上層客戶：{customer.parent or '無'}\n統一編號：{customer.tax_id or '未填'}\n聯絡電話：{customer.phone or '未填'}\n電子信箱：{customer.email or '未填'}\n地址：{customer.address or '未填'}",
                anchor="#card-basic-info",
            )
            messages.success(request, f"客戶「{customer.name}」已更新。")
            return redirect("customer-detail", pk=customer.pk)
    else:
        form = CustomerForm(instance=customer, user=request.user)

    return render(
        request,
        "customers/customer_form.html",
        {"form": form, "customer": customer, "title": f"編輯客戶 - {customer.name}"},
    )


@login_required
def customer_delete(request, pk):
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足：您沒有刪除客戶的權限。")
        return redirect("customer-list")

    customer = get_object_or_404(Customer, pk=pk)
    if not user_can_access_customer(request.user, customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")
    children_list = list(customer.children.all().order_by("code"))
    if request.method == "POST":
        name = customer.name
        code = customer.code
        customer.delete()
        log_action(
            user=request.user,
            action="刪除客戶",
            target_type="Customer",
            target_identifier=f"[{code}] {name}",
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"已刪除客戶「{name}」")
        return redirect("customer-list")

    return render(
        request,
        "customers/customer_confirm_delete.html",
        {"customer": customer, "children_list": children_list},
    )


# Contact Person Views
@login_required
def contact_create(request, customer_pk):
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足。")
        return redirect("customer-detail", pk=customer_pk)

    customer = get_object_or_404(Customer, pk=customer_pk)
    if not user_can_access_customer(request.user, customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")
    if request.method == "POST":
        form = ContactPersonForm(request.POST)
        if form.is_valid():
            contact = form.save(commit=False)
            contact.customer = customer
            contact.save()
            notify_customer_followers(
                customer=customer,
                actor=request.user,
                event_category="data_change",
                event_title=f"新增聯絡窗口：{contact.name}",
                event_detail=f"姓名：{contact.name}\n職稱/部門：{contact.title or '-'}\n電話：{contact.phone or '-'}\nEmail：{contact.email or '-'}",
                anchor="#card-contacts",
            )
            messages.success(request, f"已為 {customer.name} 新增聯絡人「{contact.name}」")
            return redirect("customer-detail", pk=customer.pk)
    else:
        form = ContactPersonForm(initial={"customer": customer})

    return render(
        request,
        "customers/contact_form.html",
        {"form": form, "customer": customer, "title": f"新增聯絡人 - {customer.name}"},
    )


@login_required
def contact_update(request, pk):
    contact = get_object_or_404(ContactPerson, pk=pk)
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足。")
        return redirect("customer-detail", pk=contact.customer_id)
    if not user_can_access_customer(request.user, contact.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")

    if request.method == "POST":
        form = ContactPersonForm(request.POST, instance=contact)
        if form.is_valid():
            form.save()
            notify_customer_followers(
                customer=contact.customer,
                actor=request.user,
                event_category="data_change",
                event_title=f"修改聯絡窗口：{contact.name}",
                event_detail=f"姓名：{contact.name}\n職稱/部門：{contact.title or '-'}\n電話：{contact.phone or '-'}\nEmail：{contact.email or '-'}",
                anchor="#card-contacts",
            )
            messages.success(request, f"聯絡人「{contact.name}」已更新。")
            return redirect("customer-detail", pk=contact.customer_id)
    else:
        form = ContactPersonForm(instance=contact)

    return render(
        request,
        "customers/contact_form.html",
        {"form": form, "customer": contact.customer, "title": f"編輯聯絡人 - {contact.name}"},
    )


@login_required
def contact_delete(request, pk):
    contact = get_object_or_404(ContactPerson, pk=pk)
    cust_id = contact.customer_id
    customer_obj = contact.customer
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足。")
        return redirect("customer-detail", pk=cust_id)
    if not user_can_access_customer(request.user, customer_obj):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")

    if request.method == "POST":
        name = contact.name
        contact.delete()
        notify_customer_followers(
            customer=customer_obj,
            actor=request.user,
            event_category="data_change",
            event_title=f"刪除聯絡窗口：{name}",
            event_detail=f"已從客戶聯絡人名冊中移除窗口「{name}」",
            anchor="#card-contacts",
        )
        messages.success(request, f"已刪除聯絡人「{name}」")
        return redirect("customer-detail", pk=cust_id)

    return render(
        request,
        "customers/contact_confirm_delete.html",
        {"contact": contact},
    )


# Maintenance Contracts Views
def _snapshot_contract_core_fields(ct: MaintenanceContract, license_ids=None, probe_ids=None) -> dict:
    lic_set = set(license_ids) if license_ids is not None else set(ct.licenses.values_list("id", flat=True))
    prb_set = set(probe_ids) if probe_ids is not None else set(ct.probes.values_list("id", flat=True))
    return {
        "customer_id": ct.customer_id,
        "contract_no": ct.contract_no,
        "name": (ct.name or "").strip(),
        "start_date": ct.start_date,
        "end_date": ct.end_date,
        "is_terminated": bool(ct.is_terminated),
        "amount": ct.amount,
        "sla_level": (ct.sla_level or "").strip(),
        "license_ids": lic_set,
        "probe_ids": prb_set,
    }


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


@login_required
def contract_list(request):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")

    filter_type = request.GET.get("filter", "all")
    approval_filter = request.GET.get("approval", "").strip()
    search = request.GET.get("search", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    base_scoped_contracts = filter_by_customer_scope(
        request.user,
        MaintenanceContract.objects.select_related("customer", "created_by", "reviewed_by")
        .prefetch_related("licenses__product", "probes__model")
        .all(),
    )
    pending_approval_count = base_scoped_contracts.filter(
        approval_status=MaintenanceContract.APPROVAL_PENDING
    ).count()
    rejected_approval_count = base_scoped_contracts.filter(
        approval_status=MaintenanceContract.APPROVAL_REJECTED
    ).count()

    contracts = base_scoped_contracts

    today = timezone.localdate()
    in_30_days = today + datetime.timedelta(days=30)

    if approval_filter in (
        MaintenanceContract.APPROVAL_PENDING,
        MaintenanceContract.APPROVAL_REJECTED,
        MaintenanceContract.APPROVAL_APPROVED,
    ):
        contracts = contracts.filter(approval_status=approval_filter)

    if filter_type == "approval_pending":
        contracts = contracts.filter(approval_status=MaintenanceContract.APPROVAL_PENDING)
    elif filter_type == "approval_rejected":
        contracts = contracts.filter(approval_status=MaintenanceContract.APPROVAL_REJECTED)
    elif filter_type == "expiring_soon":
        contracts = contracts.filter(
            approval_status=MaintenanceContract.APPROVAL_APPROVED,
            is_terminated=False,
            end_date__gte=today,
            end_date__lte=in_30_days,
        )
    elif filter_type == "expired":
        contracts = contracts.filter(
            approval_status=MaintenanceContract.APPROVAL_APPROVED,
            is_terminated=False,
            end_date__lt=today,
        )
    elif filter_type == "active":
        contracts = (
            contracts.filter(
                approval_status=MaintenanceContract.APPROVAL_APPROVED,
                is_terminated=False,
                end_date__gte=today,
            )
            .filter(Q(start_date__isnull=True) | Q(start_date__lte=today))
        )
    elif filter_type == "pending":
        contracts = contracts.filter(
            approval_status=MaintenanceContract.APPROVAL_APPROVED,
            is_terminated=False,
            start_date__isnull=False,
            start_date__gt=today,
        )
    elif filter_type == "terminated":
        contracts = contracts.filter(
            approval_status=MaintenanceContract.APPROVAL_APPROVED,
            is_terminated=True,
        )

    if search:
        contracts = contracts.filter(
            Q(contract_no__icontains=search)
            | Q(name__icontains=search)
            | Q(customer__name__icontains=search)
            | Q(customer__code__icontains=search)
        )

    if date_from:
        contracts = contracts.filter(Q(end_date__gte=date_from) | Q(end_date__isnull=True))
    if date_to:
        contracts = contracts.filter(Q(start_date__lte=date_to) | Q(start_date__isnull=True))

    paginator = Paginator(contracts, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    can_edit_lic = user_can_edit_licenses(request.user)
    can_approve_con = user_can_approve_contracts(request.user)
    return render(
        request,
        "customers/contract_list.html",
        {
            "page_obj": page_obj,
            "filter_type": filter_type,
            "approval_filter": approval_filter,
            "pending_approval_count": pending_approval_count,
            "rejected_approval_count": rejected_approval_count,
            "search": search,
            "date_from": date_from,
            "date_to": date_to,
            "can_edit_crm": can_edit_lic,
            "can_edit_licenses": can_edit_lic,
            "can_edit_contracts": can_edit_lic,
            "can_approve_contracts": can_approve_con,
            "today": today,
        },
    )


@login_required
def contract_create(request, customer_pk=None):
    if not user_can_edit_licenses(request.user):
        messages.error(request, "權限不足：您沒有開立維護合約的權限。")
        return redirect("contract-list")

    customer = None
    if customer_pk:
        customer = get_object_or_404(Customer, pk=customer_pk)
        if not user_can_access_customer(request.user, customer):
            messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
            return redirect("contract-list")

    requires_approval = is_warranty_approval_required_for_user(request.user)

    if request.method == "POST":
        form = MaintenanceContractForm(request.POST, customer=customer)
        if not customer:
            form.fields["customer"].queryset = filter_customers_for_user(request.user, Customer.objects.all())
        if form.is_valid():
            contract = form.save(commit=False)
            contract.created_by = request.user
            if requires_approval:
                contract.approval_status = MaintenanceContract.APPROVAL_PENDING
                contract.reviewed_by = None
                contract.reviewed_at = None
                contract.rejection_reason = ""
            else:
                contract.approval_status = MaintenanceContract.APPROVAL_APPROVED
                contract.reviewed_by = request.user
                contract.reviewed_at = timezone.now()
                contract.rejection_reason = ""
            contract.save()
            form.save_m2m()

            files = request.FILES.getlist("attachments")
            for f in files:
                ContractAttachment.objects.create(
                    contract=contract,
                    file=f,
                    filename=f.name,
                    file_size=f.size,
                    uploaded_by=request.user,
                )

            summary_detail = (
                f"合約編號：{contract.contract_no}\n"
                f"合約名稱：{contract.name or '-'}\n"
                f"期間：{contract.start_date or '-'} ~ {contract.end_date or '-'}\n"
                f"狀態：{contract.status_display}"
            )

            if requires_approval:
                log_action(
                    user=request.user,
                    action="送出維護保固合約簽核申請",
                    target_type="Contract",
                    target_identifier=f"{contract.contract_no} ({contract.customer.name})",
                    detail="待具備保固覆核權限人員審核",
                    ip_address=_client_ip(request),
                )
                notify_approval_submitted(
                    module_type="warranty",
                    customer=contract.customer,
                    actor=request.user,
                    target_identifier=contract.contract_no,
                    event_detail=summary_detail,
                    review_path=reverse("contract-update", kwargs={"pk": contract.pk}),
                )
                messages.warning(
                    request,
                    f"已送出維護保固合約「{contract.contract_no}」簽核申請！目前為「待覆核」狀態，待主管核准後即正式成立。",
                )
            else:
                log_action(
                    user=request.user,
                    action="新增維護合約",
                    target_type="Contract",
                    target_identifier=f"{contract.contract_no} ({contract.customer.name})",
                    ip_address=_client_ip(request),
                )
                notify_customer_followers(
                    customer=contract.customer,
                    actor=request.user,
                    event_category="data_change",
                    event_title=f"新增維護合約：{contract.contract_no}",
                    event_detail=summary_detail,
                    anchor="#card-contracts",
                )
                messages.success(request, f"成功新增合約「{contract.contract_no}」")

            if customer:
                return redirect("customer-detail", pk=customer.pk)
            return redirect("contract-update", pk=contract.pk)
    else:
        from apps.core.numbering import generate_contract_no

        initial = {"contract_no": generate_contract_no()}
        if customer:
            initial["customer"] = customer
        form = MaintenanceContractForm(initial=initial, customer=customer)
        if not customer:
            form.fields["customer"].queryset = filter_customers_for_user(request.user, Customer.objects.all())

    return render(
        request,
        "customers/contract_form.html",
        {
            "form": form,
            "customer": customer,
            "title": "新增維護合約",
            "requires_approval": requires_approval,
        },
    )


@login_required
def contract_update(request, pk):
    contract = get_object_or_404(
        MaintenanceContract.objects.select_related("customer", "created_by", "reviewed_by").prefetch_related(
            "licenses__product",
            "probes__model",
            "attachments__uploaded_by",
            "replacement_logs__performed_by",
            "replacement_logs__old_probe",
            "replacement_logs__new_probe",
            "replacement_logs__transferred_license__product",
        ),
        pk=pk,
    )
    can_edit_lic = user_can_edit_licenses(request.user)
    can_approve_con = user_can_approve_contracts(request.user)
    if not can_edit_lic and not can_approve_con:
        messages.error(request, "權限不足：您沒有編輯維護合約的權限。")
        return redirect("contract-list")
    if not user_can_access_customer(request.user, contract.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("contract-list")

    old_core = _snapshot_contract_core_fields(contract)
    old_approval_status = contract.approval_status
    requires_approval = is_warranty_approval_required_for_user(request.user)

    if request.method == "POST":
        if not can_edit_lic and not can_approve_con:
            messages.error(request, "權限不足。")
            return redirect("contract-list")
        form = MaintenanceContractForm(request.POST, instance=contract, customer=contract.customer)
        if form.is_valid():
            updated_ct = form.save(commit=False)
            new_lic_ids = [obj.id for obj in form.cleaned_data.get("licenses", [])]
            new_probe_ids = [obj.id for obj in form.cleaned_data.get("probes", [])]
            new_core = _snapshot_contract_core_fields(updated_ct, license_ids=new_lic_ids, probe_ids=new_probe_ids)
            core_changed = old_core != new_core

            resubmitted_for_approval = False
            if not requires_approval:
                if old_approval_status == MaintenanceContract.APPROVAL_REJECTED or (
                    old_approval_status == MaintenanceContract.APPROVAL_PENDING and can_approve_con
                ):
                    updated_ct.approval_status = MaintenanceContract.APPROVAL_APPROVED
                    updated_ct.reviewed_by = request.user
                    updated_ct.reviewed_at = timezone.now()
                    updated_ct.rejection_reason = ""
            else:
                if old_approval_status == MaintenanceContract.APPROVAL_REJECTED or (
                    old_approval_status == MaintenanceContract.APPROVAL_APPROVED and core_changed
                ):
                    updated_ct.approval_status = MaintenanceContract.APPROVAL_PENDING
                    updated_ct.created_by = request.user
                    updated_ct.reviewed_by = None
                    updated_ct.reviewed_at = None
                    updated_ct.rejection_reason = ""
                    resubmitted_for_approval = True

            updated_ct.save()
            form.save_m2m()

            files = request.FILES.getlist("attachments")
            for f in files:
                ContractAttachment.objects.create(
                    contract=updated_ct,
                    file=f,
                    filename=f.name,
                    file_size=f.size,
                    uploaded_by=request.user,
                )

            summary_detail = (
                f"合約編號：{updated_ct.contract_no}\n"
                f"合約名稱：{updated_ct.name or '-'}\n"
                f"期間：{updated_ct.start_date or '-'} ~ {updated_ct.end_date or '-'}\n"
                f"狀態：{updated_ct.status_display}"
            )

            if resubmitted_for_approval:
                log_action(
                    user=request.user,
                    action="修改維護保固合約並重送覆核",
                    target_type="Contract",
                    target_identifier=f"{updated_ct.contract_no} ({updated_ct.customer.name})",
                    detail="核心條件變更或退回重送，轉為待覆核",
                    ip_address=_client_ip(request),
                )
                notify_approval_submitted(
                    module_type="warranty",
                    customer=updated_ct.customer,
                    actor=request.user,
                    target_identifier=updated_ct.contract_no,
                    event_detail=summary_detail,
                    review_path=reverse("contract-update", kwargs={"pk": updated_ct.pk}),
                )
                messages.warning(
                    request,
                    f"已更新維護合約「{updated_ct.contract_no}」並重新送出覆核，待主管核准後正式生效。",
                )
            else:
                log_action(
                    user=request.user,
                    action="修改維護合約",
                    target_type="Contract",
                    target_identifier=f"{updated_ct.contract_no} ({updated_ct.customer.name})",
                    ip_address=_client_ip(request),
                )
                if updated_ct.approval_status == MaintenanceContract.APPROVAL_APPROVED:
                    notify_customer_followers(
                        customer=updated_ct.customer,
                        actor=request.user,
                        event_category="data_change",
                        event_title=f"更新維護合約：{updated_ct.contract_no}",
                        event_detail=summary_detail,
                        anchor="#card-contracts",
                    )
                messages.success(request, f"合約「{updated_ct.contract_no}」已更新。")
            return redirect("contract-update", pk=updated_ct.pk)
    else:
        form = MaintenanceContractForm(instance=contract, customer=contract.customer)

    rma_form = ContractProbeReplacementForm(contract=contract)

    return render(
        request,
        "customers/contract_form.html",
        {
            "form": form,
            "contract": contract,
            "customer": contract.customer,
            "rma_form": rma_form,
            "attachments": contract.attachments.all(),
            "replacement_logs": contract.replacement_logs.all(),
            "title": f"編輯合約 - {contract.contract_no}",
            "requires_approval": requires_approval,
            "can_approve_contracts": can_approve_con,
        },
    )


def _execute_contract_approve(ct: MaintenanceContract, reviewer, ip_address=None) -> bool:
    if ct.approval_status == MaintenanceContract.APPROVAL_APPROVED:
        return False
    ct.approval_status = MaintenanceContract.APPROVAL_APPROVED
    ct.reviewed_by = reviewer
    ct.reviewed_at = timezone.now()
    ct.rejection_reason = ""
    ct.save()

    summary_detail = (
        f"合約編號：{ct.contract_no}\n"
        f"合約名稱：{ct.name or '-'}\n"
        f"期間：{ct.start_date or '-'} ~ {ct.end_date or '-'}\n"
        f"狀態：{ct.status_display}"
    )
    log_action(
        user=reviewer,
        action="覆核核准維護保固合約",
        target_type="Contract",
        target_identifier=f"{ct.contract_no} ({ct.customer.name})",
        ip_address=ip_address,
    )
    notify_approval_decision(
        module_type="warranty",
        decision="approved",
        applicant=ct.created_by,
        reviewer=reviewer,
        customer=ct.customer,
        target_identifier=ct.contract_no,
        detail_text=summary_detail,
        target_path=reverse("contract-update", kwargs={"pk": ct.pk}),
    )
    notify_customer_followers(
        customer=ct.customer,
        actor=reviewer,
        event_category="data_change",
        event_title=f"維護保固合約已覆核生效：{ct.contract_no}",
        event_detail=summary_detail,
        anchor="#card-contracts",
    )
    return True


@login_required
@require_POST
def contract_approve(request, pk):
    if not user_can_approve_contracts(request.user):
        messages.error(request, "權限不足：您沒有覆核維護保固合約的權限。")
        return redirect("contract-list")

    ct = get_object_or_404(
        MaintenanceContract.objects.select_related("customer", "created_by"),
        pk=pk,
    )
    if not user_can_access_customer(request.user, ct.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("contract-list")

    if ct.created_by_id and ct.created_by_id == request.user.id and not request.user.is_superuser:
        messages.error(request, "簽核規範限制：不可覆核自己送審的單據，請由其他具備覆核權限的人員審核。")
    elif _execute_contract_approve(ct, request.user, ip_address=_client_ip(request)):
        messages.success(request, f"已核准維護保固合約「{ct.contract_no}」，現已正式生效並可匯出保固證書！")
    else:
        messages.info(request, f"維護保固合約「{ct.contract_no}」已是核准狀態。")

    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("contract-update", pk=pk)


@login_required
@require_POST
def contract_batch_approve(request):
    if not user_can_approve_contracts(request.user):
        messages.error(request, "權限不足：您沒有覆核維護保固合約的權限。")
        return redirect("contract-list")

    raw_ids = request.POST.getlist("selected_ids")
    valid_ids = [int(x) for x in raw_ids if str(x).isdigit()]
    if not valid_ids:
        messages.warning(request, "請先勾選至少一筆待覆核的維護合約。")
        return redirect(request.POST.get("next") or "contract-list")

    qs = filter_by_customer_scope(
        request.user,
        MaintenanceContract.objects.filter(
            pk__in=valid_ids,
            approval_status=MaintenanceContract.APPROVAL_PENDING,
        ).select_related("customer", "created_by"),
    )

    approved_cnt = 0
    skipped_self = 0
    ip_addr = _client_ip(request)
    for ct in qs:
        if ct.created_by_id and ct.created_by_id == request.user.id and not request.user.is_superuser:
            skipped_self += 1
            continue
        if _execute_contract_approve(ct, request.user, ip_address=ip_addr):
            approved_cnt += 1

    if approved_cnt > 0:
        messages.success(request, f"已批次覆核通過 {approved_cnt} 筆維護保固合約！")
    if skipped_self > 0:
        messages.warning(request, f"略過 {skipped_self} 筆由您本人送審的單據（需由其他覆核主管審核）。")
    if approved_cnt == 0 and skipped_self == 0:
        messages.info(request, "所選項目中沒有可覆核之待審維護合約。")

    return redirect(request.POST.get("next") or "contract-list")


@login_required
@require_POST
def contract_reject(request, pk):
    if not user_can_approve_contracts(request.user):
        messages.error(request, "權限不足：您沒有退回維護保固合約的權限。")
        return redirect("contract-list")

    ct = get_object_or_404(
        MaintenanceContract.objects.select_related("customer", "created_by"),
        pk=pk,
    )
    if not user_can_access_customer(request.user, ct.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("contract-list")

    reason = (request.POST.get("rejection_reason") or "").strip()
    next_url = request.POST.get("next") or request.GET.get("next")
    if not reason:
        messages.error(request, "退回合約時必須填寫「退回原因說明」。")
        return redirect(next_url or reverse("contract-update", kwargs={"pk": pk}))

    ct.approval_status = MaintenanceContract.APPROVAL_REJECTED
    ct.reviewed_by = request.user
    ct.reviewed_at = timezone.now()
    ct.rejection_reason = reason
    ct.save()

    summary_detail = (
        f"合約編號：{ct.contract_no}\n"
        f"合約名稱：{ct.name or '-'}\n"
        f"期間：{ct.start_date or '-'} ~ {ct.end_date or '-'}"
    )
    log_action(
        user=request.user,
        action="退回維護保固合約",
        target_type="Contract",
        target_identifier=f"{ct.contract_no} ({ct.customer.name})",
        detail=f"退回原因: {reason}",
        ip_address=_client_ip(request),
    )
    notify_approval_decision(
        module_type="warranty",
        decision="rejected",
        applicant=ct.created_by,
        reviewer=request.user,
        customer=ct.customer,
        target_identifier=ct.contract_no,
        detail_text=summary_detail,
        target_path=reverse("contract-update", kwargs={"pk": ct.pk}),
        rejection_reason=reason,
    )
    messages.warning(request, f"已退回維護保固合約「{ct.contract_no}」，並通知原開單人員修正。")
    if next_url:
        return redirect(next_url)
    return redirect("contract-update", pk=pk)


@login_required
def contract_swap_probe(request, pk):
    contract = get_object_or_404(MaintenanceContract, pk=pk)
    if not user_can_edit_licenses(request.user):
        messages.error(request, "權限不足。")
        return redirect("contract-update", pk=pk)
    if not user_can_access_customer(request.user, contract.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("contract-list")

    if request.method == "POST":
        form = ContractProbeReplacementForm(request.POST, contract=contract)
        if form.is_valid():
            action_type = form.cleaned_data["action_type"]
            old_probe = form.cleaned_data["old_probe"]
            new_probe = form.cleaned_data.get("new_probe")
            reason = form.cleaned_data["reason"]
            user = request.user

            # 若為「故障換機 (新機出庫)」且開啟了硬體出庫簽核，且操作者無硬體覆核權限：預鎖新機進入待覆核
            if (
                action_type == ContractProbeReplacement.ACTION_REPLACE
                and new_probe
                and is_hardware_approval_required_for_user(user)
            ):
                from apps.hardware.approval_service import create_hardware_outbound_approval
                from apps.hardware.models import HardwareOutboundApproval

                create_hardware_outbound_approval(
                    device=new_probe,
                    customer=contract.customer,
                    request_type=HardwareOutboundApproval.REQUEST_RMA,
                    applicant=user,
                    note=reason,
                    contract=contract,
                    old_probe=old_probe,
                    ip_address=_client_ip(request),
                )
                messages.warning(
                    request,
                    f"已送出維護合約故障換機簽核申請！新備品「{new_probe.code}」已預先鎖定為「出貨待審」，待硬體覆核主管核准後將自動完成新舊機替換。",
                )
                return redirect("contract-update", pk=pk)

            with transaction.atomic():
                old_probe_code = old_probe.code
                customer = contract.customer

                # 1. 處置舊設備
                InventoryTransaction.objects.create(
                    device=old_probe,
                    customer=customer,
                    type=InventoryTransaction.TYPE_RETURN_OUT,
                    quantity=1,
                    note=f"維護合約 ({contract.contract_no}) 設備退換: {reason}",
                    created_by=user,
                )
                old_probe.customer = None
                old_probe.status = HardwareDevice.STATUS_AVAILABLE
                old_probe._skip_auto_txn = True
                old_probe.save()

                # 從合約移除舊設備
                contract.probes.remove(old_probe)

                new_probe_code = ""

                if action_type == ContractProbeReplacement.ACTION_REPLACE and new_probe:
                    new_probe_code = new_probe.code
                    # 2. 處置新設備
                    new_probe.customer = customer
                    new_probe.status = HardwareDevice.STATUS_SHIPPED
                    new_probe._skip_auto_txn = True
                    new_probe.save()

                    InventoryTransaction.objects.create(
                        device=new_probe,
                        customer=customer,
                        type=InventoryTransaction.TYPE_OUT,
                        quantity=1,
                        note=f"維護合約 ({contract.contract_no}) 換機替換上線 (換下序號: {old_probe_code}): {reason}",
                        created_by=user,
                    )
                    # 加入合約
                    contract.probes.add(new_probe)

                # 3. 建立更換歷史紀錄
                ContractProbeReplacement.objects.create(
                    contract=contract,
                    action_type=action_type,
                    old_probe=old_probe,
                    old_probe_code=old_probe_code,
                    new_probe=new_probe,
                    new_probe_code=new_probe_code,
                    reason=reason,
                    transferred_license=None,
                    performed_by=user,
                )

                # 4. 同步寫入客戶軌跡紀錄
                action_label = "故障換機" if action_type == ContractProbeReplacement.ACTION_REPLACE else "設備退回"
                detail_text = f"【維護合約{action_label}】合約編號: {contract.contract_no}\n換下舊機: {old_probe_code}"
                if new_probe_code:
                    detail_text += f"\n換上新機: {new_probe_code}"
                detail_text += f"\n原因說明: {reason}"

                CustomerActivityLog.objects.create(
                    customer=customer,
                    activity_type=CustomerActivityLog.TYPE_SUPPORT,
                    content=detail_text,
                    created_by=user,
                )

                # 5. 系統稽核紀錄
                log_action(
                    user=user,
                    action=f"合約設備{action_label}",
                    target_type="Contract",
                    target_identifier=f"{contract.contract_no} ({old_probe_code} -> {new_probe_code or '退回'})",
                    detail=reason,
                    ip_address=request.META.get("REMOTE_ADDR"),
                )

                notify_customer_followers(
                    customer=customer,
                    actor=user,
                    event_category="data_change",
                    event_title=f"維護合約設備{action_label} ({contract.contract_no})",
                    event_detail=detail_text,
                    anchor="#card-contracts",
                )

            messages.success(request, f"成功完成合約設備{action_label}作業！")
        else:
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, f"退換貨失敗: {err}")

    return redirect("contract-update", pk=pk)


@login_required
def api_customer_contract_candidates(request, customer_id):
    """AJAX helper: 取得某客戶可供納入維護合約之軟體授權書與設備清單"""
    if not user_can_view_crm(request.user):
        return JsonResponse({"licenses": [], "probes": []}, status=403)
    customer = get_object_or_404(Customer, pk=customer_id)
    if not user_can_access_customer(request.user, customer):
        return JsonResponse({"licenses": [], "probes": []}, status=403)

    licenses = CustomerLicense.objects.filter(customer_id=customer_id).select_related("product").order_by("-created_at")
    probes = Probe.objects.filter(
        customer_id=customer_id, status__in=[Probe.STATUS_SHIPPED, Probe.STATUS_LOANED], is_deleted=False
    ).select_related("model").order_by("code")

    license_data = [
        {
            "id": lic.id,
            "license_no": lic.license_no,
            "product_name": lic.product.name if lic.product else "未知產品",
            "quantity": lic.quantity,
            "unit": lic.unit,
            "status_display": lic.status_display,
            "end_date": lic.end_date.strftime("%Y-%m-%d") if lic.end_date else "永久/無到期日",
        }
        for lic in licenses
    ]

    probe_data = [
        {
            "id": p.id,
            "code": p.code,
            "model_name": p.model.name if p.model else "未知型號",
            "status": p.status,
            "status_display": p.get_status_display(),
        }
        for p in probes
    ]

    return JsonResponse({"licenses": license_data, "probes": probe_data})



@login_required
def contract_delete(request, pk):
    contract = get_object_or_404(MaintenanceContract, pk=pk)
    if not user_can_edit_licenses(request.user):
        messages.error(request, "權限不足：您沒有刪除維護合約的權限。")
        return redirect("contract-list")
    if not user_can_access_customer(request.user, contract.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("contract-list")

    if request.method == "POST":
        no = contract.contract_no
        cust_obj = contract.customer
        contract.delete()
        log_action(
            user=request.user,
            action="刪除維護合約",
            target_type="Contract",
            target_identifier=no,
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        notify_customer_followers(
            customer=cust_obj,
            actor=request.user,
            event_category="data_change",
            event_title=f"刪除維護合約：{no}",
            event_detail=f"已刪除維護合約「{no}」",
            anchor="#card-contracts",
        )
        messages.success(request, f"已刪除合約「{no}」")
        return redirect("contract-list")

    return render(
        request,
        "customers/contract_confirm_delete.html",
        {"contract": contract},
    )


# -------------------------------------------------------------
# 客戶執行軌跡與討論 (Activity Log)
# -------------------------------------------------------------

@login_required
def api_customer_activity_logs(request, customer_pk):
    if not user_can_view_crm(request.user):
        return JsonResponse({"error": "權限不足"}, status=403)
    customer = get_object_or_404(Customer, pk=customer_pk)
    if not user_can_access_customer(request.user, customer):
        return JsonResponse({"error": "權限不足"}, status=403)
    logs = (
        customer.activity_logs.select_related("created_by", "contact")
        .prefetch_related("attachments")
        .order_by("-created_at", "-id")
    )

    activity_type = request.GET.get("type", "").strip()
    author = request.GET.get("author", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    search = request.GET.get("q", "").strip()

    if activity_type and activity_type != "all":
        logs = logs.filter(activity_type=activity_type)
    if author:
        logs = logs.filter(created_by__username=author)
    if date_from:
        logs = logs.filter(created_at__date__gte=date_from)
    if date_to:
        logs = logs.filter(created_at__date__lte=date_to)
    if search:
        logs = logs.filter(
            Q(content__icontains=search)
            | Q(contact__name__icontains=search)
            | Q(created_by__username__icontains=search)
            | Q(created_by__first_name__icontains=search)
            | Q(created_by__last_name__icontains=search)
        )

    paginator = Paginator(logs, 10)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    can_edit = user_can_edit_customers(request.user)

    html = render_to_string(
        "customers/activity_log_items.html",
        {
            "activity_logs": page_obj,
            "request": request,
            "can_edit_crm": can_edit,
            "can_edit_customers": can_edit,
        },
        request=request,
    )

    return JsonResponse(
        {
            "html": html,
            "has_next": page_obj.has_next(),
            "next_page_number": (
                page_obj.next_page_number() if page_obj.has_next() else None
            ),
            "total_count": paginator.count,
            "current_page": page_obj.number,
            "total_pages": paginator.num_pages,
        }
    )


@login_required
def activity_log_create(request, customer_pk):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")
    customer = get_object_or_404(Customer, pk=customer_pk)
    if not user_can_access_customer(request.user, customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")
    if request.method == "POST":
        form = CustomerActivityLogForm(request.POST, request.FILES, customer=customer)
        if form.is_valid():
            log = form.save(commit=False)
            log.customer = customer
            log.created_by = request.user
            log.save()

            files = request.FILES.getlist("files")
            for f in files:
                ActivityAttachment.objects.create(
                    activity_log=log,
                    file=f,
                    filename=f.name,
                    file_size=f.size,
                )

            log_action(
                user=request.user,
                action="新增客戶執行軌跡",
                target_type="CustomerActivityLog",
                target_identifier=f"{customer.code} - {log.get_activity_type_display()}",
                detail=f"為客戶「{customer.name}」新增執行軌跡 (ID: {log.pk})",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            notify_customer_followers(
                customer=customer,
                actor=request.user,
                event_category="comment",
                event_title=f"新增客戶留言 ({log.get_activity_type_display()})",
                event_detail=log.content,
                anchor=f"#activity-log-{log.pk}",
            )
            messages.success(request, "已成功建立一筆客戶執行軌跡紀錄。")
        else:
            messages.error(request, "建立執行軌跡失敗，請檢查輸入內容是否完整。")
    return redirect("customer-detail", pk=customer_pk)


@login_required
def activity_log_update(request, pk):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")
    log = get_object_or_404(CustomerActivityLog.objects.select_related("customer"), pk=pk)
    customer_pk = log.customer.pk
    if not user_can_access_customer(request.user, log.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")
    can_manage = user_can_edit_customers(request.user) or (log.created_by == request.user)
    if not can_manage:
        messages.error(request, "權限不足：您只能編輯自己建立的執行軌跡或需具備客戶資料管理權限。")
        return redirect("customer-detail", pk=customer_pk)

    if request.method == "POST":
        form = CustomerActivityLogForm(request.POST, request.FILES, instance=log, customer=log.customer)
        if form.is_valid():
            log_obj = form.save(commit=False)
            log_obj.is_edited = True
            log_obj.save()

            files = request.FILES.getlist("files")
            for f in files:
                ActivityAttachment.objects.create(
                    activity_log=log_obj,
                    file=f,
                    filename=f.name,
                    file_size=f.size,
                )

            log_action(
                user=request.user,
                action="修改客戶執行軌跡",
                target_type="CustomerActivityLog",
                target_identifier=f"{log.customer.code} - {log.get_activity_type_display()}",
                detail=f"更新客戶「{log.customer.name}」之執行軌跡 (ID: {log.pk})",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            notify_customer_followers(
                customer=log_obj.customer,
                actor=request.user,
                event_category="comment",
                event_title=f"編輯客戶留言 ({log_obj.get_activity_type_display()})",
                event_detail=log_obj.content,
                anchor=f"#activity-log-{log_obj.pk}",
            )
            messages.success(request, "已成功更新執行軌跡紀錄。")
        else:
            messages.error(request, "更新執行軌跡失敗，請檢查輸入內容。")
    return redirect("customer-detail", pk=customer_pk)


@login_required
def activity_log_delete(request, pk):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")
    log = get_object_or_404(CustomerActivityLog.objects.select_related("customer"), pk=pk)
    customer_pk = log.customer.pk
    if not user_can_access_customer(request.user, log.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")
    can_manage = user_can_edit_customers(request.user) or (log.created_by == request.user)
    if not can_manage:
        messages.error(request, "權限不足：您只能刪除自己建立的執行軌跡或需具備客戶資料管理權限。")
        return redirect("customer-detail", pk=customer_pk)

    if request.method == "POST":
        log_type_display = log.get_activity_type_display()
        customer_name = log.customer.name
        # 刪除實體檔案
        for att in log.attachments.all():
            if att.file:
                att.file.delete(save=False)
        log.delete()

        log_action(
            user=request.user,
            action="刪除客戶執行軌跡",
            target_type="CustomerActivityLog",
            target_identifier=f"{customer_name} - {log_type_display}",
            detail=f"刪除客戶「{customer_name}」之執行軌跡 (ID: {pk})",
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, "已成功刪除該筆執行軌跡紀錄。")
    return redirect("customer-detail", pk=customer_pk)


@login_required
def activity_attachment_delete(request, pk):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")
    attachment = get_object_or_404(ActivityAttachment.objects.select_related("activity_log__customer"), pk=pk)
    log = attachment.activity_log
    customer_pk = log.customer.pk
    if not user_can_access_customer(request.user, log.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")
    can_manage = user_can_edit_customers(request.user) or (log.created_by == request.user)
    if not can_manage:
        messages.error(request, "權限不足：您無法刪除此附件。")
        return redirect("customer-detail", pk=customer_pk)

    if request.method == "POST":
        filename = attachment.filename
        if attachment.file:
            attachment.file.delete(save=False)
        attachment.delete()
        messages.success(request, f"已成功刪除附件檔案「{filename}」。")
    return redirect("customer-detail", pk=customer_pk)


@login_required
def contract_attachment_upload(request, contract_pk):
    contract = get_object_or_404(MaintenanceContract, pk=contract_pk)
    if not user_can_edit_licenses(request.user):
        messages.error(request, "權限不足：您無法上傳合約附件。")
        return redirect("contract-update", pk=contract_pk)
    if not user_can_access_customer(request.user, contract.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("contract-list")

    if request.method == "POST":
        files = request.FILES.getlist("attachments")
        count = 0
        for f in files:
            ContractAttachment.objects.create(
                contract=contract,
                file=f,
                filename=f.name,
                file_size=f.size,
                uploaded_by=request.user,
            )
            count += 1
        if count > 0:
            log_action(
                user=request.user,
                action="上傳維護合約附件",
                target_type="Contract",
                target_identifier=contract.contract_no,
                detail=f"為合約「{contract.contract_no}」上傳了 {count} 個附件檔案",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"已成功上傳 {count} 個合約附件檔案。")
        else:
            messages.warning(request, "未選擇任何檔案進行上傳。")
    return redirect("contract-update", pk=contract_pk)


@login_required
def contract_attachment_delete(request, pk):
    attachment = get_object_or_404(ContractAttachment.objects.select_related("contract__customer"), pk=pk)
    contract_pk = attachment.contract.pk
    if not user_can_edit_licenses(request.user):
        messages.error(request, "權限不足：您無法刪除此合約附件。")
        return redirect("contract-update", pk=contract_pk)
    if not user_can_access_customer(request.user, attachment.contract.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("contract-list")

    if request.method == "POST":
        filename = attachment.filename
        contract_no = attachment.contract.contract_no
        if attachment.file:
            attachment.file.delete(save=False)
        attachment.delete()
        log_action(
            user=request.user,
            action="刪除維護合約附件",
            target_type="Contract",
            target_identifier=contract_no,
            detail=f"刪除合約「{contract_no}」之附件「{filename}」",
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"已成功刪除附件「{filename}」。")
    return redirect("contract-update", pk=contract_pk)


# -------------------------------------------------------------
# 客戶自訂環境與資訊區塊 (CustomerInfoSection)
# -------------------------------------------------------------

@login_required
def info_section_create(request, customer_pk):
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足：您無法新增客戶環境與資訊區塊。")
        return redirect("customer-detail", pk=customer_pk)

    customer = get_object_or_404(Customer, pk=customer_pk)
    if not user_can_access_customer(request.user, customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")
    if request.method == "POST":
        form = CustomerInfoSectionForm(request.POST)
        if form.is_valid():
            section = form.save(commit=False)
            section.customer = customer
            section.updated_by = request.user
            section.save()
            log_action(
                user=request.user,
                action="新增客戶自訂資訊區塊",
                target_type="CustomerInfoSection",
                target_identifier=f"{customer.code} - {section.title}",
                detail=f"為客戶「{customer.name}」新增資訊區塊「{section.title}」",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            notify_customer_followers(
                customer=customer,
                actor=request.user,
                event_category="data_change",
                event_title=f"新增環境與資訊區塊：{section.title}",
                event_detail=section.content,
                anchor="#info-sections-panel",
            )
            messages.success(request, f"已成功新增區塊「{section.title}」。")
        else:
            messages.error(request, "新增區塊失敗，請檢查區塊名稱與內容是否完整。")
    return redirect("customer-detail", pk=customer_pk)


@login_required
def info_section_update(request, pk):
    section = get_object_or_404(CustomerInfoSection.objects.select_related("customer"), pk=pk)
    customer_pk = section.customer.pk
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足：您無法編輯客戶環境與資訊區塊。")
        return redirect("customer-detail", pk=customer_pk)
    if not user_can_access_customer(request.user, section.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")

    if request.method == "POST":
        form = CustomerInfoSectionForm(request.POST, instance=section)
        if form.is_valid():
            sec = form.save(commit=False)
            sec.updated_by = request.user
            sec.save()
            log_action(
                user=request.user,
                action="修改客戶自訂資訊區塊",
                target_type="CustomerInfoSection",
                target_identifier=f"{section.customer.code} - {sec.title}",
                detail=f"更新客戶「{section.customer.name}」之資訊區塊「{sec.title}」",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            notify_customer_followers(
                customer=sec.customer,
                actor=request.user,
                event_category="data_change",
                event_title=f"更新環境與資訊區塊：{sec.title}",
                event_detail=sec.content,
                anchor="#info-sections-panel",
            )
            messages.success(request, f"已成功更新區塊「{sec.title}」。")
        else:
            messages.error(request, "更新區塊失敗，請檢查輸入內容。")
    return redirect("customer-detail", pk=customer_pk)


@login_required
def info_section_delete(request, pk):
    section = get_object_or_404(CustomerInfoSection.objects.select_related("customer"), pk=pk)
    customer_pk = section.customer.pk
    customer_obj = section.customer
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足：您無法刪除客戶環境與資訊區塊。")
        return redirect("customer-detail", pk=customer_pk)
    if not user_can_access_customer(request.user, customer_obj):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")

    if request.method == "POST":
        title = section.title
        customer_name = section.customer.name
        section.delete()
        log_action(
            user=request.user,
            action="刪除客戶自訂資訊區塊",
            target_type="CustomerInfoSection",
            target_identifier=f"{customer_name} - {title}",
            detail=f"刪除客戶「{customer_name}」之資訊區塊「{title}」",
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        notify_customer_followers(
            customer=customer_obj,
            actor=request.user,
            event_category="data_change",
            event_title=f"刪除環境與資訊區塊：{title}",
            event_detail=f"已刪除區塊「{title}」",
            anchor="#info-sections-panel",
        )
        messages.success(request, f"已刪除區塊「{title}」。")
    return redirect("customer-detail", pk=customer_pk)


# -------------------------------------------------------------
# 客戶關注與關注名單管理 (Customer Follow)
# -------------------------------------------------------------

@login_required
def customer_follow_toggle(request, pk):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")
    customer = get_object_or_404(Customer, pk=pk)
    if is_user_followed_only_scope(request.user):
        err_msg = "您的帳號設定為「僅限已關注/負責之客戶」視角，客戶負責範圍由系統管理員統一指派，無法自行變更關注狀態。"
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("ajax") == "1":
            return JsonResponse({"success": False, "message": err_msg}, status=403)
        messages.error(request, err_msg)
        return redirect("customer-list")
    if request.method == "POST":
        follow_obj = CustomerFollow.objects.filter(customer=customer, user=request.user).first()
        if follow_obj:
            follow_obj.delete()
            is_following = False
            action_msg = f"已取消關注客戶「{customer.name}」。"
            log_action(
                user=request.user,
                action="取消關注客戶",
                target_type="CustomerFollow",
                target_identifier=f"[{customer.code}] {customer.name}",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
        else:
            CustomerFollow.objects.create(customer=customer, user=request.user)
            is_following = True
            action_msg = f"已開始關注客戶「{customer.name}」！當有資料變更或新留言時將依您的通知設定發送提醒。"
            log_action(
                user=request.user,
                action="關注客戶",
                target_type="CustomerFollow",
                target_identifier=f"[{customer.code}] {customer.name}",
                ip_address=request.META.get("REMOTE_ADDR"),
            )

        followers_count = CustomerFollow.objects.filter(customer=customer).count()
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("ajax") == "1":
            return JsonResponse({
                "success": True,
                "is_following": is_following,
                "followers_count": followers_count,
                "message": action_msg,
            })

        messages.success(request, action_msg)

    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("customer-detail", pk=pk)


@login_required
def customer_follower_manage(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足：僅限系統管理員代為管理同仁的關注狀態。")
        return redirect("customer-detail", pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")
        target_user_id = request.POST.get("user_id")
        if target_user_id:
            target_user = get_object_or_404(User, pk=target_user_id)
            if action == "add":
                _, created = CustomerFollow.objects.get_or_create(customer=customer, user=target_user)
                if created:
                    log_action(
                        user=request.user,
                        action="管理員新增客戶關注人",
                        target_type="CustomerFollow",
                        target_identifier=f"[{customer.code}] {customer.name} <- {target_user.username}",
                        ip_address=request.META.get("REMOTE_ADDR"),
                    )
                    messages.success(request, f"已將同仁「{target_user.get_full_name() or target_user.username}」加入此客戶的關注名單。")
                else:
                    messages.info(request, f"同仁「{target_user.username}」已在關注名單中。")
            elif action == "remove":
                deleted, _ = CustomerFollow.objects.filter(customer=customer, user=target_user).delete()
                if deleted:
                    log_action(
                        user=request.user,
                        action="管理員移除客戶關注人",
                        target_type="CustomerFollow",
                        target_identifier=f"[{customer.code}] {customer.name} -x- {target_user.username}",
                        ip_address=request.META.get("REMOTE_ADDR"),
                    )
                    messages.success(request, f"已將同仁「{target_user.get_full_name() or target_user.username}」從關注名單移除。")

    return redirect("customer-detail", pk=pk)


# -------------------------------------------------------------
# 客戶上下游關係鍊快捷管理 (Bind / Unbind Child Customer)
# -------------------------------------------------------------

@login_required
def customer_bind_child(request, pk):
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足：您沒有編輯客戶關聯的權限。")
        return redirect("customer-detail", pk=pk)

    parent_customer = get_object_or_404(Customer, pk=pk)
    if not user_can_access_customer(request.user, parent_customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("customer-list")

    if request.method == "POST":
        child_id_raw = request.POST.get("child_customer_id", "").strip()
        if not child_id_raw.isdigit():
            messages.error(request, "請選擇要綁定為下層的客戶。")
            return redirect("customer-detail", pk=pk)

        child_customer = get_object_or_404(Customer, pk=int(child_id_raw))
        if not user_can_access_customer(request.user, child_customer):
            messages.error(request, "權限不足：所選的下層客戶不在您的關注/負責範圍內。")
            return redirect("customer-detail", pk=pk)

        if child_customer.pk == parent_customer.pk:
            messages.error(request, "不可將客戶自己綁定為自己的下層客戶。")
            return redirect("customer-detail", pk=pk)

        ancestor_ids = {a.pk for a in parent_customer.get_ancestors()}
        if child_customer.pk in ancestor_ids or parent_customer.pk in child_customer.get_descendant_ids():
            messages.error(
                request,
                f"不可將上層客戶「{child_customer.name} ({child_customer.code})」設為下層客戶，這會造成上下游循環指向。",
            )
            return redirect("customer-detail", pk=pk)

        old_parent = child_customer.parent
        child_customer.parent = parent_customer
        child_customer.save(update_fields=["parent", "updated_at"])

        log_action(
            user=request.user,
            action="綁定下層客戶關聯",
            target_type="Customer",
            target_identifier=f"[{parent_customer.code}] {parent_customer.name} -> [{child_customer.code}] {child_customer.name}",
            detail=f"將「{child_customer.name}」掛載至上層客戶「{parent_customer.name}」底下"
            + (f"（原上層：{old_parent.name}）" if old_parent else ""),
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        notify_customer_followers(
            customer=parent_customer,
            actor=request.user,
            event_category="data_change",
            event_title=f"新增下層客戶關聯：{child_customer.name}",
            event_detail=f"已將客戶「{child_customer.name} ({child_customer.code})」綁定為本客戶的下層客戶。",
            anchor="#card-hierarchy",
        )
        messages.success(
            request,
            f"已成功將「{child_customer.name} ({child_customer.code})」綁定為「{parent_customer.name}」的下層客戶！",
        )

    return redirect("customer-detail", pk=pk)


@login_required
def customer_unbind_child(request, pk, child_pk):
    if not user_can_edit_customers(request.user):
        messages.error(request, "權限不足：您沒有編輯客戶關聯的權限。")
        return redirect("customer-detail", pk=pk)

    parent_customer = get_object_or_404(Customer, pk=pk)
    child_customer = get_object_or_404(Customer, pk=child_pk)
    if not user_can_access_customer(request.user, parent_customer) or not user_can_access_customer(
        request.user, child_customer
    ):
        messages.error(request, "權限不足：客戶不在您的關注/負責範圍內。")
        return redirect("customer-detail", pk=pk)

    if request.method == "POST":
        if child_customer.parent_id == parent_customer.pk:
            child_customer.parent = None
            child_customer.save(update_fields=["parent", "updated_at"])
            log_action(
                user=request.user,
                action="解除下層客戶關聯",
                target_type="Customer",
                target_identifier=f"[{parent_customer.code}] {parent_customer.name} -x- [{child_customer.code}] {child_customer.name}",
                detail=f"解除「{child_customer.name}」與上層客戶「{parent_customer.name}」之關聯",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(
                request,
                f"已解除「{child_customer.name} ({child_customer.code})」與「{parent_customer.name}」的上下游關聯。",
            )

    return redirect("customer-detail", pk=pk)


def _user_can_edit_service_records(user) -> bool:
    from apps.core.permissions import user_can_edit_orders, user_can_ship_orders

    return (
        user_can_edit_customers(user)
        or user_can_edit_orders(user)
        or user_can_ship_orders(user)
        or user_can_manage_system(user)
    )


@login_required
def service_record_create(request, customer_pk):
    customer = get_object_or_404(Customer, pk=customer_pk)
    if not user_can_access_customer(request.user, customer) or not _user_can_edit_service_records(request.user):
        messages.error(request, "權限不足：您沒有新增專業服務紀錄的權限。")
        return redirect("customer-detail", pk=customer.pk)

    from zoneinfo import ZoneInfo
    from .forms import CustomerServiceRecordForm
    from .models import CustomerServiceRecord, ServiceRecordAttachment, generate_service_no

    today_tw = timezone.now().astimezone(ZoneInfo("Asia/Taipei")).date()

    if request.method == "POST":
        form = CustomerServiceRecordForm(request.POST)
        if form.is_valid():
            svc = form.save(commit=False)
            svc.customer = customer
            svc.created_by = request.user
            if svc.status == CustomerServiceRecord.STATUS_COMPLETED and not svc.completed_at:
                svc.completed_at = today_tw
            svc.save()

            uploaded_count = 0
            for f in request.FILES.getlist("attachments"):
                ServiceRecordAttachment.objects.create(
                    service_record=svc,
                    file=f,
                    filename=f.name,
                    file_size=f.size,
                    uploaded_by=request.user,
                )
                uploaded_count += 1

            log_action(
                user=request.user,
                action="新增客戶專業服務紀錄",
                target_type="CustomerServiceRecord",
                target_identifier=svc.service_no,
                detail=f"客戶: {customer.name} | 項目: {svc.name} | 狀態: {svc.get_status_display()} | 附件: {uploaded_count} 個",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"已新增專業服務紀錄「{svc.service_no} - {svc.name}」。")
            return redirect("customer-detail", pk=customer.pk)
    else:
        form = CustomerServiceRecordForm(
            initial={
                "service_no": generate_service_no(),
                "service_date": today_tw,
                "assigned_to": request.user.pk,
            }
        )

    return render(
        request,
        "customers/service_record_form.html",
        {
            "form": form,
            "customer": customer,
            "service_record": None,
            "attachments": [],
            "title": f"新增專業服務 / 其他紀錄 - {customer.name}",
            "can_edit_services": True,
        },
    )


@login_required
def service_record_update(request, pk):
    from zoneinfo import ZoneInfo
    from .forms import CustomerServiceRecordForm
    from .models import CustomerServiceRecord, ServiceRecordAttachment

    svc = get_object_or_404(
        CustomerServiceRecord.objects.select_related("customer", "source_order", "assigned_to", "created_by"),
        pk=pk,
    )
    customer = svc.customer
    if not user_can_access_customer(request.user, customer):
        messages.error(request, "權限不足：您無權檢視此客戶的專業服務紀錄。")
        return redirect("customer-list")

    can_edit = _user_can_edit_service_records(request.user)
    today_tw = timezone.now().astimezone(ZoneInfo("Asia/Taipei")).date()

    if request.method == "POST":
        if not can_edit:
            messages.error(request, "權限不足：您沒有編輯專業服務紀錄的權限。")
            return redirect("service-record-update", pk=svc.pk)

        form = CustomerServiceRecordForm(request.POST, instance=svc)
        if form.is_valid():
            updated = form.save(commit=False)
            if updated.status == CustomerServiceRecord.STATUS_COMPLETED and not updated.completed_at:
                updated.completed_at = today_tw
            updated.save()

            uploaded_count = 0
            for f in request.FILES.getlist("attachments"):
                ServiceRecordAttachment.objects.create(
                    service_record=updated,
                    file=f,
                    filename=f.name,
                    file_size=f.size,
                    uploaded_by=request.user,
                )
                uploaded_count += 1

            log_action(
                user=request.user,
                action="更新客戶專業服務紀錄",
                target_type="CustomerServiceRecord",
                target_identifier=updated.service_no,
                detail=f"客戶: {customer.name} | 項目: {updated.name} | 狀態: {updated.get_status_display()} | 新增附件: {uploaded_count} 個",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"已更新專業服務紀錄「{updated.service_no}」。")
            return redirect("service-record-update", pk=updated.pk)
    else:
        form = CustomerServiceRecordForm(instance=svc)

    attachments = list(svc.attachments.select_related("uploaded_by").all())
    return render(
        request,
        "customers/service_record_form.html",
        {
            "form": form,
            "customer": customer,
            "service_record": svc,
            "attachments": attachments,
            "title": f"專業服務 / 其他紀錄 - {svc.service_no}",
            "can_edit_services": can_edit,
        },
    )


@login_required
def service_record_delete(request, pk):
    from .models import CustomerServiceRecord

    svc = get_object_or_404(CustomerServiceRecord.objects.select_related("customer"), pk=pk)
    customer = svc.customer
    if not user_can_access_customer(request.user, customer) or not _user_can_edit_service_records(request.user):
        messages.error(request, "權限不足：無法刪除此專業服務紀錄。")
        return redirect("customer-detail", pk=customer.pk)

    if request.method == "POST":
        svc_no = svc.service_no
        svc_name = svc.name
        for att in svc.attachments.all():
            if att.file:
                att.file.delete(save=False)
        svc.delete()
        log_action(
            user=request.user,
            action="刪除客戶專業服務紀錄",
            target_type="CustomerServiceRecord",
            target_identifier=svc_no,
            detail=f"客戶: {customer.name} | 項目: {svc_name}",
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"已刪除專業服務紀錄「{svc_no} - {svc_name}」。")

    return redirect("customer-detail", pk=customer.pk)


@login_required
def service_attachment_upload(request, service_pk):
    from .models import CustomerServiceRecord, ServiceRecordAttachment

    svc = get_object_or_404(CustomerServiceRecord.objects.select_related("customer"), pk=service_pk)
    if not user_can_access_customer(request.user, svc.customer) or not _user_can_edit_service_records(request.user):
        messages.error(request, "權限不足：無法上傳專業服務附件。")
        return redirect("service-record-update", pk=svc.pk)

    if request.method == "POST":
        files = request.FILES.getlist("attachments")
        if not files:
            messages.warning(request, "請選擇至少一個檔案再上傳。")
        else:
            for f in files:
                ServiceRecordAttachment.objects.create(
                    service_record=svc,
                    file=f,
                    filename=f.name,
                    file_size=f.size,
                    uploaded_by=request.user,
                )
            log_action(
                user=request.user,
                action="上傳專業服務附件",
                target_type="CustomerServiceRecord",
                target_identifier=svc.service_no,
                detail=f"上傳 {len(files)} 個附件",
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"已成功上傳 {len(files)} 個附件。")

    return redirect("service-record-update", pk=svc.pk)


@login_required
def service_attachment_download(request, pk):
    import mimetypes
    from django.http import FileResponse, Http404
    from .models import ServiceRecordAttachment

    att = get_object_or_404(
        ServiceRecordAttachment.objects.select_related("service_record__customer"),
        pk=pk,
    )
    if not user_can_access_customer(request.user, att.service_record.customer):
        messages.error(request, "權限不足：無法存取此附件。")
        return redirect("customer-list")
    if not att.file:
        raise Http404("檔案不存在")

    preview = request.GET.get("preview") == "1"
    content_type, _ = mimetypes.guess_type(att.filename)
    return FileResponse(
        att.file.open("rb"),
        as_attachment=not preview,
        filename=att.filename,
        content_type=content_type or "application/octet-stream",
    )


@login_required
def service_attachment_delete(request, pk):
    from .models import ServiceRecordAttachment

    att = get_object_or_404(
        ServiceRecordAttachment.objects.select_related("service_record__customer"),
        pk=pk,
    )
    svc = att.service_record
    if not user_can_access_customer(request.user, svc.customer) or not _user_can_edit_service_records(request.user):
        messages.error(request, "權限不足：無法刪除此附件。")
        return redirect("service-record-update", pk=svc.pk)

    if request.method == "POST":
        fname = att.filename
        if att.file:
            att.file.delete(save=False)
        att.delete()
        log_action(
            user=request.user,
            action="刪除專業服務附件",
            target_type="CustomerServiceRecord",
            target_identifier=svc.service_no,
            detail=f"刪除附件: {fname}",
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"已刪除附件「{fname}」。")

    return redirect("service-record-update", pk=svc.pk)




