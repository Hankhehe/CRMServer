import datetime
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.core.notification_service import notify_customer_followers, notify_order_workflow_event
from apps.core.permissions import log_action
from apps.customers.models import (
    CustomerActivityLog,
    CustomerFollow,
    CustomerServiceRecord,
    MaintenanceContract,
    generate_service_no,
)
from apps.hardware.models import HardwareDevice, InventoryTransaction
from apps.licenses.models import CustomerLicense
from .models import Order, OrderAttachment, OrderItem, OrderPayment, OrderVersion

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def _generate_license_no() -> str:
    today_str = timezone.now().astimezone(TAIPEI_TZ).strftime("%Y%m%d")
    prefix = f"LIC-{today_str}-"
    existing = CustomerLicense.objects.filter(license_no__startswith=prefix).values_list("license_no", flat=True)
    max_seq = 0
    for code in existing:
        tail = code[len(prefix):]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:04d}"


def _generate_contract_no() -> str:
    today_str = timezone.now().astimezone(TAIPEI_TZ).strftime("%Y%m%d")
    prefix = f"MA-{today_str}-"
    existing = MaintenanceContract.objects.filter(contract_no__startswith=prefix).values_list("contract_no", flat=True)
    max_seq = 0
    for code in existing:
        tail = code[len(prefix):]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:04d}"


def get_warranty_target_labels(
    item: OrderItem,
    order_items_by_sort: Optional[dict[int, OrderItem]] = None,
) -> list[str]:
    """回傳維護保固明細所涵蓋的本單新購與客戶已購標的可讀清單"""
    labels: list[str] = []
    if item.item_type != OrderItem.TYPE_WARRANTY:
        return labels

    if order_items_by_sort is None and item.order_id:
        order_items_by_sort = {
            it.sort_order: it
            for it in OrderItem.objects.filter(order_id=item.order_id).only("sort_order", "item_type", "name")
        }
    order_items_by_sort = order_items_by_sort or {}

    for raw_idx in (item.warranty_target_item_indexes or []):
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            continue
        target_it = order_items_by_sort.get(idx)
        if target_it:
            type_tag = "軟體" if target_it.item_type == OrderItem.TYPE_LICENSE else "硬體"
            labels.append(f"本單新購{type_tag}：{target_it.name}")

    for lic in item.warranty_existing_licenses.all():
        prod_name = lic.product.name if lic.product_id and lic.product else ""
        labels.append(f"已購軟體：[{lic.license_no}] {prod_name}".strip())

    for dev in item.warranty_existing_devices.all():
        model_name = dev.model.name if dev.model_id and dev.model else ""
        labels.append(f"已購硬體：[{dev.code}] {model_name}".strip())

    return labels


def build_order_snapshot_dict(order: Order) -> dict:
    """將訂單當下的表頭、金額與所有明細列序列化為完整 JSON 快照字典"""
    all_items = list(
        order.items.select_related("product", "hardware_model")
        .prefetch_related("warranty_existing_licenses__product", "warranty_existing_devices__model")
        .all()
    )
    items_by_sort = {it.sort_order: it for it in all_items}
    items_payload = []
    for idx, item in enumerate(all_items, start=1):
        items_payload.append({
            "seq": idx,
            "item_type": item.item_type,
            "item_type_display": item.get_item_type_display(),
            "name": item.name,
            "description": item.description,
            "product_id": item.product_id,
            "product_code": item.product.code if item.product else "",
            "product_name": item.product.name if item.product else "",
            "license_type": item.license_type,
            "hardware_model_id": item.hardware_model_id,
            "hardware_model_name": item.hardware_model.name if item.hardware_model else "",
            "start_date": item.start_date.strftime("%Y-%m-%d") if item.start_date else "",
            "end_date": item.end_date.strftime("%Y-%m-%d") if item.end_date else "",
            "warranty_days": item.warranty_days,
            "warranty_targets_display": get_warranty_target_labels(item, items_by_sort)
            if item.item_type == OrderItem.TYPE_WARRANTY
            else [],
            "sla_level": item.sla_level,
            "quantity": int(item.quantity or 0),
            "unit": item.unit,
            "unit_price": str(int(item.unit_price or 0)),
            "line_total": str(int(item.line_total or 0)),
        })

    return {
        "order_no": order.order_no,
        "title": order.title,
        "customer_code": order.customer.code,
        "customer_name": order.customer.name,
        "customer_tax_id": order.customer.tax_id,
        "customer_address": order.customer.address,
        "contact_name": order.contact_name,
        "contact_phone": order.contact_phone,
        "contact_email": order.contact_email,
        "quote_date": order.quote_date.strftime("%Y-%m-%d") if order.quote_date else "",
        "valid_until": order.valid_until.strftime("%Y-%m-%d") if order.valid_until else "",
        "tax_mode": order.tax_mode,
        "tax_mode_display": order.get_tax_mode_display(),
        "subtotal": str(int(order.subtotal or 0)),
        "discount_amount": str(int(order.discount_amount or 0)),
        "tax_amount": str(int(order.tax_amount or 0)),
        "total_amount": str(int(order.total_amount or 0)),
        "payment_terms": order.payment_terms,
        "terms_note": order.terms_note,
        "salesperson_name": (
            order.salesperson.get_full_name() or order.salesperson.username
            if order.salesperson
            else ""
        ),
        "items": items_payload,
    }


