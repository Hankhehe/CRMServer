import json
import mimetypes
import os
import re
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.customers.models import MaintenanceContract
from apps.licenses.models import CustomerLicense
from .models import SystemSetting
from .permissions import (
    log_action,
    user_can_access_customer,
    user_can_manage_system,
    user_can_view_crm,
)

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
ALLOWED_BG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
MAX_BG_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _format_roc_date(d) -> str:
    """將日期格式化為民國年格式，例如：民國 115 年 11 月 19 日"""
    if not d:
        d = timezone.now().astimezone(TAIPEI_TZ).date()
    roc_year = d.year - 1911
    return f"民國 {roc_year} 年 {d.month} 月 {d.day} 日"


def _format_period(start_date, end_date) -> str:
    """格式化期間字串 YYYY/MM/DD~YYYY/MM/DD"""
    if start_date and end_date:
        return f"{start_date.strftime('%Y/%m/%d')}~{end_date.strftime('%Y/%m/%d')}"
    if start_date:
        return f"{start_date.strftime('%Y/%m/%d')} 起"
    if end_date:
        return f"至 {end_date.strftime('%Y/%m/%d')}"
    return ""


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip())
    return cleaned[:120] or "certificate"


def _has_valid_bg(file_field) -> bool:
    if not file_field or not getattr(file_field, "name", ""):
        return False
    try:
        return file_field.storage.exists(file_field.name)
    except Exception:
        return False


def _build_warranty_defaults(contract: MaintenanceContract) -> tuple[str, str]:
    """
    依維護合約關聯的軟體授權推導預設的「產品名稱」與「保固數量」。
    若未關聯任何授權，產品名稱退回合約名稱，保固數量留空供預覽視窗微調。
    """
    related_licenses = list(contract.licenses.select_related("product").all())
    if not related_licenses:
        return (contract.name or "", "")

    product_names: list[str] = []
    qty_parts: list[str] = []
    for lic in related_licenses:
        if lic.product and lic.product.name and lic.product.name not in product_names:
            product_names.append(lic.product.name)
        qty_str = f"{lic.quantity:,} {lic.unit}".strip()
        if len(related_licenses) == 1:
            qty_parts.append(f"適用 {qty_str} 軟體授權")
        else:
            p_label = lic.product.name if lic.product else lic.license_no
            qty_parts.append(f"{p_label} {qty_str}")

    product_text = "、".join(product_names) if product_names else (contract.name or "")
    quantity_text = "、".join(qty_parts)
    return (product_text, quantity_text)


