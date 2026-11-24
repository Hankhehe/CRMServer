import json
import shutil
import datetime
import tempfile
from pathlib import Path
import pyodbc
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.management import call_command
from django.db import connections
from django.db.models import Count, Q
from django.http import JsonResponse, FileResponse
from django.conf import settings
from django.utils import timezone

from .models import AuditLog, OuPermission, Role, SystemSetting, UserProfile
from .permissions import (
    user_can_manage_system,
    user_can_view_crm,
    user_can_edit_customers,
    user_can_edit_licenses,
    user_can_edit_products,
    user_can_edit_probe,
    user_can_edit_crm,
    filter_customers_for_user,
    filter_by_customer_scope,
    is_user_followed_only_scope,
    log_action,
    cleanup_expired_audit_logs,
    get_ad_groups,
)
from .forms import (
    CustomAuthenticationForm,
    LocalUserCreationForm,
    LocalUserEditForm,
    OuPermissionForm,
    RoleForm,
    SystemSettingForm,
    SmtpSettingForm,
    AccountNotificationPreferenceForm,
    IdleTimeoutSettingForm,
    ApprovalWorkflowSettingForm,
)
from .db_migrator import execute_full_database_migration_and_switch
from .notification_service import send_test_smtp_email
from .backup_service import (
    create_backup,
    list_backups,
    get_backup_file,
    delete_backup,
    restore_backup,
)
from apps.customers.models import Customer, MaintenanceContract, CustomerFollow, CustomerActivityLog
from apps.hardware.models import HardwareDevice as Probe, InventoryTransaction, HardwareModel as ProbeModelDetail
from apps.licenses.models import CustomerLicense

User = get_user_model()


def _client_ip(request):
    f = request.META.get("HTTP_X_FORWARDED_FOR")
    return f.split(",")[0].strip() if f else request.META.get("REMOTE_ADDR")


def login_view(request):
    if request.user.is_authenticated:
        if not user_can_view_crm(request.user):
            return redirect("hardware-dashboard")
        return redirect("dashboard")

    if request.method == "POST":
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            log_action(
                user=user,
                action="使用者登入",
                target_type="Auth",
                target_identifier=user.username,
                ip_address=_client_ip(request),
            )
            messages.success(request, f"歡迎回來，{user.username}！")
            next_url = request.GET.get("next")
            if not next_url:
                next_url = "dashboard" if user_can_view_crm(user) else "hardware-dashboard"
            return redirect(next_url)
    else:
        form = CustomAuthenticationForm(request)

    return render(request, "core/login.html", {"form": form})


def logout_view(request):
    if request.user.is_authenticated:
        log_action(
            user=request.user,
            action="使用者登出",
            target_type="Auth",
            target_identifier=request.user.username,
            ip_address=_client_ip(request),
        )
    logout(request)
    if not request.GET.get("auto"):
        messages.info(request, "您已成功登出系統。")
    return redirect("login")