def create_order_version_snapshot(
    order: Order,
    actor,
    change_note: str = "",
    increment_version: bool = False,
    ip_address: Optional[str] = None,
) -> OrderVersion:
    """
    建立當前訂單內容之報價版本快照。
    - increment_version=True：將 order.current_version + 1 後建立新版快照（僅於點擊「產生新版報價單」時觸發；
      呼叫端若在修改草稿後跳版，應在覆寫舊值前先確保舊版快照已留存）。
    - increment_version=False：建立或更新當前版次的快照（不跳版號）。
    """
    with transaction.atomic():
        order.recalculate_totals(save=True)
        if increment_version:
            if not order.versions.filter(version_no=order.current_version).exists():
                OrderVersion.objects.create(
                    order=order,
                    version_no=order.current_version,
                    change_note="初始報價版本自動留存",
                    snapshot_data=build_order_snapshot_dict(order),
                    subtotal=order.subtotal,
                    discount_amount=order.discount_amount,
                    tax_amount=order.tax_amount,
                    total_amount=order.total_amount,
                    created_by=actor,
                )
            order.current_version += 1
            order.save(update_fields=["current_version", "updated_at"])

        snapshot_dict = build_order_snapshot_dict(order)
        ver_obj, _ = OrderVersion.objects.update_or_create(
            order=order,
            version_no=order.current_version,
            defaults={
                "change_note": change_note or (f"報價版次 v{order.current_version}" if increment_version else "報價內容存檔快照"),
                "snapshot_data": snapshot_dict,
                "subtotal": order.subtotal,
                "discount_amount": order.discount_amount,
                "tax_amount": order.tax_amount,
                "total_amount": order.total_amount,
                "created_by": actor,
            },
        )

        if increment_version:
            log_action(
                user=actor,
                action=f"產生新版報價單 (v{order.current_version})",
                target_type="Order",
                target_identifier=order.order_no,
                detail=f"總金額: NT$ {int(order.total_amount):,} | 說明: {change_note or '-'}",
                ip_address=ip_address,
            )

        return ver_obj


def submit_order_for_approval(
    order: Order,
    actor,
    no_signed_quote_reason: str = "",
    ip_address: Optional[str] = None,
) -> tuple[bool, str]:
    """
    業務將訂單送交業務主管審核：
    - 彈性佐證檢核：若無「客戶回簽報價單」附件，必須填寫「未附回簽單說明」。
    - 自動確保當前版次有對應的 OrderVersion 快照留存。
    """
    if order.status not in (Order.STATUS_DRAFT, Order.STATUS_REJECTED):
        return False, "只有「草稿 / 議價中」或「審核退回」狀態的訂單可以送交審核。"

    if not order.items.exists():
        return False, "訂單至少需包含一項產品或服務明細才可送出審核。"

    reason = (no_signed_quote_reason or order.no_signed_quote_reason or "").strip()
    has_signed = order.has_signed_quote_attachment
    if not has_signed and not reason:
        return (
            False,
            "請先上傳「客戶回簽報價單」附件作為佐證；若屬急單或客戶以口頭/Email確認，請填寫「未附回簽報價單說明」後再送審。",
        )

    with transaction.atomic():
        order.recalculate_totals(save=True)
        if not order.versions.filter(version_no=order.current_version).exists():
            create_order_version_snapshot(
                order=order,
                actor=actor,
                change_note="送審時自動封存報價版本",
                increment_version=False,
                ip_address=ip_address,
            )

        order.no_signed_quote_reason = reason
        order.status = Order.STATUS_PENDING_APPROVAL
        order.submitted_at = timezone.now()
        order.rejection_reason = ""
        order.save(
            update_fields=[
                "no_signed_quote_reason",
                "status",
                "submitted_at",
                "rejection_reason",
                "updated_at",
            ]
        )

        if order.salesperson:
            CustomerFollow.objects.get_or_create(customer=order.customer, user=order.salesperson)

        proof_note = "已附客戶回簽報價單" if has_signed else f"未附回簽單說明：{reason}"
        log_action(
            user=actor,
            action="送出訂單交由業務主管審核",
            target_type="Order",
            target_identifier=order.order_no,
            detail=f"客戶: {order.customer.name} | 金額: NT$ {int(order.total_amount):,} | {proof_note}",
            ip_address=ip_address,
        )

    notify_order_workflow_event(
        order=order,
        event_type="submitted",
        actor=actor,
        target_roles=["approver"],
        event_title="新訂單待業務主管審核",
        event_detail=f"專案主旨：{order.title}\n報價版次：v{order.current_version}\n佐證狀態：{proof_note}",
        badge_text="待主管審核",
        header_color="#d97706",
    )
    return True, f"訂單「{order.order_no}」已送交業務主管審核，系統已發送通知！"


