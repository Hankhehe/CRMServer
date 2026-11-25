"""集中式業務單據自動編號模組。

統一規則：<前綴>-YYYYMMDD-XXXX（台灣時區 Asia/Taipei，當日 4 碼遞增流水號 0001 起）
- 維護合約 (MaintenanceContract): W
- 軟體授權書 (CustomerLicense): L
- 訂單 (Order): SO
- 專業服務 (CustomerServiceRecord): SRV
"""

from typing import Callable, Optional
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

PREFIX_CONTRACT = "W"
PREFIX_LICENSE = "L"
PREFIX_ORDER = "SO"
PREFIX_SERVICE = "SRV"


def generate_daily_seq_no(
    model_cls,
    field_name: str,
    prefix_code: str,
    digits: int = 4,
) -> str:
    """產生指定單據當日唯一的遞增編號，格式：<prefix_code>-YYYYMMDD-XXXX。"""
    today_str = timezone.now().astimezone(TAIPEI_TZ).strftime("%Y%m%d")
    prefix = f"{prefix_code}-{today_str}-"
    existing = model_cls.objects.filter(
        **{f"{field_name}__startswith": prefix}
    ).values_list(field_name, flat=True)

    max_seq = 0
    for code in existing:
        if not code:
            continue
        tail = code[len(prefix):]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))

    seq = max_seq + 1
    candidate = f"{prefix}{seq:0{digits}d}"
    while model_cls.objects.filter(**{field_name: candidate}).exists():
        seq += 1
        candidate = f"{prefix}{seq:0{digits}d}"
    return candidate


def generate_contract_no() -> str:
    """產生維護合約編號，格式: W-YYYYMMDD-XXXX"""
    from apps.customers.models import MaintenanceContract

    return generate_daily_seq_no(MaintenanceContract, "contract_no", PREFIX_CONTRACT)


def generate_license_no() -> str:
    """產生軟體授權書編號，格式: L-YYYYMMDD-XXXX"""
    from apps.licenses.models import CustomerLicense

    return generate_daily_seq_no(CustomerLicense, "license_no", PREFIX_LICENSE)


def generate_order_no() -> str:
    """產生訂單單號，格式: SO-YYYYMMDD-XXXX"""
    from apps.orders.models import Order

    return generate_daily_seq_no(Order, "order_no", PREFIX_ORDER)


def generate_service_no() -> str:
    """產生專業服務單號，格式: SRV-YYYYMMDD-XXXX"""
    from apps.customers.models import CustomerServiceRecord

    return generate_daily_seq_no(CustomerServiceRecord, "service_no", PREFIX_SERVICE)


def validate_unique_doc_no(
    model_cls,
    field_name: str,
    value: str,
    *,
    instance_pk=None,
    generator: Optional[Callable[[], str]] = None,
    label: str = "編號",
) -> str:
    """檢查單據編號是否撞號；若已被使用則拋出含建議新編號的 ValidationError。"""
    cleaned = (value or "").strip()
    if not cleaned:
        return cleaned

    qs = model_cls.objects.filter(**{field_name: cleaned})
    if instance_pk is not None:
        qs = qs.exclude(pk=instance_pk)

    if qs.exists():
        if generator is not None:
            suggested = generator()
            raise forms.ValidationError(
                f"{label}「{cleaned}」已被使用，建議改用「{suggested}」。"
            )
        raise forms.ValidationError(f"{label}「{cleaned}」已被使用，請改用其他編號。")

    return cleaned