@login_required
def dashboard(request):
    if not user_can_view_crm(request.user):
        return redirect("hardware-dashboard")

    today = timezone.localdate()
    in_30_days = today + datetime.timedelta(days=30)

    # CRM Metrics (Apply customer_scope filtering)
    visible_customers = filter_customers_for_user(request.user, Customer.objects.all())
    visible_contracts = filter_by_customer_scope(
        request.user, MaintenanceContract.objects.select_related("customer").all()
    )
    visible_licenses = filter_by_customer_scope(
        request.user, CustomerLicense.objects.select_related("customer", "product").all()
    )

    total_customers = visible_customers.count()
    active_contracts = visible_contracts.filter(
        approval_status="approved", is_terminated=False, end_date__gte=today
    ).count()
    expiring_soon_contracts = visible_contracts.filter(
        approval_status="approved", is_terminated=False, end_date__gte=today, end_date__lte=in_30_days
    )
    expired_contracts = visible_contracts.filter(
        approval_status="approved", is_terminated=False, end_date__lt=today
    ).count()

    # Probe Metrics
    probe_counts = Probe.objects.filter(is_deleted=False).aggregate(
        total=Count("id"),
        available=Count("id", filter=Q(status=Probe.STATUS_AVAILABLE)),
        shipped=Count("id", filter=Q(status=Probe.STATUS_SHIPPED)),
        loaned=Count("id", filter=Q(status=Probe.STATUS_LOANED)),
        pending_outbound=Count(
            "id",
            filter=Q(status__in=[Probe.STATUS_PENDING_SHIP, Probe.STATUS_PENDING_LOAN]),
        ),
        scrapped=Count("id", filter=Q(status=Probe.STATUS_SCRAPPED)),
    )

    # Loan Overdue / Returning soon probes
    loaned_probes = Probe.objects.filter(
        status=Probe.STATUS_LOANED, is_deleted=False
    ).select_related("model", "customer").order_by("expected_return_at")

    # Recent Transactions
    recent_transactions = InventoryTransaction.objects.select_related(
        "device", "customer", "created_by"
    ).order_by("-occurred_at")[:8]

    # License Metrics
    active_licenses = (
        visible_licenses.filter(approval_status="approved", is_disabled=False)
        .filter(Q(start_date__isnull=True) | Q(start_date__lte=today))
        .filter(
            Q(is_perpetual=True)
            | Q(license_type=CustomerLicense.TYPE_PERPETUAL)
            | Q(end_date__isnull=True)
            | Q(end_date__gte=today)
        )
        .count()
    )
    expiring_soon_licenses = (
        visible_licenses.filter(
            approval_status="approved",
            is_disabled=False,
            is_perpetual=False,
            end_date__gte=today,
            end_date__lte=in_30_days,
        )
        .exclude(license_type=CustomerLicense.TYPE_PERPETUAL)
    )

    from apps.orders.models import Order
    from django.db.models import Sum
    from decimal import Decimal
    visible_orders = filter_by_customer_scope(
        request.user, Order.objects.select_related("customer", "salesperson").prefetch_related("payments"), customer_lookup="customer"
    )
    pending_payment_orders = visible_orders.filter(status=Order.STATUS_PENDING_PAYMENT).order_by("shipped_at", "id")
    pending_payment_orders_count = pending_payment_orders.count()
    pending_payment_orders_total = pending_payment_orders.aggregate(s=Sum("total_amount"))["s"] or Decimal("0")
    order_pipeline_alerts = visible_orders.filter(
        status__in=[
            Order.STATUS_PENDING_APPROVAL,
            Order.STATUS_PENDING_SHIPMENT,
            Order.STATUS_PENDING_PAYMENT,
        ]
    ).order_by("-updated_at")[:8]

    return render(
        request,
        "core/dashboard.html",
        {
            "today": today,
            "total_customers": total_customers,
            "active_contracts": active_contracts,
            "expiring_soon_contracts": expiring_soon_contracts[:6],
            "expiring_soon_count": expiring_soon_contracts.count(),
            "expired_contracts": expired_contracts,
            "active_licenses": active_licenses,
            "expiring_soon_licenses": expiring_soon_licenses[:6],
            "expiring_licenses_count": expiring_soon_licenses.count(),
            "probe_counts": probe_counts,
            "loaned_probes": loaned_probes[:6],
            "loaned_count": loaned_probes.count(),
            "recent_transactions": recent_transactions,
            "pending_payment_orders_count": pending_payment_orders_count,
            "pending_payment_orders_total": pending_payment_orders_total,
            "order_pipeline_alerts": order_pipeline_alerts,
            "can_edit_probe": user_can_edit_probe(request.user),
            "can_edit_hardware": user_can_edit_probe(request.user),
            "can_edit_customers": user_can_edit_customers(request.user),
            "can_edit_licenses": user_can_edit_licenses(request.user),
            "can_edit_crm": user_can_edit_crm(request.user),
            "is_followed_only_scope": is_user_followed_only_scope(request.user),
        },
    )


@login_required
def change_own_password(request):
    if hasattr(request.user, "profile") and request.user.profile.distinguished_name:
        messages.error(request, "AD 網域帳號請在 Windows 網域環境中修改密碼。")
        return redirect("dashboard")

    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            log_action(
                user=user,
                action="修改個人密碼",
                target_type="Auth",
                target_identifier=user.username,
                ip_address=_client_ip(request),
            )
            messages.success(request, "您的密碼已成功修改！")
            return redirect("dashboard")
    else:
        form = PasswordChangeForm(request.user)

    return render(request, "core/own_password_change.html", {"form": form})


