"""
硬體設備出庫簽核覆核服務模組 (Hardware Outbound Approval Service)
涵蓋範圍：
1. 正式出貨 (ship -> pending_ship)
2. 客戶借測 (loan -> pending_loan)
3. 維護合約故障換機新機出庫 (rma_replace -> pending_ship)
"""

from typing import Optional, Tuple
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.permissions import log_action
from apps.core.notification_service import (
    notify_customer_followers,
    notify_approval_submitted,
    notify_approval_decision,
)
from .models import HardwareDevice, InventoryTransaction, HardwareOutboundApproval


def create_hardware_outbound_approval(
    device: HardwareDevice,
    customer,
    request_type: str,  # "ship" | "loan" | "rma_replace"
    applicant,
    expected_return_at=None,
    note: str = "",
    contract=None,
    old_probe: Optional[HardwareDevice] = None,
    ip_address: Optional[str] = None,
    send_email: bool = True,
) -> HardwareOutboundApproval:
    """
    將目標設備預先鎖定為「出貨待審 (pending_ship)」或「借測待審 (pending_loan)」，
    建立待覆核之 HardwareOutboundApproval，並發送簽核通知給覆核主管。
    """
    with transaction.atomic():
        # 若該設備先前仍有殘留未結案之 pending 簽核單，先標記覆蓋為退回以免重複
        HardwareOutboundApproval.objects.filter(
            device=device, approval_status=HardwareOutboundApproval.APPROVAL_PENDING
        ).update(
            approval_status=HardwareOutboundApproval.APPROVAL_REJECTED,
            rejection_reason="系統因重新發起申請自動取代",
            reviewed_at=timezone.now(),
        )

        target_pending_status = (
            HardwareDevice.STATUS_PENDING_LOAN
            if request_type == HardwareOutboundApproval.REQUEST_LOAN
            else HardwareDevice.STATUS_PENDING_SHIP
        )

        # 若設備原先是 shipped / loaned 換客戶重出，先讓 save() 自動補退回舊客戶紀錄，再鎖為待審
        device.status = target_pending_status
        device.customer = customer
        device.expected_return_at = (
            expected_return_at if request_type == HardwareOutboundApproval.REQUEST_LOAN else None
        )
        device._created_by = applicant
        device._transaction_note = note
        device.save()

        req_obj = HardwareOutboundApproval.objects.create(
            device=device,
            customer=customer,
            request_type=request_type,
            expected_return_at=expected_return_at if request_type == HardwareOutboundApproval.REQUEST_LOAN else None,
            note=note or "",
            contract=contract,
            old_probe=old_probe,
            approval_status=HardwareOutboundApproval.APPROVAL_PENDING,
            requested_by=applicant,
        )

        req_label = req_obj.get_request_type_display()
        cust_name = customer.name if customer else "未知客戶"
        detail_lines = [
            f"申請類別：{req_label}",
            f"設備序號：{device.code} ({device.model.name if device.model else ''})",
            f"目標客戶：{cust_name}",
        ]
        if request_type == HardwareOutboundApproval.REQUEST_LOAN and expected_return_at:
            detail_lines.append(f"預計歸還日：{expected_return_at}")
        if request_type == HardwareOutboundApproval.REQUEST_RMA and contract:
            detail_lines.append(f"維護合約：{contract.contract_no} (換下舊機: {old_probe.code if old_probe else '-'})")
        if note:
            detail_lines.append(f"說明備註：{note}")
        detail_text = "\n".join(detail_lines)

        log_action(
            user=applicant,
            action=f"送出硬體{req_label}簽核申請",
            target_type="HardwareOutboundApproval",
            target_identifier=f"{device.code} -> {cust_name}",
            detail=detail_text.replace("\n", " | "),
            ip_address=ip_address,
        )

    if send_email:
        notify_approval_submitted(
            module_type="hardware",
            customer=customer,
            actor=applicant,
            target_identifier=f"{device.code} ({req_label})",
            event_detail=detail_text,
            review_path=reverse("hardware-detail", kwargs={"pk": device.pk}),
        )

    return req_obj