def approve_order(
    order: Order,
    reviewer,
    ip_address: Optional[str] = None,
) -> tuple[bool, str]:
    """
    業務主管核准訂單：
    1. 自動依訂單中的【軟體授權】明細建立已核准之 CustomerLicense
    2. 自動依訂單中的【維護保固】明細建立已核准之 MaintenanceContract，並自動關聯同單開立之授權書
    3. 將訂單推進至「待出貨 / 待交付 (pending_shipment)」
    4. 紀錄審計日誌、客戶執行軌跡並通知業務與工程師
    """
    if order.status != Order.STATUS_PENDING_APPROVAL:
        return False, "此訂單目前非「待主管審核」狀態，無法執行核准。"

    now_ts = timezone.now()
    today_tw = now_ts.astimezone(TAIPEI_TZ).date()

    created_licenses: list[CustomerLicense] = []
    created_contracts: list[MaintenanceContract] = []
    created_services: list[CustomerServiceRecord] = []

    with transaction.atomic():
        items = list(
            order.items.select_related("product", "hardware_model")
            .prefetch_related("warranty_existing_licenses", "warranty_existing_devices")
            .all()
        )

        # 1. 開立軟體授權書
        sort_to_license: dict[int, CustomerLicense] = {}
        for item in items:
            if item.item_type == OrderItem.TYPE_LICENSE and not item.generated_license_id:
                if not item.product:
                    return False, f"品項「{item.name}」為軟體授權類型但未指定系統軟體產品主檔，請先退回業務補齊。"
                lic_type = item.license_type or CustomerLicense.TYPE_PERPETUAL
                is_perp = lic_type == CustomerLicense.TYPE_PERPETUAL
                lic_no = _generate_license_no()
                lic = CustomerLicense.objects.create(
                    customer=order.customer,
                    product=item.product,
                    license_no=lic_no,
                    license_type=lic_type,
                    quantity=max(int(item.quantity or 1), 1),
                    unit=item.unit or item.product.default_unit or "台",
                    start_date=item.start_date or today_tw,
                    end_date=None if is_perp else item.end_date,
                    is_perpetual=is_perp,
                    note=f"由訂單 {order.order_no} (v{order.current_version}) 核准自動開立。{item.description}".strip(),
                    approval_status=CustomerLicense.APPROVAL_APPROVED,
                    created_by=order.salesperson or order.created_by or reviewer,
                    reviewed_by=reviewer,
                    reviewed_at=now_ts,
                    source_order=order,
                )
                item.generated_license = lic
                item.save(update_fields=["generated_license"])
                created_licenses.append(lic)
                sort_to_license[item.sort_order] = lic
            elif item.item_type == OrderItem.TYPE_LICENSE and item.generated_license_id:
                sort_to_license[item.sort_order] = item.generated_license

        # 2. 開立維護保固合約（依審核通過當日 + 保固天數起算到期日，並綁定所選本單新購與客戶已購標的）
        for item in items:
            if item.item_type == OrderItem.TYPE_WARRANTY and not item.generated_contract_id:
                contract_no = _generate_contract_no()
                if item.warranty_days and int(item.warranty_days) > 0:
                    ct_start_date = today_tw
                    ct_end_date = today_tw + datetime.timedelta(days=int(item.warranty_days))
                else:
                    ct_start_date = item.start_date or today_tw
                    ct_end_date = item.end_date

                ct = MaintenanceContract.objects.create(
                    customer=order.customer,
                    contract_no=contract_no,
                    name=item.name or f"{order.title} 維護保固合約",
                    start_date=ct_start_date,
                    end_date=ct_end_date,
                    amount=item.line_total,
                    sla_level=item.sla_level or "",
                    note=f"由訂單 {order.order_no} (v{order.current_version}) 核准自動開立。{item.description}".strip(),
                    approval_status=MaintenanceContract.APPROVAL_APPROVED,
                    created_by=order.salesperson or order.created_by or reviewer,
                    reviewed_by=reviewer,
                    reviewed_at=now_ts,
                    source_order=order,
                )
                target_indexes = [
                    int(x) for x in (item.warranty_target_item_indexes or []) if str(x).isdigit()
                ]
                existing_lics = list(item.warranty_existing_licenses.all())
                existing_devs = list(item.warranty_existing_devices.all())
                has_explicit_targets = bool(target_indexes or existing_lics or existing_devs)

                linked_lics: list[CustomerLicense] = list(existing_lics)
                if target_indexes:
                    for idx in target_indexes:
                        matched_lic = sort_to_license.get(idx)
                        if matched_lic and matched_lic not in linked_lics:
                            linked_lics.append(matched_lic)
                elif not has_explicit_targets and created_licenses:
                    linked_lics.extend(created_licenses)

                if linked_lics:
                    ct.licenses.add(*linked_lics)
                if existing_devs:
                    ct.probes.add(*existing_devs)

                item.generated_contract = ct
                item.start_date = ct_start_date
                item.end_date = ct_end_date
                item.save(update_fields=["generated_contract", "start_date", "end_date"])
                created_contracts.append(ct)

        # 3. 開立專業服務 / 其他紀錄
        for item in items:
            if item.item_type == OrderItem.TYPE_SERVICE and not item.generated_service_id:
                svc_no = generate_service_no()
                svc = CustomerServiceRecord.objects.create(
                    customer=order.customer,
                    service_no=svc_no,
                    name=item.name,
                    description=item.description or "",
                    service_date=item.start_date,
                    status=CustomerServiceRecord.STATUS_PENDING,
                    quantity=max(int(item.quantity or 1), 1),
                    unit=item.unit or "式",
                    amount=item.line_total,
                    assigned_to=order.salesperson or order.created_by,
                    note=f"由訂單 {order.order_no} (v{order.current_version}) 核准自動開立。",
                    source_order=order,
                    created_by=order.salesperson or order.created_by or reviewer,
                )
                item.generated_service = svc
                item.save(update_fields=["generated_service"])
                created_services.append(svc)

        order.status = Order.STATUS_PENDING_SHIPMENT
        order.approved_by = reviewer
        order.approved_at = now_ts
        order.rejection_reason = ""
        order.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "rejection_reason",
                "updated_at",
            ]
        )

        lic_summary = ", ".join(l.license_no for l in created_licenses) or "無"
        ct_summary = ", ".join(c.contract_no for c in created_contracts) or "無"
        svc_summary = ", ".join(s.service_no for s in created_services) or "無"
        detail_msg = (
            f"核准訂單：{order.order_no} ({order.title})\n"
            f"訂單總額：NT$ {int(order.total_amount):,}\n"
            f"自動開立軟體授權書：{lic_summary}\n"
            f"自動開立維護保固合約：{ct_summary}\n"
            f"自動開立專業服務紀錄：{svc_summary}\n"
            f"目前狀態已推進為「待出貨 / 待交付」，請工程師檢查並完成出貨。"
        )

        CustomerActivityLog.objects.create(
            customer=order.customer,
            activity_type=CustomerActivityLog.TYPE_QUOTATION,
            content=f"【訂單主管審核核准】{order.order_no}\n{detail_msg}",
            created_by=reviewer,
        )

        log_action(
            user=reviewer,
            action="核准訂單並自動開立授權/保固",
            target_type="Order",
            target_identifier=order.order_no,
            detail=detail_msg.replace("\n", " | "),
            ip_address=ip_address,
        )

    notify_order_workflow_event(
        order=order,
        event_type="approved",
        actor=reviewer,
        target_roles=["salesperson", "engineer"],
        event_title="訂單已核准開單，請工程師安排出貨與交付",
        event_detail=detail_msg,
        badge_text="待出貨 / 待交付",
        header_color="#7c3aed",
    )
    notify_customer_followers(
        customer=order.customer,
        actor=reviewer,
        event_category="data_change",
        event_title=f"訂單核准成立：{order.order_no}",
        event_detail=detail_msg,
    )
    return True, f"已核准訂單「{order.order_no}」！開立授權 {len(created_licenses)} 筆、保固合約 {len(created_contracts)} 筆，訂單已進入「待出貨 / 待交付」。"