@login_required
def account_center(request):
    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)
    active_tab = request.GET.get("tab", "followed")
    if not user_can_view_crm(user) and active_tab in ("followed", "comments"):
        active_tab = "settings"
    q_followed = request.GET.get("q_followed", "").strip()
    q_comments = request.GET.get("q_comments", "").strip()
    comment_type = request.GET.get("comment_type", "").strip()

    if request.method == "POST":
        action = request.POST.get("action", "save_preferences")
        if action == "save_preferences":
            pref_form = AccountNotificationPreferenceForm(request.POST)
            if pref_form.is_valid():
                user.email = pref_form.cleaned_data.get("email", "").strip()
                user.first_name = pref_form.cleaned_data.get("first_name", "").strip()
                user.last_name = pref_form.cleaned_data.get("last_name", "").strip()
                user.save()

                profile.receive_email_notifications = pref_form.cleaned_data.get(
                    "receive_email_notifications", False
                )
                profile.notify_on_new_comment = pref_form.cleaned_data.get(
                    "notify_on_new_comment", False
                )
                profile.notify_on_data_change = pref_form.cleaned_data.get(
                    "notify_on_data_change", False
                )
                profile.save()

                log_action(
                    user=user,
                    action="更新個人帳號中心與通知偏好",
                    target_type="UserProfile",
                    target_identifier=user.username,
                    detail=f"Email: {user.email or '未填'}, 總開關: {profile.receive_email_notifications}, 留言通知: {profile.notify_on_new_comment}, 變更通知: {profile.notify_on_data_change}",
                    ip_address=_client_ip(request),
                )
                messages.success(request, "您的個人資料與 Email 通知偏好設定已儲存！")
                return redirect("/account-center/?tab=settings")
            else:
                active_tab = "settings"
    else:
        pref_form = AccountNotificationPreferenceForm(
            initial={
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "receive_email_notifications": profile.receive_email_notifications,
                "notify_on_new_comment": profile.notify_on_new_comment,
                "notify_on_data_change": profile.notify_on_data_change,
            }
        )

    # 1. 我關注的客戶清單
    follows_qs = (
        CustomerFollow.objects.filter(user=user)
        .select_related("customer")
        .prefetch_related("customer__contracts", "customer__followers")
        .order_by("-created_at")
    )
    total_followed_count = follows_qs.count()
    if q_followed:
        follows_qs = follows_qs.filter(
            Q(customer__code__icontains=q_followed)
            | Q(customer__name__icontains=q_followed)
            | Q(customer__tax_id__icontains=q_followed)
            | Q(customer__contact__icontains=q_followed)
        )

    # 2. 我發表過的留言 (客戶執行軌跡)
    my_comments_qs = (
        CustomerActivityLog.objects.filter(created_by=user)
        .select_related("customer", "contact")
        .prefetch_related("attachments")
        .order_by("-created_at")
    )
    total_comments_count = my_comments_qs.count()
    if q_comments:
        my_comments_qs = my_comments_qs.filter(
            Q(content__icontains=q_comments)
            | Q(customer__name__icontains=q_comments)
            | Q(customer__code__icontains=q_comments)
            | Q(contact__name__icontains=q_comments)
        )
    if comment_type:
        my_comments_qs = my_comments_qs.filter(activity_type=comment_type)

    comments_paginator = Paginator(my_comments_qs, 20)
    comments_page = comments_paginator.get_page(request.GET.get("cpage"))

    return render(
        request,
        "core/account_center.html",
        {
            "profile": profile,
            "pref_form": pref_form,
            "active_tab": active_tab,
            "followed_records": follows_qs,
            "total_followed_count": total_followed_count,
            "q_followed": q_followed,
            "comments_page": comments_page,
            "total_comments_count": total_comments_count,
            "filtered_comments_count": comments_paginator.count,
            "q_comments": q_comments,
            "comment_type": comment_type,
            "activity_type_choices": CustomerActivityLog.ACTIVITY_TYPE_CHOICES,
        },
    )


# Account & Role Management
@login_required
def account_list(request):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足：僅限系統管理員管理帳號與角色。")
        return redirect("dashboard")

    Role.ensure_system_roles()
    active_tab = request.GET.get("tab", "accounts")
    system_settings = SystemSetting.get_settings()
    timeout_form = IdleTimeoutSettingForm(instance=system_settings)
    approval_form = ApprovalWorkflowSettingForm(instance=system_settings)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_idle_timeout":
            timeout_form = IdleTimeoutSettingForm(request.POST, instance=system_settings)
            if timeout_form.is_valid():
                timeout_form.save()
                log_action(
                    user=request.user,
                    action="更新閒置登出時間設定",
                    target_type="SystemSetting",
                    target_identifier=f"{system_settings.idle_timeout_minutes} 分鐘",
                    ip_address=_client_ip(request),
                )
                messages.success(request, f"閒置自動登出時間已成功更新為 {system_settings.idle_timeout_minutes} 分鐘！")
                return redirect("/accounts/?tab=timeout")
            else:
                active_tab = "timeout"
        elif action == "save_approval_workflow":
            approval_form = ApprovalWorkflowSettingForm(request.POST, instance=system_settings)
            if approval_form.is_valid():
                approval_form.save()
                log_action(
                    user=request.user,
                    action="更新全域簽核覆核流程開關",
                    target_type="SystemSetting",
                    target_identifier="ApprovalWorkflow",
                    detail=(
                        f"軟體授權簽核: {'開啟' if system_settings.require_license_approval else '關閉'}, "
                        f"維護保固簽核: {'開啟' if system_settings.require_warranty_approval else '關閉'}, "
                        f"硬體出庫簽核: {'開啟' if system_settings.require_hardware_approval else '關閉'}"
                    ),
                    ip_address=_client_ip(request),
                )
                messages.success(request, "全域簽核覆核開關設定已成功儲存！（既有待覆核單據將維持待覆核狀態，供主管審理或一鍵放行）")
                return redirect("/accounts/?tab=approval")
            else:
                active_tab = "approval"
        elif action in ("approve_all_pending_licenses", "approve_all_pending_contracts", "approve_all_pending_hardware"):
            now_ts = timezone.now()
            if action == "approve_all_pending_licenses":
                pending_qs = list(CustomerLicense.objects.filter(approval_status="pending").select_related("customer", "product", "created_by"))
                for lic in pending_qs:
                    lic.approval_status = "approved"
                    lic.reviewed_by = request.user
                    lic.reviewed_at = now_ts
                    lic.rejection_reason = ""
                    lic.save()
                log_action(
                    user=request.user,
                    action="系統設定一鍵核准所有待覆核軟體授權書",
                    target_type="CustomerLicense",
                    target_identifier=f"共 {len(pending_qs)} 筆",
                    ip_address=_client_ip(request),
                )
                messages.success(request, f"已一鍵核准放行 {len(pending_qs)} 筆待覆核的軟體授權書！")
            elif action == "approve_all_pending_contracts":
                pending_qs = list(MaintenanceContract.objects.filter(approval_status="pending").select_related("customer", "created_by"))
                for ct in pending_qs:
                    ct.approval_status = "approved"
                    ct.reviewed_by = request.user
                    ct.reviewed_at = now_ts
                    ct.rejection_reason = ""
                    ct.save()
                log_action(
                    user=request.user,
                    action="系統設定一鍵核准所有待覆核維護保固合約",
                    target_type="MaintenanceContract",
                    target_identifier=f"共 {len(pending_qs)} 筆",
                    ip_address=_client_ip(request),
                )
                messages.success(request, f"已一鍵核准放行 {len(pending_qs)} 筆待覆核的維護保固合約！")
            elif action == "approve_all_pending_hardware":
                from apps.hardware.approval_service import approve_hardware_outbound_request
                from apps.hardware.models import HardwareOutboundApproval
                pending_reqs = list(
                    HardwareOutboundApproval.objects.filter(approval_status="pending")
                    .select_related("device", "customer", "contract", "old_probe", "requested_by")
                )
                approved_cnt = 0
                for req_obj in pending_reqs:
                    ok, _ = approve_hardware_outbound_request(req_obj, request.user, ip_address=_client_ip(request))
                    if ok:
                        approved_cnt += 1
                messages.success(request, f"已一鍵核准放行 {approved_cnt} 筆待覆核的硬體設備出庫申請！")
            return redirect("/accounts/?tab=approval")

    users = User.objects.select_related("profile", "profile__role_obj").order_by("-is_superuser", "username")
    roles = Role.objects.annotate(user_count=Count("users")).order_by("-is_system", "name")

    from apps.hardware.models import HardwareOutboundApproval
    pending_summary = {
        "licenses": CustomerLicense.objects.filter(approval_status="pending").count(),
        "contracts": MaintenanceContract.objects.filter(approval_status="pending").count(),
        "hardware": HardwareOutboundApproval.objects.filter(approval_status="pending").count(),
    }

    return render(
        request,
        "core/account_list.html",
        {
            "users": users,
            "roles": roles,
            "active_tab": active_tab,
            "system_settings": system_settings,
            "timeout_form": timeout_form,
            "approval_form": approval_form,
            "pending_summary": pending_summary,
        },
    )