def approve_hardware_outbound_request(
    req_obj: HardwareOutboundApproval,
    reviewer,
    ip_address: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    覆核通過硬體出庫申請：
    - 一般出貨 / 客戶借測：設備變更為正式 shipped / loaned，寫入正式 OUT / LOAN 交易紀錄。
    - 維護合約故障換機 (RMA)：一併完成舊機退回入庫、新機正式出貨、合約設備關聯替換、更換紀錄與客戶執行軌跡。
    """
    if req_obj.approval_status != HardwareOutboundApproval.APPROVAL_PENDING:
        return False, "此硬體出庫簽核單已處理完畢，無需重複審核。"

    if (
        req_obj.requested_by_id
        and req_obj.requested_by_id == reviewer.id
        and not getattr(reviewer, "is_superuser", False)
    ):
        return False, "簽核規範限制：不可覆核自己送審的出庫申請，請由其他具備覆核權限的人員審核。"

    device = req_obj.device
    customer = req_obj.customer
    req_label = req_obj.get_request_type_display()

    with transaction.atomic():
        if req_obj.request_type == HardwareOutboundApproval.REQUEST_RMA:
            from apps.customers.models import ContractProbeReplacement, CustomerActivityLog

            contract = req_obj.contract
            old_probe = req_obj.old_probe
            reason = req_obj.note or "維護合約故障換機"

            if old_probe and old_probe.status in (HardwareDevice.STATUS_SHIPPED, HardwareDevice.STATUS_LOANED):
                InventoryTransaction.objects.create(
                    device=old_probe,
                    customer=customer,
                    type=InventoryTransaction.TYPE_RETURN_OUT,
                    quantity=1,
                    note=f"維護合約 ({contract.contract_no if contract else '-'}) 設備退換: {reason}",
                    created_by=reviewer,
                )
                old_probe.customer = None
                old_probe.status = HardwareDevice.STATUS_AVAILABLE
                old_probe.expected_return_at = None
                old_probe._skip_auto_txn = True
                old_probe.save()
                if contract:
                    contract.probes.remove(old_probe)

            device.customer = customer
            device.status = HardwareDevice.STATUS_SHIPPED
            device.expected_return_at = None
            device._skip_auto_txn = True
            device.save()

            old_code = old_probe.code if old_probe else "-"
            InventoryTransaction.objects.create(
                device=device,
                customer=customer,
                type=InventoryTransaction.TYPE_OUT,
                quantity=1,
                note=f"維護合約 ({contract.contract_no if contract else '-'}) 換機核准上線 (換下序號: {old_code}): {reason}",
                created_by=reviewer,
            )
            if contract:
                contract.probes.add(device)
                ContractProbeReplacement.objects.create(
                    contract=contract,
                    action_type=ContractProbeReplacement.ACTION_REPLACE,
                    old_probe=old_probe,
                    old_probe_code=old_code,
                    new_probe=device,
                    new_probe_code=device.code,
                    reason=reason,
                    transferred_license=None,
                    performed_by=reviewer,
                )
            if customer:
                detail_log = (
                    f"【維護合約故障換機 (覆核通過)】合約編號: {contract.contract_no if contract else '-'}\n"
                    f"換下舊機: {old_code}\n換上新機: {device.code}\n原因說明: {reason}"
                )
                CustomerActivityLog.objects.create(
                    customer=customer,
                    activity_type=CustomerActivityLog.TYPE_SUPPORT,
                    content=detail_log,
                    created_by=reviewer,
                )
        else:
            is_loan = req_obj.request_type == HardwareOutboundApproval.REQUEST_LOAN
            device.customer = customer
            device.status = HardwareDevice.STATUS_LOANED if is_loan else HardwareDevice.STATUS_SHIPPED
            device.expected_return_at = req_obj.expected_return_at if is_loan else None
            device._skip_auto_txn = True
            device.save()

            txn_type = InventoryTransaction.TYPE_LOAN if is_loan else InventoryTransaction.TYPE_OUT
            default_note = (
                f"覆核通過正式{'借測' if is_loan else '出貨'}給客戶 {customer.name if customer else '未知'}"
            )
            InventoryTransaction.objects.create(
                device=device,
                customer=customer,
                type=txn_type,
                quantity=1,
                note=req_obj.note or default_note,
                created_by=reviewer,
                expected_return_at=req_obj.expected_return_at if is_loan else None,
            )

        req_obj.approval_status = HardwareOutboundApproval.APPROVAL_APPROVED
        req_obj.reviewed_by = reviewer
        req_obj.reviewed_at = timezone.now()
        req_obj.rejection_reason = ""
        req_obj.save()

        cust_name = customer.name if customer else "未知客戶"
        summary_detail = (
            f"出庫類別：{req_label}\n"
            f"設備序號：{device.code} ({device.model.name if device.model else ''})\n"
            f"目標客戶：{cust_name}\n"
            f"說明備註：{req_obj.note or '-'}"
        )

        log_action(
            user=reviewer,
            action=f"覆核核准硬體{req_label}",
            target_type="HardwareOutboundApproval",
            target_identifier=f"{device.code} -> {cust_name}",
            detail=req_obj.note or "",
            ip_address=ip_address,
        )

    notify_approval_decision(
        module_type="hardware",
        decision="approved",
        applicant=req_obj.requested_by,
        reviewer=reviewer,
        customer=customer,
        target_identifier=device.code,
        detail_text=summary_detail,
        target_path=reverse("hardware-detail", kwargs={"pk": device.pk}),
    )
    if customer:
        notify_customer_followers(
            customer=customer,
            actor=reviewer,
            event_category="data_change",
            event_title=f"硬體{req_label}已覆核出庫：{device.code}",
            event_detail=summary_detail,
        )

    return True, f"已核准設備「{device.code}」之{req_label}申請，庫存與異動歷程已正式生效！"


def reject_hardware_outbound_request(
    req_obj: HardwareOutboundApproval,
    reviewer,
    rejection_reason: str,
    ip_address: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    退回硬體出庫申請：
    - 必填退回原因
    - 將簽核單標記為 rejected
    - 自動解鎖設備釋放回「庫存中 (available)」，清除暫綁客戶與預計歸還日
    """
    if req_obj.approval_status != HardwareOutboundApproval.APPROVAL_PENDING:
        return False, "此硬體出庫簽核單已處理完畢。"

    reason = (rejection_reason or "").strip()
    if not reason:
        return False, "退回出庫申請時必須填寫「退回原因說明」。"

    device = req_obj.device
    customer = req_obj.customer
    req_label = req_obj.get_request_type_display()

    with transaction.atomic():
        if device.status in (HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN):
            device.status = HardwareDevice.STATUS_AVAILABLE
            device.customer = None
            device.expected_return_at = None
            device._skip_auto_txn = True
            device.save()

        req_obj.approval_status = HardwareOutboundApproval.APPROVAL_REJECTED
        req_obj.reviewed_by = reviewer
        req_obj.reviewed_at = timezone.now()
        req_obj.rejection_reason = reason
        req_obj.save()

        cust_name = customer.name if customer else "未知客戶"
        summary_detail = (
            f"出庫類別：{req_label}\n"
            f"設備序號：{device.code} ({device.model.name if device.model else ''})\n"
            f"原目標客戶：{cust_name}\n"
            f"（設備已自動解鎖釋放回「庫存中」）"
        )

        log_action(
            user=reviewer,
            action=f"退回硬體{req_label}申請",
            target_type="HardwareOutboundApproval",
            target_identifier=f"{device.code} -> {cust_name}",
            detail=f"退回原因: {reason}",
            ip_address=ip_address,
        )

    notify_approval_decision(
        module_type="hardware",
        decision="rejected",
        applicant=req_obj.requested_by,
        reviewer=reviewer,
        customer=customer,
        target_identifier=device.code,
        detail_text=summary_detail,
        target_path=reverse("hardware-detail", kwargs={"pk": device.pk}),
        rejection_reason=reason,
    )

    return True, f"已退回設備「{device.code}」之{req_label}申請，該設備已自動解鎖釋放回「庫存中」。"