def reject_order(
    order: Order,
    reviewer,
    rejection_reason: str,
    ip_address: Optional[str] = None,
) -> tuple[bool, str]:
    """業務主管退回訂單至業務修正"""
    if order.status != Order.STATUS_PENDING_APPROVAL:
        return False, "只有「待主管審核」狀態的訂單可以執行退回。"

    reason = (rejection_reason or "").strip()
    if not reason:
        return False, "退回訂單時必須填寫「退回修正原因」。"

    with transaction.atomic():
        order.status = Order.STATUS_REJECTED
        order.approved_by = reviewer
        order.approved_at = timezone.now()
        order.rejection_reason = reason
        order.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "rejection_reason",
                "updated_at",
            ]
        )
        log_action(
            user=reviewer,
            action="退回訂單修正",
            target_type="Order",
            target_identifier=order.order_no,
            detail=f"退回原因: {reason}",
            ip_address=ip_address,
        )

    notify_order_workflow_event(
        order=order,
        event_type="rejected",
        actor=reviewer,
        target_roles=["salesperson"],
        event_title="訂單已被主管退回，請修正後重新送審",
        event_detail=f"退回原因：{reason}",
        badge_text="審核退回",
        header_color="#dc2626",
    )
    return True, f"已退回訂單「{order.order_no}」，業務可進行調整或補件後重新送審。"