@login_required
@require_GET
def cert_data_api(request, cert_type: str, pk: int):
    """回傳指定授權書或維護合約的證書預設欄位與底圖設定 (JSON)"""
    if cert_type not in ("license", "warranty"):
        return JsonResponse({"error": "不支援的證書類型"}, status=400)

    if not user_can_view_crm(request.user):
        return JsonResponse({"error": "權限不足：無法存取 CRM 證書資料"}, status=403)

    sys_settings = SystemSetting.get_settings()
    today_tw = timezone.now().astimezone(TAIPEI_TZ).date()
    ts = int(sys_settings.updated_at.timestamp()) if sys_settings.updated_at else 0

    if cert_type == "license":
        lic = get_object_or_404(
            CustomerLicense.objects.select_related("customer", "product"), pk=pk
        )
        if not user_can_access_customer(request.user, lic.customer):
            return JsonResponse({"error": "權限不足：您無權存取此客戶的授權資料"}, status=403)
        if lic.approval_status != CustomerLicense.APPROVAL_APPROVED:
            return JsonResponse(
                {"error": "此軟體授權書尚未覆核通過（或已退回），必須待主管覆核核准後才可預覽與匯出正式證書。"},
                status=403,
            )

        is_perpetual = lic.is_perpetual or lic.license_type == CustomerLicense.TYPE_PERPETUAL
        period_str = "永久授權" if is_perpetual else _format_period(lic.start_date, lic.end_date)
        has_custom_bg = _has_valid_bg(sys_settings.license_cert_bg)
        custom_bg_url = (
            f"{reverse('cert-bg-serve', kwargs={'cert_type': 'license'})}?v={ts}"
            if has_custom_bg
            else ""
        )

        payload = {
            "cert_type": "license",
            "object_id": lic.pk,
            "title_en": "Software License Certificate",
            "title_zh": "軟體授權證書",
            "has_custom_bg": has_custom_bg,
            "custom_bg_url": custom_bg_url,
            "filename_base": _sanitize_filename(
                f"軟體授權證書_{lic.license_no}_{lic.customer.name}"
            ),
            "fields": {
                "product_name": lic.product.name if lic.product else "",
                "quantity": f"{lic.quantity}{lic.unit}",
                "recipient": lic.customer.name if lic.customer else "",
                "period": period_str,
                "cert_no": lic.license_no,
                "issue_date": _format_roc_date(today_tw),
            },
        }
        return JsonResponse(payload)

    # cert_type == "warranty"
    contract = get_object_or_404(
        MaintenanceContract.objects.select_related("customer").prefetch_related(
            "licenses__product"
        ),
        pk=pk,
    )
    if not user_can_access_customer(request.user, contract.customer):
        return JsonResponse({"error": "權限不足：您無權存取此客戶的合約資料"}, status=403)
    if contract.approval_status != MaintenanceContract.APPROVAL_APPROVED:
        return JsonResponse(
            {"error": "此維護保固合約尚未覆核通過（或已退回），必須待主管覆核核准後才可預覽與匯出正式保固證書。"},
            status=403,
        )

    product_text, quantity_text = _build_warranty_defaults(contract)
    period_str = _format_period(contract.start_date, contract.end_date)
    has_custom_bg = _has_valid_bg(sys_settings.warranty_cert_bg)
    custom_bg_url = (
        f"{reverse('cert-bg-serve', kwargs={'cert_type': 'warranty'})}?v={ts}"
        if has_custom_bg
        else ""
    )

    payload = {
        "cert_type": "warranty",
        "object_id": contract.pk,
        "title_en": "Warranty Certificate",
        "title_zh": "軟體保固證書",
        "has_custom_bg": has_custom_bg,
        "custom_bg_url": custom_bg_url,
        "filename_base": _sanitize_filename(
            f"軟體保固證書_{contract.contract_no}_{contract.customer.name}"
        ),
        "fields": {
            "product_name": product_text,
            "quantity": quantity_text,
            "recipient": contract.customer.name if contract.customer else "",
            "period": period_str,
            "cert_no": contract.contract_no,
            "issue_date": "",
        },
    }
    return JsonResponse(payload)


@login_required
@require_POST
def cert_audit_api(request):
    """記錄使用者匯出授權書或保固證書之審計日誌"""
    if not user_can_view_crm(request.user):
        return JsonResponse({"error": "權限不足"}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "無效的 JSON 請求內容"}, status=400)

    cert_type = data.get("cert_type", "")
    object_id = data.get("object_id")
    export_format = str(data.get("export_format", "PNG")).upper()[:20]
    cert_no = str(data.get("cert_no", "")).strip()[:150]
    recipient = str(data.get("recipient", "")).strip()[:150]
    product_name = str(data.get("product_name", "")).strip()[:200]

    if cert_type == "license":
        lic = get_object_or_404(CustomerLicense.objects.select_related("customer"), pk=object_id)
        if not user_can_access_customer(request.user, lic.customer):
            return JsonResponse({"error": "權限不足"}, status=403)
        if lic.approval_status != CustomerLicense.APPROVAL_APPROVED:
            return JsonResponse({"error": "此軟體授權書尚未覆核通過，禁止匯出證書"}, status=403)
        action_label = "匯出軟體授權證書"
        target_type = "CustomerLicense"
        identifier = cert_no or lic.license_no
    elif cert_type == "warranty":
        ct = get_object_or_404(MaintenanceContract.objects.select_related("customer"), pk=object_id)
        if not user_can_access_customer(request.user, ct.customer):
            return JsonResponse({"error": "權限不足"}, status=403)
        if ct.approval_status != MaintenanceContract.APPROVAL_APPROVED:
            return JsonResponse({"error": "此維護保固合約尚未覆核通過，禁止匯出證書"}, status=403)
        action_label = "匯出軟體保固證書"
        target_type = "MaintenanceContract"
        identifier = cert_no or ct.contract_no
    else:
        return JsonResponse({"error": "不支援的證書類型"}, status=400)

    detail_parts = [f"格式: {export_format}"]
    if recipient:
        detail_parts.append(f"對象: {recipient}")
    if product_name:
        detail_parts.append(f"產品: {product_name}")

    log_action(
        user=request.user,
        action=action_label,
        target_type=target_type,
        target_identifier=identifier,
        detail=" | ".join(detail_parts),
        ip_address=_client_ip(request),
    )
    return JsonResponse({"ok": True})