@login_required
def role_create(request):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足：僅限系統管理員管理角色。")
        return redirect("account-list")

    Role.ensure_system_roles()
    if request.method == "POST":
        form = RoleForm(request.POST)
        if form.is_valid():
            role = form.save()
            log_action(
                user=request.user,
                action="建立自訂角色",
                target_type="Role",
                target_identifier=role.name,
                detail=f"代碼: {role.code}, 權限: {role.active_permission_labels}",
                ip_address=_client_ip(request),
            )
            messages.success(request, f"成功建立角色「{role.name}」！")
            return redirect(f"/accounts/?tab=roles")
    else:
        form = RoleForm()

    return render(
        request,
        "core/role_form.html",
        {
            "form": form,
            "title": "新增自訂系統角色",
            "system_presets": Role.SYSTEM_PRESETS,
        },
    )


@login_required
def role_update(request, pk):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足：僅限系統管理員管理角色。")
        return redirect("account-list")

    role = get_object_or_404(Role, pk=pk)
    if request.method == "POST":
        form = RoleForm(request.POST, instance=role)
        if form.is_valid():
            role = form.save()
            # 即時動態同步：更新所有套用此角色的帳號 profile 欄位
            for p in role.users.select_related("user").all():
                p.customer_scope = role.customer_scope
                p.can_view_crm = role.can_view_crm
                p.can_edit_customers = role.can_edit_customers
                p.can_edit_licenses = role.can_edit_licenses
                p.can_edit_products = role.can_edit_products
                p.can_edit_probes = role.can_edit_probes
                p.can_manage_system = role.can_manage_system
                p.can_edit_crm = role.can_edit_customers and role.can_edit_licenses
                p.can_edit_orders = role.can_edit_orders
                p.can_ship_orders = role.can_ship_orders
                if role.can_approve_licenses or p.user.is_superuser:
                    p.can_approve_licenses = True
                if role.can_approve_contracts or p.user.is_superuser:
                    p.can_approve_contracts = True
                if role.can_approve_hardware or p.user.is_superuser:
                    p.can_approve_hardware = True
                if role.can_approve_orders or p.user.is_superuser:
                    p.can_approve_orders = True
                if role.can_close_orders or p.user.is_superuser:
                    p.can_close_orders = True
                p.save()

            log_action(
                user=request.user,
                action="修改系統角色",
                target_type="Role",
                target_identifier=role.name,
                detail=f"代碼: {role.code}, 權限: {role.active_permission_labels}",
                ip_address=_client_ip(request),
            )
            messages.success(request, f"已成功更新角色「{role.name}」設定，並已即時同步套用至所有人員！")
            return redirect(f"/accounts/?tab=roles")
    else:
        form = RoleForm(instance=role)

    return render(
        request,
        "core/role_form.html",
        {
            "form": form,
            "role": role,
            "title": f"編輯角色 - {role.name}",
            "system_presets": Role.SYSTEM_PRESETS,
        },
    )