def confirm_order_shipment(
    order: Order,
    engineer,
    device_allocations: dict[int, list[int]],  # {order_item_id: [device_id, ...]}
    license_keys: Optional[dict[int, str]] = None,  # {customer_license_id: key_str}
    shipment_note: str = "",
    ip_address: Optional[str] = None,
) -> tuple[bool, str]:
    """
    工程師檢查並確認出貨 / 交付完畢：
    1. 檢核訂單內每一列硬體設備明細皆已挑滿正確數量的在庫設備序號 (available, non-deleted, 同型號)
    2. 更新選中硬體設備為 shipped、綁定客戶與 source_order，寫入 OUT 交易紀錄
    3. 若同張訂單有維護保固合約，自動將出貨硬體加入合約涵蓋設備
    4. 若工程師一併填寫軟體授權金鑰，寫入對應 CustomerLicense
    5. 訂單狀態推進為「待收款 (pending_payment)」
    """
    if order.status != Order.STATUS_PENDING_SHIPMENT:
        return False, "此訂單目前非「待出貨 / 待交付」狀態。"

    now_ts = timezone.now()

    with transaction.atomic():
        hw_items = list(
            order.items.filter(item_type=OrderItem.TYPE_HARDWARE).select_related("hardware_model")
        )
        all_selected_device_ids: list[int] = []
        item_devices_map: dict[int, list[HardwareDevice]] = {}

        for item in hw_items:
            raw_ids = device_allocations.get(item.pk, [])
            unique_ids = []
            for did in raw_ids:
                if did not in unique_ids:
                    unique_ids.append(did)

            if len(unique_ids) != int(item.quantity or 0):
                model_label = item.hardware_model.name if item.hardware_model else item.name
                return (
                    False,
                    f"硬體明細「{model_label}」需求數量為 {item.quantity} 台，目前僅選擇 {len(unique_ids)} 台，請選齊後再完成出貨。",
                )

            for did in unique_ids:
                if did in all_selected_device_ids:
                    return False, "同一台硬體設備序號不可重複配發於多個明細項目。"
                all_selected_device_ids.append(did)

            devices = list(
                HardwareDevice.objects.select_for_update()
                .filter(pk__in=unique_ids, is_deleted=False)
                .select_related("model")
            )
            if len(devices) != len(unique_ids):
                return False, "部分選取的硬體設備不存在或已被刪除，請重新確認。"

            for dev in devices:
                if dev.status != HardwareDevice.STATUS_AVAILABLE:
                    return (
                        False,
                        f"設備「{dev.code}」目前狀態為「{dev.get_status_display()}」，非在庫可用狀態，不可出貨。",
                    )
                if item.hardware_model_id and dev.model_id != item.hardware_model_id:
                    return (
                        False,
                        f"設備「{dev.code}」型號為「{dev.model.name}」，與訂單指定型號「{item.hardware_model.name}」不符。",
                    )
            item_devices_map[item.pk] = devices

        # 執行正式出貨與交易寫入
        shipped_codes: list[str] = []
        all_shipped_devices: list[HardwareDevice] = []
        hw_sort_to_devices: dict[int, list[HardwareDevice]] = {}
        for item in hw_items:
            devices = item_devices_map.get(item.pk, [])
            for dev in devices:
                dev.status = HardwareDevice.STATUS_SHIPPED
                dev.customer = order.customer
                dev.expected_return_at = None
                dev.source_order = order
                dev._skip_auto_txn = True
                dev.save()

                InventoryTransaction.objects.create(
                    device=dev,
                    customer=order.customer,
                    type=InventoryTransaction.TYPE_OUT,
                    quantity=1,
                    occurred_at=now_ts,
                    note=f"訂單 {order.order_no} 工程師確認出貨 ({shipment_note})" if shipment_note else f"訂單 {order.order_no} 正式出貨",
                    created_by=engineer,
                    source_order=order,
                )
                shipped_codes.append(dev.code)
                all_shipped_devices.append(dev)
            item.allocated_devices.set(devices)
            hw_sort_to_devices[item.sort_order] = devices

        # 若有維護合約則依保固明細指定的本單硬體項目掛載出貨設備（舊資料無明確標的時相容掛載全部）
        if all_shipped_devices:
            warranty_items = list(
                order.items.filter(item_type=OrderItem.TYPE_WARRANTY)
                .select_related("generated_contract")
                .prefetch_related("warranty_existing_licenses", "warranty_existing_devices")
            )
            for w_item in warranty_items:
                ct = w_item.generated_contract
                if not ct:
                    continue
                target_indexes = [
                    int(x) for x in (w_item.warranty_target_item_indexes or []) if str(x).isdigit()
                ]
                has_explicit_targets = bool(
                    target_indexes
                    or w_item.warranty_existing_licenses.exists()
                    or w_item.warranty_existing_devices.exists()
                )
                if target_indexes:
                    matched_devs: list[HardwareDevice] = []
                    for idx in target_indexes:
                        matched_devs.extend(hw_sort_to_devices.get(idx, []))
                    if matched_devs:
                        ct.probes.add(*matched_devs)
                elif not has_explicit_targets:
                    ct.probes.add(*all_shipped_devices)

        # 更新工程師順手填入之授權金鑰
        if license_keys:
            for lic in CustomerLicense.objects.filter(source_order=order):
                if lic.pk in license_keys:
                    new_key = (license_keys[lic.pk] or "").strip()
                    if new_key and new_key != lic.license_key:
                        lic.license_key = new_key
                        lic.save(update_fields=["license_key", "updated_at"])

        # 將本訂單開立且尚未取消之專業服務紀錄自動標記為已完成
        today_tw = now_ts.astimezone(TAIPEI_TZ).date()
        for svc in CustomerServiceRecord.objects.filter(source_order=order).exclude(
            status=CustomerServiceRecord.STATUS_CANCELLED
        ):
            if svc.status != CustomerServiceRecord.STATUS_COMPLETED:
                svc.status = CustomerServiceRecord.STATUS_COMPLETED
                if not svc.completed_at:
                    svc.completed_at = today_tw
                svc.save(update_fields=["status", "completed_at", "updated_at"])

        order.status = Order.STATUS_PENDING_PAYMENT
        order.shipped_by = engineer
        order.shipped_at = now_ts
        order.shipment_note = (shipment_note or "").strip()
        order.save(
            update_fields=[
                "status",
                "shipped_by",
                "shipped_at",
                "shipment_note",
                "updated_at",
            ]
        )

        hw_summary = ", ".join(shipped_codes) if shipped_codes else "純軟體 / 服務交付（無硬體出貨）"
        detail_msg = (
            f"訂單編號：{order.order_no}\n"
            f"交付設備：{hw_summary}\n"
            f"交付備註：{order.shipment_note or '-'}\n"
            f"訂單狀態已轉為「待收款」，請業務向客戶請款並於系統回報收款！"
        )

        CustomerActivityLog.objects.create(
            customer=order.customer,
            activity_type=CustomerActivityLog.TYPE_SUPPORT,
            content=f"【訂單完成出貨與交付】{order.order_no}\n{detail_msg}",
            created_by=engineer,
        )
        log_action(
            user=engineer,
            action="確認訂單出貨交付並轉待收款",
            target_type="Order",
            target_identifier=order.order_no,
            detail=detail_msg.replace("\n", " | "),
            ip_address=ip_address,
        )

    notify_order_workflow_event(
        order=order,
        event_type="shipped",
        actor=engineer,
        target_roles=["salesperson", "finance"],
        event_title="訂單已出貨交付完畢，狀態轉為「待收款」",
        event_detail=detail_msg,
        badge_text="待收款",
        header_color="#1d4ed8",
    )
    return True, f"訂單「{order.order_no}」已完成出貨交付檢查，狀態已推進為「待收款」！"


