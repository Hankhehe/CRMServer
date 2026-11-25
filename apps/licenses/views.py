import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone

from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.permissions import (
    user_can_view_crm,
    user_can_edit_crm,
    user_can_edit_licenses,
    user_can_edit_products,
    user_can_approve_licenses,
    is_license_approval_required_for_user,
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
from apps.customers.models import Customer
from .models import Product, CustomerLicense
from .forms import ProductForm, CustomerLicenseForm


# ---------------------------------------------------------------------------
# 軟體授權書 (Customer License) Views
# ---------------------------------------------------------------------------

@login_required
def license_list(request):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")

    query = request.GET.get("q", "").strip()
    product_id = request.GET.get("product", "").strip()
    status_filter = request.GET.get("status", "").strip()
    approval_filter = request.GET.get("approval", "").strip()
    license_type = request.GET.get("type", "").strip()
    expiring = request.GET.get("expiring", "").strip()

    base_scoped_licenses = filter_by_customer_scope(
        request.user,
        CustomerLicense.objects.select_related("customer", "product", "created_by", "reviewed_by").all(),
    )
    pending_approval_count = base_scoped_licenses.filter(
        approval_status=CustomerLicense.APPROVAL_PENDING
    ).count()
    rejected_approval_count = base_scoped_licenses.filter(
        approval_status=CustomerLicense.APPROVAL_REJECTED
    ).count()

    licenses = base_scoped_licenses

    if query:
        licenses = licenses.filter(
            Q(license_no__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__code__icontains=query)
            | Q(product__name__icontains=query)
            | Q(product__code__icontains=query)
        )

    if product_id:
        licenses = licenses.filter(product_id=product_id)

    if approval_filter in (
        CustomerLicense.APPROVAL_PENDING,
        CustomerLicense.APPROVAL_REJECTED,
        CustomerLicense.APPROVAL_APPROVED,
    ):
        licenses = licenses.filter(approval_status=approval_filter)

    today = timezone.localdate()

    if status_filter:
        if status_filter == "approval_pending":
            licenses = licenses.filter(approval_status=CustomerLicense.APPROVAL_PENDING)
        elif status_filter == "approval_rejected":
            licenses = licenses.filter(approval_status=CustomerLicense.APPROVAL_REJECTED)
        elif status_filter == CustomerLicense.STATUS_SUSPENDED:
            licenses = licenses.filter(
                approval_status=CustomerLicense.APPROVAL_APPROVED,
                is_disabled=True,
            )
        elif status_filter == CustomerLicense.STATUS_PENDING:
            licenses = licenses.filter(
                approval_status=CustomerLicense.APPROVAL_APPROVED,
                is_disabled=False,
                start_date__isnull=False,
                start_date__gt=today,
            )
        elif status_filter == CustomerLicense.STATUS_EXPIRED:
            licenses = licenses.filter(
                approval_status=CustomerLicense.APPROVAL_APPROVED,
                is_disabled=False,
                is_perpetual=False,
                end_date__isnull=False,
                end_date__lt=today,
            ).exclude(license_type=CustomerLicense.TYPE_PERPETUAL)
        elif status_filter == CustomerLicense.STATUS_ACTIVE:
            licenses = (
                licenses.filter(
                    approval_status=CustomerLicense.APPROVAL_APPROVED,
                    is_disabled=False,
                )
                .filter(Q(start_date__isnull=True) | Q(start_date__lte=today))
                .filter(
                    Q(is_perpetual=True)
                    | Q(license_type=CustomerLicense.TYPE_PERPETUAL)
                    | Q(end_date__isnull=True)
                    | Q(end_date__gte=today)
                )
            )
        else:
            licenses = licenses.filter(status=status_filter)

    if license_type:
        if license_type == "perpetual":
            licenses = licenses.filter(Q(is_perpetual=True) | Q(license_type="perpetual"))
        elif license_type == "subscription":
            licenses = licenses.filter(is_perpetual=False).exclude(license_type="perpetual")
        else:
            licenses = licenses.filter(license_type=license_type)

    if expiring == "1":
        target_date = today + datetime.timedelta(days=30)
        licenses = licenses.filter(
            approval_status=CustomerLicense.APPROVAL_APPROVED,
            is_disabled=False,
            is_perpetual=False,
            end_date__isnull=False,
            end_date__gte=today,
            end_date__lte=target_date,
        ).exclude(license_type=CustomerLicense.TYPE_PERPETUAL)

    licenses = licenses.order_by("-created_at")

    paginator = Paginator(licenses, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    can_edit_lic = user_can_edit_licenses(request.user)
    can_approve_lic = user_can_approve_licenses(request.user)
    can_edit_prod = user_can_edit_products(request.user)
    products = Product.objects.filter(is_active=True).order_by("name")

    return render(
        request,
        "licenses/license_list.html",
        {
            "page_obj": page_obj,
            "query": query,
            "product_id": product_id,
            "status_filter": status_filter,
            "approval_filter": approval_filter,
            "license_type": license_type,
            "expiring": expiring,
            "pending_approval_count": pending_approval_count,
            "rejected_approval_count": rejected_approval_count,
            "can_edit_crm": can_edit_lic,
            "can_edit_licenses": can_edit_lic,
            "can_approve_licenses": can_approve_lic,
            "can_edit_products": can_edit_prod,
            "products": products,
            "total_count": paginator.count,
        },
    )


@login_required
def license_export(request):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")

    from apps.core.excel_utils import export_to_excel

    query = request.GET.get("q", "").strip()
    product_id = request.GET.get("product", "").strip()
    status_filter = request.GET.get("status", "").strip()
    approval_filter = request.GET.get("approval", "").strip()
    license_type = request.GET.get("type", "").strip()
    expiring = request.GET.get("expiring", "").strip()

    licenses = (
        CustomerLicense.objects.select_related("customer", "product", "created_by", "reviewed_by")
        .prefetch_related("maintenance_contracts")
        .all()
    )
    licenses = filter_by_customer_scope(request.user, licenses)

    if query:
        licenses = licenses.filter(
            Q(license_no__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__code__icontains=query)
            | Q(product__name__icontains=query)
            | Q(product__code__icontains=query)
        )

    if product_id:
        licenses = licenses.filter(product_id=product_id)

    if approval_filter in (
        CustomerLicense.APPROVAL_PENDING,
        CustomerLicense.APPROVAL_REJECTED,
        CustomerLicense.APPROVAL_APPROVED,
    ):
        licenses = licenses.filter(approval_status=approval_filter)

    today = timezone.localdate()

    if status_filter:
        if status_filter == "approval_pending":
            licenses = licenses.filter(approval_status=CustomerLicense.APPROVAL_PENDING)
        elif status_filter == "approval_rejected":
            licenses = licenses.filter(approval_status=CustomerLicense.APPROVAL_REJECTED)
        elif status_filter == CustomerLicense.STATUS_SUSPENDED:
            licenses = licenses.filter(approval_status=CustomerLicense.APPROVAL_APPROVED, is_disabled=True)
        elif status_filter == CustomerLicense.STATUS_PENDING:
            licenses = licenses.filter(
                approval_status=CustomerLicense.APPROVAL_APPROVED,
                is_disabled=False,
                start_date__isnull=False,
                start_date__gt=today,
            )
        elif status_filter == CustomerLicense.STATUS_EXPIRED:
            licenses = licenses.filter(
                approval_status=CustomerLicense.APPROVAL_APPROVED,
                is_disabled=False,
                is_perpetual=False,
                end_date__isnull=False,
                end_date__lt=today,
            ).exclude(license_type=CustomerLicense.TYPE_PERPETUAL)
        elif status_filter == CustomerLicense.STATUS_ACTIVE:
            licenses = (
                licenses.filter(approval_status=CustomerLicense.APPROVAL_APPROVED, is_disabled=False)
                .filter(Q(start_date__isnull=True) | Q(start_date__lte=today))
                .filter(
                    Q(is_perpetual=True)
                    | Q(license_type=CustomerLicense.TYPE_PERPETUAL)
                    | Q(end_date__isnull=True)
                    | Q(end_date__gte=today)
                )
            )
        else:
            licenses = licenses.filter(status=status_filter)

    if license_type:
        if license_type == "perpetual":
            licenses = licenses.filter(Q(is_perpetual=True) | Q(license_type="perpetual"))
        elif license_type == "subscription":
            licenses = licenses.filter(is_perpetual=False).exclude(license_type="perpetual")
        else:
            licenses = licenses.filter(license_type=license_type)

    if expiring == "1":
        target_date = today + datetime.timedelta(days=30)
        licenses = licenses.filter(
            approval_status=CustomerLicense.APPROVAL_APPROVED,
            is_disabled=False,
            is_perpetual=False,
            end_date__isnull=False,
            end_date__gte=today,
            end_date__lte=target_date,
        ).exclude(license_type=CustomerLicense.TYPE_PERPETUAL)

    licenses = licenses.order_by("-created_at")

    headers = [
        "授權書編號",
        "簽核覆核狀態",
        "採購客戶代號",
        "採購客戶名稱",
        "產品代碼",
        "產品名稱",
        "授權類型",
        "授權數量",
        "計量單位",
        "授權起始日",
        "授權到期日",
        "永久買斷",
        "授權狀態",
        "涵蓋維護編號",
        "授權金鑰 / 序號",
        "備註說明",
        "建立時間",
    ]

    # 掃描當次匯出資料中所有產品規格 Schema 與 custom_values 出現過的 key
    custom_field_keys = []
    custom_field_label_map = {}

    for lic in licenses:
        schema = (lic.product.custom_field_schema or []) if lic.product else []
        for item in schema:
            k = item.get("key")
            if k and k not in custom_field_keys:
                custom_field_keys.append(k)
                custom_field_label_map[k] = item.get("label") or k
        vals = lic.custom_values or {}
        for k in vals.keys():
            if k and k not in custom_field_keys:
                custom_field_keys.append(k)
                custom_field_label_map[k] = k

    # 動態展開自訂規格表頭
    for k in custom_field_keys:
        headers.append(f"自訂: {custom_field_label_map[k]}")

    type_dict = dict(CustomerLicense.TYPE_CHOICES)
    status_dict = dict(CustomerLicense.STATUS_CHOICES)
    approval_dict = dict(CustomerLicense.APPROVAL_CHOICES)

    rows = []
    for lic in licenses:
        contract_nos = ", ".join([mc.contract_no for mc in lic.maintenance_contracts.all()])
        vals = lic.custom_values or {}
        schema = (lic.product.custom_field_schema or []) if lic.product else []
        schema_dict = {item.get("key"): item for item in schema if item.get("key")}

        row = [
            lic.license_no,
            approval_dict.get(lic.approval_status, lic.approval_status),
            lic.customer.code if lic.customer else "",
            lic.customer.name if lic.customer else "",
            lic.product.code if lic.product else "",
            lic.product.name if lic.product else "",
            type_dict.get(lic.license_type, lic.license_type),
            lic.quantity,
            lic.unit,
            lic.start_date or "",
            lic.end_date or "",
            lic.is_perpetual,
            status_dict.get(lic.status, lic.status),
            contract_nos,
            lic.license_key or "",
            lic.note or "",
            lic.created_at,
        ]

        # 填入各動態自訂規格
        for k in custom_field_keys:
            raw_val = vals.get(k)
            schema_item = schema_dict.get(k)
            if (raw_val is None or raw_val == "") and schema_item:
                raw_val = schema_item.get("default", "")

            if raw_val in (None, ""):
                display_val = ""
            elif isinstance(raw_val, list):
                display_val = ", ".join(str(x) for x in raw_val if str(x).strip())
            elif schema_item and schema_item.get("type") == "boolean":
                display_val = "是" if raw_val in (True, "true", "True", 1, "1", "是") else "否"
            else:
                display_val = str(raw_val)

            row.append(display_val)

        rows.append(row)

    return export_to_excel("licenses", "客戶軟體授權清單", headers, rows)


def _snapshot_license_core_fields(lic: CustomerLicense) -> dict:
    return {
        "customer_id": lic.customer_id,
        "product_id": lic.product_id,
        "license_no": lic.license_no,
        "quantity": lic.quantity,
        "unit": lic.unit,
        "start_date": lic.start_date,
        "end_date": None if lic.is_perpetual else lic.end_date,
        "is_perpetual": bool(lic.is_perpetual or lic.license_type == CustomerLicense.TYPE_PERPETUAL),
        "is_disabled": bool(lic.is_disabled),
        "license_key": (lic.license_key or "").strip(),
        "custom_values": dict(lic.custom_values or {}),
    }


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


@login_required
def license_detail(request, pk):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")

    license_obj = get_object_or_404(
        CustomerLicense.objects.select_related("customer", "product", "created_by", "reviewed_by"),
        pk=pk,
    )
    if not user_can_access_customer(request.user, license_obj.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("license-list")

    can_edit = user_can_edit_licenses(request.user)
    can_approve = user_can_approve_licenses(request.user)
    custom_display = license_obj.custom_specs_display

    return render(
        request,
        "licenses/license_detail.html",
        {
            "license": license_obj,
            "can_edit_crm": can_edit,
            "can_edit_licenses": can_edit,
            "can_approve_licenses": can_approve,
            "custom_display": custom_display,
        },
    )


@login_required
def license_create(request):
    if not user_can_edit_licenses(request.user):
        return HttpResponseForbidden("您沒有權限新增軟體授權書")

    initial = {}
    customer_id = request.GET.get("customer")
    if customer_id:
        cust = get_object_or_404(Customer, pk=customer_id)
        if not user_can_access_customer(request.user, cust):
            return HttpResponseForbidden("您沒有權限存取此客戶")
        initial["customer"] = cust

    product_id = request.GET.get("product")
    if product_id:
        initial["product"] = get_object_or_404(Product, pk=product_id)

    requires_approval = is_license_approval_required_for_user(request.user)

    if request.method == "POST":
        form = CustomerLicenseForm(request.POST)
        form.fields["customer"].queryset = filter_customers_for_user(request.user, Customer.objects.all())
        if form.is_valid():
            license_obj = form.save(commit=False)
            license_obj.created_by = request.user
            if requires_approval:
                license_obj.approval_status = CustomerLicense.APPROVAL_PENDING
                license_obj.reviewed_by = None
                license_obj.reviewed_at = None
                license_obj.rejection_reason = ""
            else:
                license_obj.approval_status = CustomerLicense.APPROVAL_APPROVED
                license_obj.reviewed_by = request.user
                license_obj.reviewed_at = timezone.now()
                license_obj.rejection_reason = ""
            license_obj.save()

            summary_detail = (
                f"產品：{license_obj.product.name}\n"
                f"數量：{license_obj.quantity} {license_obj.unit}\n"
                f"授權期間：{license_obj.start_date or '-'} ~ {license_obj.end_date or '永久'}\n"
                f"狀態：{license_obj.status_display}"
            )

            if requires_approval:
                log_action(
                    user=request.user,
                    action="送出軟體授權書簽核申請",
                    target_type="CustomerLicense",
                    target_identifier=license_obj.license_no,
                    detail=f"客戶: {license_obj.customer.name}, 產品: {license_obj.product.name}, 數量: {license_obj.quantity}{license_obj.unit} (待主管覆核)",
                    ip_address=_client_ip(request),
                )
                notify_approval_submitted(
                    module_type="license",
                    customer=license_obj.customer,
                    actor=request.user,
                    target_identifier=license_obj.license_no,
                    event_detail=summary_detail,
                    review_path=reverse("license-detail", kwargs={"pk": license_obj.pk}),
                )
                messages.warning(
                    request,
                    f"已送出軟體授權書「{license_obj.license_no}」簽核申請！目前為「待覆核」狀態，待具備覆核權限之人員核准後即正式成立。",
                )
            else:
                log_action(
                    user=request.user,
                    action="新增軟體授權書",
                    target_type="CustomerLicense",
                    target_identifier=license_obj.license_no,
                    detail=f"客戶: {license_obj.customer.name}, 產品: {license_obj.product.name}, 數量: {license_obj.quantity}{license_obj.unit}",
                    ip_address=_client_ip(request),
                )
                if license_obj.customer:
                    notify_customer_followers(
                        customer=license_obj.customer,
                        actor=request.user,
                        event_category="data_change",
                        event_title=f"新增軟體授權書：{license_obj.license_no}",
                        event_detail=summary_detail,
                    )
                messages.success(request, f"成功建立軟體授權書：{license_obj.license_no}")

            if customer_id:
                return redirect("customer-detail", pk=customer_id)
            return redirect("license-detail", pk=license_obj.pk)
    else:
        from apps.core.numbering import generate_license_no

        initial.setdefault("license_no", generate_license_no())
        form = CustomerLicenseForm(initial=initial)
        form.fields["customer"].queryset = filter_customers_for_user(request.user, Customer.objects.all())

    return render(
        request,
        "licenses/license_form.html",
        {
            "form": form,
            "is_create": True,
            "customer_id": customer_id,
            "requires_approval": requires_approval,
        },
    )


@login_required
def license_update(request, pk):
    if not user_can_edit_licenses(request.user):
        return HttpResponseForbidden("您沒有權限修改軟體授權書")

    license_obj = get_object_or_404(CustomerLicense, pk=pk)
    if not user_can_access_customer(request.user, license_obj.customer):
        return HttpResponseForbidden("您沒有權限修改此客戶的軟體授權書")

    old_core = _snapshot_license_core_fields(license_obj)
    old_approval_status = license_obj.approval_status
    requires_approval = is_license_approval_required_for_user(request.user)

    if request.method == "POST":
        form = CustomerLicenseForm(request.POST, instance=license_obj)
        form.fields["customer"].queryset = filter_customers_for_user(request.user, Customer.objects.all())
        if form.is_valid():
            updated_obj = form.save(commit=False)
            new_core = _snapshot_license_core_fields(updated_obj)
            core_changed = old_core != new_core

            resubmitted_for_approval = False
            if not requires_approval:
                # 若編輯者本身具備覆核權限（或簽核開關為關閉），編輯儲存即為核准狀態
                if old_approval_status == CustomerLicense.APPROVAL_REJECTED or (
                    old_approval_status == CustomerLicense.APPROVAL_PENDING and user_can_approve_licenses(request.user)
                ):
                    updated_obj.approval_status = CustomerLicense.APPROVAL_APPROVED
                    updated_obj.reviewed_by = request.user
                    updated_obj.reviewed_at = timezone.now()
                    updated_obj.rejection_reason = ""
            else:
                # 編輯者無覆核權限且簽核開關開啟：
                # 1. 原為已退回 -> 修改後重送待覆核
                # 2. 原為已核准但修改了核心商務條件 -> 自動變回待覆核（僅改備註則維持已核准）
                if old_approval_status == CustomerLicense.APPROVAL_REJECTED or (
                    old_approval_status == CustomerLicense.APPROVAL_APPROVED and core_changed
                ):
                    updated_obj.approval_status = CustomerLicense.APPROVAL_PENDING
                    updated_obj.created_by = request.user
                    updated_obj.reviewed_by = None
                    updated_obj.reviewed_at = None
                    updated_obj.rejection_reason = ""
                    resubmitted_for_approval = True

            updated_obj.save()

            summary_detail = (
                f"產品：{updated_obj.product.name}\n"
                f"數量：{updated_obj.quantity} {updated_obj.unit}\n"
                f"授權期間：{updated_obj.start_date or '-'} ~ {updated_obj.end_date or '永久'}\n"
                f"狀態：{updated_obj.status_display}"
            )

            if resubmitted_for_approval:
                log_action(
                    user=request.user,
                    action="修改軟體授權書並重送覆核",
                    target_type="CustomerLicense",
                    target_identifier=updated_obj.license_no,
                    detail=f"更新客戶: {updated_obj.customer.name}, 產品: {updated_obj.product.name} (轉為待覆核)",
                    ip_address=_client_ip(request),
                )
                notify_approval_submitted(
                    module_type="license",
                    customer=updated_obj.customer,
                    actor=request.user,
                    target_identifier=updated_obj.license_no,
                    event_detail=summary_detail,
                    review_path=reverse("license-detail", kwargs={"pk": updated_obj.pk}),
                )
                messages.warning(
                    request,
                    f"已更新軟體授權書「{updated_obj.license_no}」並重新送出覆核，待主管核准後即正式生效。",
                )
            else:
                log_action(
                    user=request.user,
                    action="修改軟體授權書",
                    target_type="CustomerLicense",
                    target_identifier=updated_obj.license_no,
                    detail=f"更新客戶: {updated_obj.customer.name}, 產品: {updated_obj.product.name}",
                    ip_address=_client_ip(request),
                )
                if updated_obj.approval_status == CustomerLicense.APPROVAL_APPROVED and updated_obj.customer:
                    notify_customer_followers(
                        customer=updated_obj.customer,
                        actor=request.user,
                        event_category="data_change",
                        event_title=f"更新軟體授權書：{updated_obj.license_no}",
                        event_detail=summary_detail,
                    )
                messages.success(request, f"成功更新軟體授權書：{updated_obj.license_no}")
            return redirect("license-detail", pk=updated_obj.pk)
    else:
        form = CustomerLicenseForm(instance=license_obj)
        form.fields["customer"].queryset = filter_customers_for_user(request.user, Customer.objects.all())

    return render(
        request,
        "licenses/license_form.html",
        {
            "form": form,
            "is_create": False,
            "license": license_obj,
            "requires_approval": requires_approval,
        },
    )


def _execute_license_approve(lic: CustomerLicense, reviewer, ip_address=None) -> bool:
    if lic.approval_status == CustomerLicense.APPROVAL_APPROVED:
        return False
    lic.approval_status = CustomerLicense.APPROVAL_APPROVED
    lic.reviewed_by = reviewer
    lic.reviewed_at = timezone.now()
    lic.rejection_reason = ""
    lic.save()

    summary_detail = (
        f"產品：{lic.product.name}\n"
        f"數量：{lic.quantity} {lic.unit}\n"
        f"授權期間：{lic.start_date or '-'} ~ {lic.end_date or '永久'}\n"
        f"狀態：{lic.status_display}"
    )

    log_action(
        user=reviewer,
        action="覆核核准軟體授權書",
        target_type="CustomerLicense",
        target_identifier=lic.license_no,
        detail=f"客戶: {lic.customer.name}, 產品: {lic.product.name}",
        ip_address=ip_address,
    )
    notify_approval_decision(
        module_type="license",
        decision="approved",
        applicant=lic.created_by,
        reviewer=reviewer,
        customer=lic.customer,
        target_identifier=lic.license_no,
        detail_text=summary_detail,
        target_path=reverse("license-detail", kwargs={"pk": lic.pk}),
    )
    if lic.customer:
        notify_customer_followers(
            customer=lic.customer,
            actor=reviewer,
            event_category="data_change",
            event_title=f"軟體授權書已覆核生效：{lic.license_no}",
            event_detail=summary_detail,
        )
    return True


@login_required
@require_POST
def license_approve(request, pk):
    if not user_can_approve_licenses(request.user):
        messages.error(request, "權限不足：您沒有覆核軟體授權書的權限。")
        return redirect("license-detail", pk=pk)

    lic = get_object_or_404(
        CustomerLicense.objects.select_related("customer", "product", "created_by"),
        pk=pk,
    )
    if not user_can_access_customer(request.user, lic.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("license-list")

    if lic.created_by_id and lic.created_by_id == request.user.id and not request.user.is_superuser:
        messages.error(request, "簽核規範限制：不可覆核自己送審的單據，請由其他具備覆核權限的人員審核。")
    elif _execute_license_approve(lic, request.user, ip_address=_client_ip(request)):
        messages.success(request, f"已核准軟體授權書「{lic.license_no}」，現已正式生效並可匯出證書！")
    else:
        messages.info(request, f"軟體授權書「{lic.license_no}」已是核准狀態。")

    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("license-detail", pk=pk)


@login_required
@require_POST
def license_batch_approve(request):
    if not user_can_approve_licenses(request.user):
        messages.error(request, "權限不足：您沒有覆核軟體授權書的權限。")
        return redirect("license-list")

    raw_ids = request.POST.getlist("selected_ids")
    valid_ids = [int(x) for x in raw_ids if str(x).isdigit()]
    if not valid_ids:
        messages.warning(request, "請先勾選至少一筆待覆核的軟體授權書。")
        return redirect(request.POST.get("next") or "license-list")

    qs = filter_by_customer_scope(
        request.user,
        CustomerLicense.objects.filter(
            pk__in=valid_ids,
            approval_status=CustomerLicense.APPROVAL_PENDING,
        ).select_related("customer", "product", "created_by"),
    )

    approved_cnt = 0
    skipped_self = 0
    ip_addr = _client_ip(request)
    for lic in qs:
        if lic.created_by_id and lic.created_by_id == request.user.id and not request.user.is_superuser:
            skipped_self += 1
            continue
        if _execute_license_approve(lic, request.user, ip_address=ip_addr):
            approved_cnt += 1

    if approved_cnt > 0:
        messages.success(request, f"已批次覆核通過 {approved_cnt} 筆軟體授權書！")
    if skipped_self > 0:
        messages.warning(request, f"略過 {skipped_self} 筆由您本人送審的單據（需由其他覆核主管審核）。")
    if approved_cnt == 0 and skipped_self == 0:
        messages.info(request, "所選項目中沒有可覆核之待審授權書。")

    return redirect(request.POST.get("next") or "license-list")


@login_required
@require_POST
def license_reject(request, pk):
    if not user_can_approve_licenses(request.user):
        messages.error(request, "權限不足：您沒有退回軟體授權書的權限。")
        return redirect("license-detail", pk=pk)

    lic = get_object_or_404(
        CustomerLicense.objects.select_related("customer", "product", "created_by"),
        pk=pk,
    )
    if not user_can_access_customer(request.user, lic.customer):
        messages.error(request, "權限不足：此客戶不在您的關注/負責範圍內。")
        return redirect("license-list")

    reason = (request.POST.get("rejection_reason") or "").strip()
    next_url = request.POST.get("next") or request.GET.get("next")
    if not reason:
        messages.error(request, "退回單據時必須填寫「退回原因說明」。")
        return redirect(next_url or reverse("license-detail", kwargs={"pk": pk}))

    lic.approval_status = CustomerLicense.APPROVAL_REJECTED
    lic.reviewed_by = request.user
    lic.reviewed_at = timezone.now()
    lic.rejection_reason = reason
    lic.save()

    summary_detail = (
        f"產品：{lic.product.name}\n"
        f"數量：{lic.quantity} {lic.unit}\n"
        f"授權期間：{lic.start_date or '-'} ~ {lic.end_date or '永久'}"
    )

    log_action(
        user=request.user,
        action="退回軟體授權書",
        target_type="CustomerLicense",
        target_identifier=lic.license_no,
        detail=f"退回原因: {reason}",
        ip_address=_client_ip(request),
    )
    notify_approval_decision(
        module_type="license",
        decision="rejected",
        applicant=lic.created_by,
        reviewer=request.user,
        customer=lic.customer,
        target_identifier=lic.license_no,
        detail_text=summary_detail,
        target_path=reverse("license-detail", kwargs={"pk": lic.pk}),
        rejection_reason=reason,
    )
    messages.warning(request, f"已退回軟體授權書「{lic.license_no}」，並通知原開單人員修正。")
    if next_url:
        return redirect(next_url)
    return redirect("license-detail", pk=pk)


@login_required
def license_delete(request, pk):
    if not user_can_edit_licenses(request.user):
        return HttpResponseForbidden("您沒有權限刪除軟體授權書")

    license_obj = get_object_or_404(CustomerLicense, pk=pk)
    if not user_can_access_customer(request.user, license_obj.customer):
        return HttpResponseForbidden("您沒有權限刪除此客戶的軟體授權書")
    customer_pk = license_obj.customer_id
    customer_obj = license_obj.customer
    product_name = license_obj.product.name if license_obj.product else ""
    license_no = license_obj.license_no
    next_url = request.GET.get("next") or request.POST.get("next")

    if request.method == "POST":
        license_obj.delete()
        log_action(
            user=request.user,
            action="刪除軟體授權書",
            target_type="CustomerLicense",
            target_identifier=license_no,
            detail=f"刪除授權號: {license_no}",
        )
        if customer_obj:
            notify_customer_followers(
                customer=customer_obj,
                actor=request.user,
                event_category="data_change",
                event_title=f"刪除軟體授權書：{license_no}",
                event_detail=f"已刪除軟體授權書「{license_no} ({product_name})」",
            )
        messages.success(request, f"已刪除軟體授權書：{license_no}")
        if next_url:
            return redirect(next_url)
        if customer_pk:
            return redirect("customer-detail", pk=customer_pk)
        return redirect("license-list")

    return render(
        request,
        "licenses/license_confirm_delete.html",
        {
            "license": license_obj,
            "customer_pk": customer_pk,
            "next": next_url,
        },
    )


# ---------------------------------------------------------------------------
# 產品主檔 (Product) Views
# ---------------------------------------------------------------------------

@login_required
def product_list(request):
    if not user_can_view_crm(request.user):
        messages.error(request, "權限不足：您的角色僅限查詢硬體設備庫存。")
        return redirect("hardware-dashboard")

    products = Product.objects.prefetch_related("licenses").all()
    can_edit_prod = user_can_edit_products(request.user)
    can_edit_lic = user_can_edit_licenses(request.user)

    return render(
        request,
        "licenses/product_list.html",
        {
            "products": products,
            "can_edit_crm": can_edit_prod,
            "can_edit_products": can_edit_prod,
            "can_edit_licenses": can_edit_lic,
        },
    )


@login_required
def product_create(request):
    if not user_can_edit_products(request.user):
        return HttpResponseForbidden("您沒有權限新增軟體產品")

    if request.method == "POST":
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            log_action(
                user=request.user,
                action="新增產品主檔",
                target_type="Product",
                target_identifier=product.code,
                detail=f"名稱: {product.name}",
            )
            messages.success(request, f"成功新增產品：{product.name}")
            return redirect("product-list")
    else:
        form = ProductForm()

    return render(
        request,
        "licenses/product_form.html",
        {"form": form, "is_create": True},
    )


@login_required
def product_update(request, pk):
    if not user_can_edit_products(request.user):
        return HttpResponseForbidden("您沒有權限修改軟體產品")

    product = get_object_or_404(Product, pk=pk)

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            log_action(
                user=request.user,
                action="修改產品主檔",
                target_type="Product",
                target_identifier=product.code,
                detail=f"更新名稱: {product.name}",
            )
            messages.success(request, f"成功更新產品：{product.name}")
            return redirect("product-list")
    else:
        form = ProductForm(instance=product)

    return render(
        request,
        "licenses/product_form.html",
        {
            "form": form,
            "product": product,
            "is_create": False,
        },
    )


@login_required
def product_delete(request, pk):
    if not user_can_edit_products(request.user):
        return HttpResponseForbidden("您沒有權限刪除軟體產品")

    product = get_object_or_404(Product, pk=pk)
    if product.licenses.exists():
        messages.error(request, f"無法刪除產品 [{product.name}]，因為已有關聯之客戶授權書！")
        return redirect("product-list")

    if request.method == "POST":
        code = product.code
        name = product.name
        product.delete()
        log_action(
            user=request.user,
            action="刪除產品主檔",
            target_type="Product",
            target_identifier=code,
            detail=f"刪除產品: {name}",
        )
        messages.success(request, f"已刪除產品：{name}")
        return redirect("product-list")

    return render(
        request,
        "licenses/product_confirm_delete.html",
        {"product": product},
    )


# ---------------------------------------------------------------------------
# API: 動態取得產品自訂欄位 Schema
# ---------------------------------------------------------------------------

@login_required
def product_meta_api(request, pk):
    product = get_object_or_404(Product, pk=pk)
    return JsonResponse(
        {
            "id": product.id,
            "code": product.code,
            "name": product.name,
            "default_unit": product.default_unit,
            "custom_field_schema": product.custom_field_schema or [],
        }
    )