@login_required
def role_delete(request, pk):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足：僅限系統管理員管理角色。")
        return redirect("account-list")

    role = get_object_or_404(Role, pk=pk)
    if role.is_system:
        messages.error(request, f"系統內建標準角色「{role.name}」受保護，不可刪除。")
        return redirect(f"/accounts/?tab=roles")

    if role.users.exists():
        count = role.users.count()
        messages.error(request, f"角色「{role.name}」目前仍有 {count} 位使用者正在使用中，請先將其更換為其他角色後再行刪除。")
        return redirect(f"/accounts/?tab=roles")

    role_name = role.name
    role.delete()
    log_action(
        user=request.user,
        action="刪除系統角色",
        target_type="Role",
        target_identifier=role_name,
        ip_address=_client_ip(request),
    )
    messages.success(request, f"已成功刪除角色「{role_name}」。")
    return redirect(f"/accounts/?tab=roles")


@login_required
def account_create(request):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足。")
        return redirect("account-list")

    if request.method == "POST":
        form = LocalUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            log_action(
                user=request.user,
                action="建立本機帳號",
                target_type="User",
                target_identifier=user.username,
                ip_address=_client_ip(request),
            )
            messages.success(request, f"成功建立帳號「{user.username}」！")
            return redirect("account-list")
    else:
        form = LocalUserCreationForm()

    return render(request, "core/account_form.html", {"form": form, "title": "建立本機帳號"})


@login_required
def account_update(request, pk):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足。")
        return redirect("account-list")

    user_obj = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = LocalUserEditForm(request.POST, instance=user_obj)
        if form.is_valid():
            form.save()
            log_action(
                user=request.user,
                action="更新帳號設定與角色",
                target_type="User",
                target_identifier=user_obj.username,
                ip_address=_client_ip(request),
            )
            messages.success(request, f"帳號「{user_obj.username}」已更新！")
            return redirect("account-list")
    else:
        form = LocalUserEditForm(instance=user_obj)

    return render(
        request,
        "core/account_form.html",
        {"form": form, "target_user": user_obj, "title": f"編輯帳號 - {user_obj.username}"},
    )


@login_required
def account_delete(request, pk):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足。")
        return redirect("account-list")

    user_obj = get_object_or_404(User, pk=pk)
    if user_obj.username == "admin":
        messages.error(request, "系統保留之 admin 帳號無法被刪除！")
        return redirect("account-list")
    if user_obj == request.user:
        messages.error(request, "無法刪除自己正在登入的帳號！")
        return redirect("account-list")

    if request.method == "POST":
        username = user_obj.username
        user_obj.delete()
        log_action(
            user=request.user,
            action="刪除帳號",
            target_type="User",
            target_identifier=username,
            ip_address=_client_ip(request),
        )
        messages.success(request, f"已成功刪除帳號「{username}」")
        return redirect("account-list")

    return render(request, "core/account_confirm_delete.html", {"user_obj": user_obj})


@login_required
def account_change_password(request, pk):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足。")
        return redirect("account-list")

    user_obj = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = SetPasswordForm(user_obj, request.POST)
        if form.is_valid():
            form.save()
            log_action(
                user=request.user,
                action="管理員重設密碼",
                target_type="User",
                target_identifier=user_obj.username,
                ip_address=_client_ip(request),
            )
            messages.success(request, f"已成功重設帳號「{user_obj.username}」的密碼！")
            return redirect("account-list")
    else:
        form = SetPasswordForm(user_obj)

    return render(
        request,
        "core/account_password_change.html",
        {"form": form, "target_user": user_obj},
    )


# LDAP / Permissions
@login_required
def ou_permission_list(request):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足：僅限系統管理員進行 LDAP 權限配置。")
        return redirect("dashboard")

    permissions = OuPermission.objects.all()
    form = OuPermissionForm()
    system_settings = SystemSetting.get_settings()
    settings_form = SystemSettingForm(instance=system_settings)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_permission":
            form = OuPermissionForm(request.POST)
            if form.is_valid():
                p = form.save()
                log_action(
                    user=request.user,
                    action="新增 AD 權限映射",
                    target_type="OuPermission",
                    target_identifier=p.ou_path,
                    ip_address=_client_ip(request),
                )
                messages.success(request, f"成功新增群組「{p.ou_path}」的角色指派！")
                return redirect("ou-permissions")
        elif action == "save_settings":
            settings_form = SystemSettingForm(request.POST, instance=system_settings)
            if settings_form.is_valid():
                settings_form.save()
                log_action(
                    user=request.user,
                    action="更新 AD 連線設定",
                    target_type="SystemSetting",
                    target_identifier="LDAP",
                    ip_address=_client_ip(request),
                )
                messages.success(request, "AD / LDAP 連線設定已更新！")
                return redirect("ou-permissions")

    return render(
        request,
        "core/permission_list.html",
        {
            "permissions": permissions,
            "form": form,
            "settings_form": settings_form,
        },
    )