def report_order_payment(
    order: Order,
    reporter,
    amount: Decimal,
    payment_date,
    payment_method: str,
    reference_no: str = "",
    proof_file=None,
    note: str = "",
    ip_address: Optional[str] = None,
) -> tuple[bool, str]:
    """業務於「待收款」階段新增一筆收款回報，待管理部核對入帳"""
    if order.status != Order.STATUS_PENDING_PAYMENT:
        return False, "只有「待收款」狀態的訂單可以回報收款。"

    if not amount or Decimal(amount) <= Decimal("0"):
        return False, "收款回報金額必須大於 0。"

    with transaction.atomic():
        pm = OrderPayment.objects.create(
            order=order,
            amount=Decimal(amount),
            payment_date=payment_date or timezone.now().astimezone(TAIPEI_TZ).date(),
            payment_method=payment_method or OrderPayment.METHOD_BANK_TRANSFER,
            reference_no=(reference_no or "").strip(),
            proof_file=proof_file,
            note=(note or "").strip(),
            status=OrderPayment.STATUS_PENDING,
            reported_by=reporter,
        )

        detail_msg = (
            f"回報金額：NT$ {int(pm.amount):,}\n"
            f"收款日期：{pm.payment_date}\n"
            f"付款方式：{pm.get_payment_method_display()}\n"
            f"參考編號：{pm.reference_no or '-'}\n"
            f"回報備註：{pm.note or '-'}"
        )
        log_action(
            user=reporter,
            action="回報訂單收款待入帳確認",
            target_type="Order",
            target_identifier=order.order_no,
            detail=detail_msg.replace("\n", " | "),
            ip_address=ip_address,
        )

    notify_order_workflow_event(
        order=order,
        event_type="payment_reported",
        actor=reporter,
        target_roles=["finance"],
        event_title="業務已回報訂單收款，請管理部確認入帳",
        event_detail=detail_msg,
        badge_text="待入帳確認",
        header_color="#0284c7",
    )
    return True, f"已回報收款 NT$ {int(pm.amount):,}，已通知管理部進行入帳確認。"