@login_required
@require_GET
def cert_bg_serve(request, cert_type: str):
    """串流輸出已上傳之自訂證書底圖（確保同源 Canvas 不受 CORS/DEBUG 限制）"""
    if cert_type not in ("license", "warranty"):
        raise Http404("不支援的證書類型")

    sys_settings = SystemSetting.get_settings()
    file_field = (
        sys_settings.license_cert_bg
        if cert_type == "license"
        else sys_settings.warranty_cert_bg
    )
    if not _has_valid_bg(file_field):
        raise Http404("尚未設定自訂底圖")

    content_type, _ = mimetypes.guess_type(file_field.name)
    if not content_type:
        content_type = "application/octet-stream"

    response = FileResponse(file_field.open("rb"), content_type=content_type)
    response["Cache-Control"] = "no-cache"
    return response


@login_required
def cert_template_settings_view(request):
    """系統全域設定：證書底圖範本管理（上傳自訂底圖 / 還原內建預設底圖）"""
    if not user_can_manage_system(request.user):
        messages.error(request, "權限不足：僅限系統管理員管理證書底圖範本。")
        return redirect("dashboard")

    sys_settings = SystemSetting.get_settings()

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action in ("upload_license", "upload_warranty"):
            is_lic = action == "upload_license"
            file_key = "license_cert_bg" if is_lic else "warranty_cert_bg"
            label = "軟體授權證書" if is_lic else "軟體保固證書"
            uploaded = request.FILES.get(file_key)

            if not uploaded:
                messages.error(request, f"請先選擇要上傳的{label}底圖檔案。")
                return redirect("cert-template-settings")

            ext = os.path.splitext(uploaded.name)[1].lower()
            if ext not in ALLOWED_BG_EXTS:
                messages.error(
                    request,
                    f"不支援的檔案格式 ({ext})，請上傳 PNG、JPG、WEBP 或 SVG 圖片檔。",
                )
                return redirect("cert-template-settings")

            if uploaded.size > MAX_BG_FILE_SIZE:
                messages.error(request, "底圖檔案大小不可超過 10 MB。")
                return redirect("cert-template-settings")

            old_field = getattr(sys_settings, file_key)
            if old_field and old_field.name:
                try:
                    old_field.delete(save=False)
                except Exception:
                    pass

            setattr(sys_settings, file_key, uploaded)
            sys_settings.save(update_fields=[file_key, "updated_at"])
            log_action(
                user=request.user,
                action=f"上傳{label}自訂底圖",
                target_type="SystemSetting",
                target_identifier=file_key,
                detail=f"檔名: {uploaded.name} ({uploaded.size} bytes)",
                ip_address=_client_ip(request),
            )
            messages.success(request, f"已成功更新「{label}」自訂底圖！")
            return redirect("cert-template-settings")

        if action in ("reset_license", "reset_warranty"):
            is_lic = action == "reset_license"
            file_key = "license_cert_bg" if is_lic else "warranty_cert_bg"
            label = "軟體授權證書" if is_lic else "軟體保固證書"
            old_field = getattr(sys_settings, file_key)
            if old_field and old_field.name:
                try:
                    old_field.delete(save=False)
                except Exception:
                    pass
            setattr(sys_settings, file_key, None)
            sys_settings.save(update_fields=[file_key, "updated_at"])
            log_action(
                user=request.user,
                action=f"還原{label}為內建預設底圖",
                target_type="SystemSetting",
                target_identifier=file_key,
                ip_address=_client_ip(request),
            )
            messages.success(request, f"已將「{label}」還原為系統內建預設底圖。")
            return redirect("cert-template-settings")

    ts = int(sys_settings.updated_at.timestamp()) if sys_settings.updated_at else 0
    has_license_bg = _has_valid_bg(sys_settings.license_cert_bg)
    has_warranty_bg = _has_valid_bg(sys_settings.warranty_cert_bg)

    return render(
        request,
        "core/cert_template_settings.html",
        {
            "system_settings": sys_settings,
            "has_license_bg": has_license_bg,
            "has_warranty_bg": has_warranty_bg,
            "license_bg_url": (
                f"{reverse('cert-bg-serve', kwargs={'cert_type': 'license'})}?v={ts}"
                if has_license_bg
                else ""
            ),
            "warranty_bg_url": (
                f"{reverse('cert-bg-serve', kwargs={'cert_type': 'warranty'})}?v={ts}"
                if has_warranty_bg
                else ""
            ),
        },
    )