@login_required
def smtp_setting_view(request):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足：僅限系統管理員進行 SMTP 郵件通知設定。")
        return redirect("dashboard")

    system_settings = SystemSetting.get_settings()
    form = SmtpSettingForm(instance=system_settings)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_smtp":
            form = SmtpSettingForm(request.POST, instance=system_settings)
            if form.is_valid():
                form.save()
                log_action(
                    user=request.user,
                    action="更新 SMTP 郵件設定",
                    target_type="SystemSetting",
                    target_identifier="SMTP",
                    ip_address=_client_ip(request),
                )
                messages.success(request, "SMTP 郵件伺服器設定已成功儲存！")
                return redirect("smtp-settings")
        elif action == "test_smtp":
            test_email = request.POST.get("test_email", "").strip() or request.user.email
            ok, msg = send_test_smtp_email(test_email)
            if ok:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect("smtp-settings")

    return render(
        request,
        "core/smtp_setting.html",
        {
            "form": form,
            "system_settings": system_settings,
        },
    )


@login_required
def ou_permission_delete(request, pk):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足。")
        return redirect("ou-permissions")

    perm = get_object_or_404(OuPermission, pk=pk)
    path = perm.ou_path
    perm.delete()
    log_action(
        user=request.user,
        action="刪除 AD 權限映射",
        target_type="OuPermission",
        target_identifier=path,
        ip_address=_client_ip(request),
    )
    messages.success(request, f"已移除群組「{path}」的權限指派。")
    return redirect("ou-permissions")


# Audit Logs
AUDIT_LOG_RETENTION_PRESETS = [
    (0, "永久保留 (不自動刪除)"),
    (7, "保留 7 天"),
    (14, "保留 14 天"),
    (30, "保留 30 天 (1 個月)"),
    (60, "保留 60 天 (2 個月)"),
    (90, "保留 90 天 (3 個月)"),
    (180, "保留 180 天 (半年)"),
    (365, "保留 365 天 (1 年)"),
    (730, "保留 730 天 (2 年)"),
]


@login_required
def audit_log_list(request):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足。")
        return redirect("dashboard")

    system_settings = SystemSetting.get_settings()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_retention":
            raw_choice = request.POST.get("retention_days", "0").strip()
            raw_custom = request.POST.get("custom_days", "").strip()
            try:
                if raw_choice == "custom":
                    days = int(raw_custom)
                else:
                    days = int(raw_choice)
                if days < 0 or days > 36500:
                    raise ValueError()
            except ValueError:
                messages.error(request, "請輸入有效的保留天數（0 代表永久保留，或 1 ~ 36500 天）。")
                return redirect("audit-log-list")

            system_settings.audit_log_retention_days = days
            system_settings.save(update_fields=["audit_log_retention_days", "updated_at"])

            deleted_count = cleanup_expired_audit_logs(days)
            label_str = "永久保留 (不自動刪除)" if days == 0 else f"保留 {days} 天"
            log_action(
                user=request.user,
                action="更新審計日誌保留期限",
                target_type="SystemSetting",
                target_identifier="AuditLog",
                detail=f"設定為「{label_str}」，立即自動刪除過期日誌 {deleted_count} 筆",
                ip_address=_client_ip(request),
            )
            if days == 0:
                messages.success(request, "審計日誌保留策略已更新為「永久保留（不自動刪除）」。")
            elif deleted_count > 0:
                messages.success(
                    request,
                    f"審計日誌保留策略已設定為「保留 {days} 天」，並已自動清除 {deleted_count} 筆過期日誌！",
                )
            else:
                messages.success(
                    request,
                    f"審計日誌保留策略已設定為「保留 {days} 天」，超過 {days} 天的日誌將自動刪除。",
                )
            return redirect("audit-log-list")

    # 每次載入列表時自動執行過期清理
    cleanup_expired_audit_logs(system_settings.audit_log_retention_days)

    query = request.GET.get("q", "").strip()
    logs = AuditLog.objects.select_related("user").all()
    if query:
        logs = logs.filter(
            Q(action__icontains=query)
            | Q(target_type__icontains=query)
            | Q(target_identifier__icontains=query)
            | Q(user__username__icontains=query)
            | Q(detail__icontains=query)
        )

    paginator = Paginator(logs, 40)
    page_obj = paginator.get_page(request.GET.get("page"))

    current_retention = system_settings.audit_log_retention_days
    preset_values = {val for val, _ in AUDIT_LOG_RETENTION_PRESETS}
    is_custom_retention = current_retention not in preset_values

    return render(
        request,
        "core/audit_log_list.html",
        {
            "page_obj": page_obj,
            "query": query,
            "total_count": paginator.count,
            "retention_days": current_retention,
            "retention_presets": AUDIT_LOG_RETENTION_PRESETS,
            "is_custom_retention": is_custom_retention,
        },
    )