def confirm_order_payment(
    payment: OrderPayment,
    reviewer,
    review_note: str = "",
    auto_close_if_paid_in_full: bool = True,
    ip_address: Optional[str] = None,
) -> tuple[bool, str]:
    """管理部確認該筆回報款項已實際入帳；若累計入帳金額達訂單總額且勾選自動結案，則將訂單結案"""
    order = payment.order
    if payment.status != OrderPayment.STATUS_PENDING:
        return False, "此筆收款紀錄已處理過，無需重複確認。"
    if order.status != Order.STATUS_PENDING_PAYMENT:
        return False, "訂單目前非「待收款」狀態。"

    now_ts = timezone.now()
    closed_now = False

    with transaction.atomic():
        payment.status = OrderPayment.STATUS_CONFIRMED
        payment.confirmed_by = reviewer
        payment.confirmed_at = now_ts
        payment.review_note = (review_note or "").strip()
        payment.save(
            update_fields=[
                "status",
                "confirmed_by",
                "confirmed_at",
                "review_note",
            ]
        )

        # 重新統計累計已確認入帳金額
        confirmed_total = sum(
            (p.amount or Decimal("0"))
            for p in order.payments.filter(status=OrderPayment.STATUS_CONFIRMED)
        )
        log_action(
            user=reviewer,
            action="管理部確認訂單款項入帳",
            target_type="Order",
            target_identifier=order.order_no,
            detail=f"確認入帳: NT$ {int(payment.amount):,} | 累計入帳: NT$ {int(confirmed_total):,} / 應收: NT$ {int(order.total_amount):,}",
            ip_address=ip_address,
        )

        if auto_close_if_paid_in_full and confirmed_total >= (order.total_amount or Decimal("0")):
            order.status = Order.STATUS_CLOSED
            order.closed_by = reviewer
            order.closed_at = now_ts
            order.closing_note = (review_note or "款項全額確認入帳自動結案").strip()
            order.save(
                update_fields=[
                    "status",
                    "closed_by",
                    "closed_at",
                    "closing_note",
                    "updated_at",
                ]
            )
            closed_now = True
            CustomerActivityLog.objects.create(
                customer=order.customer,
                activity_type=CustomerActivityLog.TYPE_QUOTATION,
                content=f"【訂單收款入帳結案】{order.order_no}\n管理部已確認累計入帳 NT$ {int(confirmed_total):,}，訂單正式結案。",
                created_by=reviewer,
            )
            log_action(
                user=reviewer,
                action="訂單確認入帳並正式結案",
                target_type="Order",
                target_identifier=order.order_no,
                detail=f"累計入帳: NT$ {int(confirmed_total):,}",
                ip_address=ip_address,
            )

    if closed_now:
        notify_order_workflow_event(
            order=order,
            event_type="closed",
            actor=reviewer,
            target_roles=["salesperson"],
            event_title="管理部已確認款項全額入帳，訂單正式結案",
            event_detail=f"本次確認金額：NT$ {int(payment.amount):,}\n累計入帳總額：NT$ {int(confirmed_total):,}\n結案備註：{order.closing_note}",
            badge_text="已結案",
            header_color="#059669",
        )
        return True, f"已確認入帳 NT$ {int(payment.amount):,}，款項已收齊，訂單「{order.order_no}」已正式結案！"

    return True, f"已確認入帳 NT$ {int(payment.amount):,}（目前尚欠餘額 NT$ {int(order.unpaid_balance):,}）。"


def reject_order_payment(
    payment: OrderPayment,
    reviewer,
    review_note: str,
    ip_address: Optional[str] = None,
) -> tuple[bool, str]:
    """管理部未對到帳時，退回該筆收款回報"""
    if payment.status != OrderPayment.STATUS_PENDING:
        return False, "只有「待確認入帳」的收款紀錄可以退回。"
    reason = (review_note or "").strip()
    if not reason:
        return False, "退回帳務回報時請填寫退回原因（例如：未查獲該筆匯款、金額不符）。"

    with transaction.atomic():
        payment.status = OrderPayment.STATUS_REJECTED
        payment.confirmed_by = reviewer
        payment.confirmed_at = timezone.now()
        payment.review_note = reason
        payment.save(
            update_fields=[
                "status",
                "confirmed_by",
                "confirmed_at",
                "review_note",
            ]
        )
        log_action(
            user=reviewer,
            action="管理部退回訂單收款回報",
            target_type="Order",
            target_identifier=payment.order.order_no,
            detail=f"回報金額: NT$ {int(payment.amount):,} | 原因: {reason}",
            ip_address=ip_address,
        )

    notify_order_workflow_event(
        order=payment.order,
        event_type="payment_rejected",
        actor=reviewer,
        target_roles=["salesperson"],
        event_title="管理部退回收款回報，請重新確認帳務資料",
        event_detail=f"退回金額：NT$ {int(payment.amount):,}\n退回原因：{reason}",
        badge_text="對帳退回",
        header_color="#dc2626",
    )
    return True, "已退回該筆收款回報，並通知業務重新確認。"


def close_order_manually(
    order: Order,
    reviewer,
    closing_note: str = "",
    ip_address: Optional[str] = None,
) -> tuple[bool, str]:
    """管理部手動結案訂單（例如匯費內扣少 30 元或確認尾款已清）"""
    if order.status != Order.STATUS_PENDING_PAYMENT:
        return False, "只有「待收款」狀態的訂單可以執行結案。"

    if order.has_pending_payment_review:
        return False, "尚有「待管理部確認入帳」的收款回報，請先審理該筆收款回報後再執行結案。"

    note = (closing_note or "").strip()
    if order.unpaid_balance > Decimal("0") and not note:
        return False, "目前累計入帳金額小於訂單總額，執行手動結案時請務必填寫「結案說明備註」（例如：客戶扣匯費 30 元結清）。"

    with transaction.atomic():
        order.status = Order.STATUS_CLOSED
        order.closed_by = reviewer
        order.closed_at = timezone.now()
        order.closing_note = note or "管理部確認款項結清並結案"
        order.save(
            update_fields=[
                "status",
                "closed_by",
                "closed_at",
                "closing_note",
                "updated_at",
            ]
        )
        CustomerActivityLog.objects.create(
            customer=order.customer,
            activity_type=CustomerActivityLog.TYPE_QUOTATION,
            content=f"【訂單手動結案】{order.order_no}\n管理部已確認結案。備註：{order.closing_note}",
            created_by=reviewer,
        )
        log_action(
            user=reviewer,
            action="管理部手動結案訂單",
            target_type="Order",
            target_identifier=order.order_no,
            detail=f"累計入帳: NT$ {int(order.confirmed_paid_amount):,} / 總額: NT$ {int(order.total_amount):,} | 備註: {order.closing_note}",
            ip_address=ip_address,
        )

    notify_order_workflow_event(
        order=order,
        event_type="closed",
        actor=reviewer,
        target_roles=["salesperson"],
        event_title="管理部已將訂單標記為正式結案",
        event_detail=f"累計入帳：NT$ {int(order.confirmed_paid_amount):,}\n結案備註：{order.closing_note}",
        badge_text="已結案",
        header_color="#059669",
    )
    return True, f"訂單「{order.order_no}」已正式結案！"


def cancel_order(
    order: Order,
    actor,
    cancel_reason: str,
    ip_address: Optional[str] = None,
) -> tuple[bool, str]:
    """
    作廢訂單並安全回收關聯單據與設備庫存：
    - 停用關聯 CustomerLicense
    - 終止關聯 MaintenanceContract
    - 將出貨綁定的 HardwareDevice 自動辦理退貨回庫存
    """
    if order.status in (Order.STATUS_CLOSED, Order.STATUS_CANCELLED):
        return False, "已結案或已作廢的訂單不可重複作廢。"

    reason = (cancel_reason or "").strip()
    if not reason:
        return False, "作廢訂單時必須填寫「作廢原因」。"

    now_ts = timezone.now()
    recycled_licenses = 0
    recycled_contracts = 0
    recycled_services = 0
    returned_devices = []

    with transaction.atomic():
        for lic in CustomerLicense.objects.filter(source_order=order):
            if not lic.is_disabled:
                lic.is_disabled = True
                lic.note = f"{lic.note}\n[來源訂單 {order.order_no} 已作廢自動停用：{reason}]".strip()
                lic.save()
                recycled_licenses += 1

        for ct in MaintenanceContract.objects.filter(source_order=order):
            if not ct.is_terminated:
                ct.is_terminated = True
                ct.note = f"{ct.note}\n[來源訂單 {order.order_no} 已作廢自動終止：{reason}]".strip()
                ct.save()
                recycled_contracts += 1

        for svc in CustomerServiceRecord.objects.filter(source_order=order):
            if svc.status != CustomerServiceRecord.STATUS_CANCELLED:
                svc.status = CustomerServiceRecord.STATUS_CANCELLED
                svc.note = f"{svc.note}\n[來源訂單 {order.order_no} 已作廢自動取消：{reason}]".strip()
                svc.save(update_fields=["status", "note", "updated_at"])
                recycled_services += 1

        for dev in HardwareDevice.objects.select_for_update().filter(source_order=order, is_deleted=False):
            if dev.status == HardwareDevice.STATUS_SHIPPED and dev.customer_id == order.customer_id:
                InventoryTransaction.objects.create(
                    device=dev,
                    customer=order.customer,
                    type=InventoryTransaction.TYPE_RETURN_OUT,
                    quantity=1,
                    occurred_at=now_ts,
                    note=f"訂單 {order.order_no} 作廢自動退貨回庫存：{reason}",
                    created_by=actor,
                    source_order=order,
                )
                for ct in MaintenanceContract.objects.filter(source_order=order):
                    ct.probes.remove(dev)
                dev.status = HardwareDevice.STATUS_AVAILABLE
                dev.customer = None
                dev.expected_return_at = None
                dev.source_order = None
                dev._skip_auto_txn = True
                dev.save()
                returned_devices.append(dev.code)

        order.status = Order.STATUS_CANCELLED
        order.cancelled_by = actor
        order.cancelled_at = now_ts
        order.cancel_reason = reason
        order.save(
            update_fields=[
                "status",
                "cancelled_by",
                "cancelled_at",
                "cancel_reason",
                "updated_at",
            ]
        )

        recycle_msg = (
            f"作廢原因：{reason}\n"
            f"回收授權書：{recycled_licenses} 筆 | 終止保固合約：{recycled_contracts} 筆 | 退回庫存設備：{', '.join(returned_devices) if returned_devices else '無'}"
        )
        CustomerActivityLog.objects.create(
            customer=order.customer,
            activity_type=CustomerActivityLog.TYPE_QUOTATION,
            content=f"【訂單作廢與資源回收】{order.order_no}\n{recycle_msg}",
            created_by=actor,
        )
        log_action(
            user=actor,
            action="作廢訂單並回收關聯單據/設備",
            target_type="Order",
            target_identifier=order.order_no,
            detail=recycle_msg.replace("\n", " | "),
            ip_address=ip_address,
        )

    notify_order_workflow_event(
        order=order,
        event_type="cancelled",
        actor=actor,
        target_roles=["salesperson", "approver"],
        event_title="訂單已執行作廢",
        event_detail=recycle_msg,
        badge_text="已作廢",
        header_color="#475569",
    )
    return True, f"訂單「{order.order_no}」已作廢！已自動回收授權 {recycled_licenses} 筆、合約 {recycled_contracts} 筆、退庫設備 {len(returned_devices)} 台。"