@login_required
def audit_log_export(request):
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足。")
        return redirect("dashboard")

    from apps.core.excel_utils import export_to_excel

    cleanup_expired_audit_logs()

    query = request.GET.get("q", "").strip()
    logs = AuditLog.objects.select_related("user").all()
    if query:
        logs = logs.filter(
            Q(action__icontains=query)
            | Q(target_type__icontains=query)
            | Q(target_identifier__icontains=query)
            | Q(user__username__icontains=query)
            | Q(detail__icontains=query)
        )

    headers = [
        "記錄編號",
        "發生時間",
        "操作人員",
        "執行動作",
        "目標模組/型別",
        "目標代號/名稱",
        "IP 位址",
        "詳細資訊",
    ]

    rows = []
    for log in logs:
        rows.append([
            log.id,
            log.created_at,
            log.user.username if log.user else "系統",
            log.action,
            log.target_type or "",
            log.target_identifier or "",
            log.ip_address or "",
            log.detail or "",
        ])

    return export_to_excel("audit_logs", "系統審計日誌", headers, rows)


# DB Migration (SQLite <-> PostgreSQL / MSSQL)
@login_required
def db_migration_view(request):
    if not request.user.is_superuser:
        messages.error(request, "僅限超級管理員執行資料庫遷移操作。")
        return redirect("dashboard")

    db_config = {}
    if settings.DB_CONFIG_PATH.exists():
        try:
            with open(settings.DB_CONFIG_PATH, "r", encoding="utf-8") as f:
                db_config = json.load(f)
        except Exception:
            pass

    current_db = db_config.get("current_db", "sqlite")
    postgres_info = db_config.get("postgres", {})
    mssql_info = db_config.get("mssql", {})

    try:
        import psycopg2
        has_psycopg2 = True
    except ImportError:
        has_psycopg2 = False

    try:
        drivers = pyodbc.drivers()
    except Exception:
        drivers = []

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "run_migrations":
            try:
                call_command("migrate", interactive=False, verbosity=0)
                messages.success(request, f"成功對目前資料庫 ({current_db}) 套用 Migrations！")
            except Exception as e:
                messages.error(request, f"套用失敗：{str(e)}")
            return redirect("db-migration")

        elif action == "test_postgres_connection":
            if not has_psycopg2:
                return JsonResponse({
                    "success": False,
                    "message": "伺服器環境尚未安裝 psycopg2-binary 驅動套件，請先在 Python 環境安裝後再測試連線。"
                })
            host = request.POST.get("host", "127.0.0.1").strip()
            port = request.POST.get("port", "5432").strip()
            name = request.POST.get("name", "").strip()
            user = request.POST.get("user", "").strip()
            password = request.POST.get("password", "").strip()

            if not all([host, name, user]):
                return JsonResponse({"success": False, "message": "主機、資料庫名稱與帳號為必填項目"})

            try:
                conn = psycopg2.connect(
                    dbname=name,
                    user=user,
                    password=password,
                    host=host,
                    port=port,
                    connect_timeout=5,
                )
                conn.close()
                return JsonResponse({"success": True, "message": "連線測試成功！已成功連通 PostgreSQL 資料庫。"})
            except Exception as e:
                return JsonResponse({"success": False, "message": f"PostgreSQL 連線失敗: {str(e)}"})

        elif action == "switch_to_postgres":
            if not has_psycopg2:
                messages.error(request, "無法切換：系統尚未安裝 psycopg2-binary 套件！")
                return redirect("db-migration")

            host = request.POST.get("host", "127.0.0.1").strip()
            port = request.POST.get("port", "5432").strip()
            name = request.POST.get("name", "").strip()
            user = request.POST.get("user", "").strip()
            password = request.POST.get("password", "").strip()

            if not all([host, name, user]):
                messages.error(request, "主機、資料庫名稱與帳號為必填項目！")
                return redirect("db-migration")

            pg_config = {
                "host": host,
                "port": port,
                "name": name,
                "user": user,
                "password": password,
            }

            ok, msg = execute_full_database_migration_and_switch("postgres", pg_config)
            if ok:
                log_action(
                    user=request.user,
                    action="一鍵清空重建、轉移資料並熱切換至 PostgreSQL",
                    target_type="Database",
                    target_identifier=f"{host}:{port}/{name}",
                    ip_address=_client_ip(request),
                )
                messages.success(request, f"{msg} 請使用原本管理員帳號密碼登入。")
                return redirect("login")
            else:
                messages.error(request, f"切換失敗：{msg}")
                return redirect("db-migration")

        elif action == "switch_to_mssql":
            host = request.POST.get("host", "").strip()
            port = request.POST.get("port", "1433").strip()
            name = request.POST.get("name", "").strip()
            user = request.POST.get("user", "").strip()
            password = request.POST.get("password", "").strip()
            driver = request.POST.get("driver", "ODBC Driver 18 for SQL Server").strip()

            if not all([host, name, user, password]):
                messages.error(request, "主機、資料庫名稱、帳號與密碼為必填項目！")
                return redirect("db-migration")

            ms_config = {
                "host": host,
                "port": port,
                "name": name,
                "user": user,
                "password": password,
                "driver": driver,
            }

            ok, msg = execute_full_database_migration_and_switch("mssql", ms_config)
            if ok:
                log_action(
                    user=request.user,
                    action="一鍵清空重建、轉移資料並熱切換至 MS SQL",
                    target_type="Database",
                    target_identifier=f"{host}:{port}/{name}",
                    ip_address=_client_ip(request),
                )
                messages.success(request, f"{msg} 請使用原本管理員帳號密碼登入。")
                return redirect("login")
            else:
                messages.error(request, f"切換失敗：{msg}")
                return redirect("db-migration")

        elif action == "switch_to_sqlite":
            ok, msg = execute_full_database_migration_and_switch("sqlite", {})
            if ok:
                log_action(
                    user=request.user,
                    action="一鍵清空重建、轉移資料並熱切換至 SQLite",
                    target_type="Database",
                    target_identifier="db.sqlite3",
                    ip_address=_client_ip(request),
                )
                messages.success(request, f"{msg} 請使用原本管理員帳號密碼登入。")
                return redirect("login")
            else:
                messages.error(request, f"切換失敗：{msg}")
                return redirect("db-migration")

        elif action == "test_connection":
            host = request.POST.get("host")
            port = request.POST.get("port", "1433")
            name = request.POST.get("name")
            user = request.POST.get("user")
            password = request.POST.get("password")
            driver = request.POST.get("driver")

            if not all([host, name, user, password, driver]):
                return JsonResponse({"success": False, "message": "所有連線資訊皆為必填項目"})

            try:
                conn_str = f"DRIVER={{{driver}}};SERVER={host},{port};UID={user};PWD={password};DATABASE=master;Timeout=5;TrustServerCertificate=yes"
                conn = pyodbc.connect(conn_str)
                conn.close()
                return JsonResponse({"success": True, "message": "連線測試成功！可以與 SQL Server 連線。"})
            except Exception as e:
                return JsonResponse({"success": False, "message": f"連線測試失敗: {str(e)}"})

    return render(
        request,
        "core/db_migration.html",
        {
            "current_db": current_db,
            "has_psycopg2": has_psycopg2,
            "postgres_info": postgres_info,
            "mssql_info": mssql_info,
            "drivers": drivers,
        },
    )


# System Backup & Restore
@login_required
def backup_management_view(request):
    if not request.user.is_superuser:
        messages.error(request, "僅限超級管理員執行系統備份與還原操作。")
        return redirect("dashboard")

    ip = _client_ip(request)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_backup":
            try:
                target_zip = create_backup(user=request.user, ip_address=ip)
                messages.success(request, f"成功建立系統備份：{target_zip.name}")
            except Exception as e:
                messages.error(request, f"備份失敗：{str(e)}")
            return redirect("backup-management")

        elif action == "delete_backup":
            filename = request.POST.get("filename", "")
            if delete_backup(filename, user=request.user, ip_address=ip):
                messages.success(request, f"已成功刪除備份檔：{filename}")
            else:
                messages.error(request, f"刪除備份檔失敗或檔案不存在：{filename}")
            return redirect("backup-management")

        elif action == "download_backup":
            filename = request.POST.get("filename", "")
            file_path = get_backup_file(filename)
            if not file_path:
                messages.error(request, "找不到指定的備份檔案。")
                return redirect("backup-management")
            log_action(
                user=request.user,
                action="下載系統備份",
                target_type="System",
                target_identifier=filename,
                detail=f"下載備份檔案: {filename}",
                ip_address=ip,
            )
            return FileResponse(open(file_path, "rb"), as_attachment=True, filename=file_path.name)

        elif action == "restore_from_server":
            filename = request.POST.get("filename", "")
            file_path = get_backup_file(filename)
            if not file_path:
                messages.error(request, "指定的伺服器備份檔案不存在。")
                return redirect("backup-management")

            success, msg = restore_backup(file_path, user=request.user, ip_address=ip)
            if success:
                logout(request)
                messages.success(
                    request,
                    f"系統已成功還原為「{filename}」之狀態！請使用該備份中的管理員帳號密碼重新登入。",
                )
                return redirect("login")
            else:
                messages.error(request, f"還原失敗：{msg}")
                return redirect("backup-management")

        elif action == "restore_from_upload":
            upload_file = request.FILES.get("backup_file")
            if not upload_file:
                messages.error(request, "請選取要上傳的 .zip 備份檔案。")
                return redirect("backup-management")

            if not upload_file.name.endswith(".zip"):
                messages.error(request, "僅支援 .zip 格式的備份檔案。")
                return redirect("backup-management")

            # Save uploaded file temporarily to perform restore
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                for chunk in upload_file.chunks():
                    tmp.write(chunk)
                tmp_path = Path(tmp.name)

            try:
                success, msg = restore_backup(tmp_path, user=request.user, ip_address=ip)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()

            if success:
                logout(request)
                messages.success(
                    request,
                    f"系統已成功還原為上傳檔案「{upload_file.name}」之狀態！請使用該備份中的管理員帳號密碼重新登入。",
                )
                return redirect("login")
            else:
                messages.error(request, f"還原失敗：{msg}")
                return redirect("backup-management")

    backups = list_backups()
    return render(
        request,
        "core/backup_management.html",
        {
            "backups": backups,
            "total_backups": len(backups),
        },
    )

