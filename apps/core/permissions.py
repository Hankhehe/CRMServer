from typing import Optional
from django.contrib.auth.models import AbstractBaseUser
from django.db.models import QuerySet
from .models import UserProfile, OuPermission, AuditLog


def extract_ou_path(distinguished_name: str) -> str:
    if not distinguished_name:
        return ""
    parts = distinguished_name.split(",")
    ou_parts = [p.strip() for p in parts if p.strip().upper().startswith("OU=")]
    return ",".join(ou_parts)


def get_user_profile(user: AbstractBaseUser) -> Optional[UserProfile]:
    if not user or not user.is_authenticated:
        return None
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def _get_user_ad_matched_roles(profile: UserProfile) -> set[str]:
    """取得使用者所屬 AD 群組在 OuPermission 中映射的所有角色集合"""
    ad_groups_val = getattr(profile, "ad_groups", "")
    if not ad_groups_val:
        return set()
    user_groups = {g.strip().lower() for g in ad_groups_val.split(",") if g.strip()}
    if not user_groups:
        return set()
    matched = set()
    for perm in OuPermission.objects.all():
        if perm.ou_path.strip().lower() in user_groups:
            matched.add(perm.role)
    return matched


def get_user_role(user: AbstractBaseUser) -> str:
    """取得使用者的系統角色簡短中文名稱"""
    if not user or not user.is_authenticated:
        return "訪客"
    if user.is_superuser:
        return "系統超級管理員"
    if getattr(user, "is_staff", False):
        return "內部管理員"
    profile = get_user_profile(user)
    if profile:
        full_label = profile.get_role_display()
        return full_label.split(" (")[0] if " (" in full_label else full_label
    return "一般使用者"


def user_can_view_crm(user: AbstractBaseUser) -> bool:
    """
    檢查是否可瀏覽 CRM 模組（總覽儀表板、客戶名冊、維護合約、授權書、軟體產品主檔）。
    僅當角色為「硬體設備查詢人員 (hw_viewer)」且無任何 CRM 相關權限時為 False。
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if not profile:
        return True

    # 若擁有任何 CRM 編輯或系統管理權限則必定可檢視
    if (
        profile.has_customer_edit_access
        or profile.has_license_edit_access
        or profile.has_product_edit_access
        or profile.has_system_manage_access
    ):
        return True

    ad_roles = _get_user_ad_matched_roles(profile)
    non_hw_viewer_roles = {
        UserProfile.ROLE_ALL,
        UserProfile.ROLE_SYS_ADMIN,
        UserProfile.ROLE_CUSTOMER_ADMIN,
        UserProfile.ROLE_LICENSE_ADMIN,
        UserProfile.ROLE_PROBE,
        UserProfile.ROLE_VIEW,
        UserProfile.ROLE_CRM,
    }
    if ad_roles & non_hw_viewer_roles:
        return True

    if profile.role == UserProfile.ROLE_HW_VIEWER:
        return False

    return profile.can_view_crm


def user_can_edit_customers(user: AbstractBaseUser) -> bool:
    """檢查是否擁有「客戶基本資料、聯絡窗口、客戶自訂環境資訊區塊」的新增/編輯/刪除權限"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if profile:
        if profile.has_customer_edit_access:
            return True
        ad_roles = _get_user_ad_matched_roles(profile)
        if ad_roles & {
            OuPermission.ROLE_ALL,
            OuPermission.ROLE_CRM,
            OuPermission.ROLE_CUSTOMER_ADMIN,
        }:
            return True
    return False


def user_can_edit_licenses(user: AbstractBaseUser) -> bool:
    """檢查是否擁有「軟體授權書 (CustomerLicense)」與「維護保固合約 (MaintenanceContract)」的開立/編輯/刪除權限"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if profile:
        if profile.has_license_edit_access:
            return True
        ad_roles = _get_user_ad_matched_roles(profile)
        if ad_roles & {
            OuPermission.ROLE_ALL,
            OuPermission.ROLE_CRM,
            OuPermission.ROLE_LICENSE_ADMIN,
        }:
            return True
    return False


def user_can_edit_contracts(user: AbstractBaseUser) -> bool:
    """維護保固合約與軟體授權書同屬「授權與保固開立人員」職責"""
    return user_can_edit_licenses(user)


def user_can_edit_products(user: AbstractBaseUser) -> bool:
    """檢查是否擁有「軟體產品主檔 (Product)」的新增/編輯/刪除權限（系統維護人員 / 全域管理員）"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if profile:
        if profile.has_product_edit_access:
            return True
        ad_roles = _get_user_ad_matched_roles(profile)
        if ad_roles & {
            OuPermission.ROLE_ALL,
            OuPermission.ROLE_SYS_ADMIN,
            OuPermission.ROLE_CRM,
        }:
            return True
    return False


def user_can_edit_crm(user: AbstractBaseUser) -> bool:
    """
    相容性輔助函式：檢查是否擁有客戶資料編輯權限或授權合約編輯權限。
    在細部拆分後，客戶詳情與客戶列表請優先使用 user_can_edit_customers / user_can_edit_licenses。
    """
    return user_can_edit_customers(user) or user_can_edit_licenses(user)


def user_can_edit_probe(user: AbstractBaseUser) -> bool:
    """檢查是否擁有硬體設備庫存、進出貨交易與供應商編輯權限"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if profile:
        if profile.has_probe_edit_access:
            return True
        ad_roles = _get_user_ad_matched_roles(profile)
        if ad_roles & {OuPermission.ROLE_ALL, OuPermission.ROLE_PROBE}:
            return True
    return False


user_can_edit_hardware = user_can_edit_probe


def user_can_edit_hardware_models(user: AbstractBaseUser) -> bool:
    """檢查是否擁有「硬體規格主檔 (HardwareModel)」的新增/編輯/刪除權限（設備庫存人員與系統維護人員皆可）"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if profile:
        if profile.has_hardware_model_edit_access:
            return True
        ad_roles = _get_user_ad_matched_roles(profile)
        if ad_roles & {
            OuPermission.ROLE_ALL,
            OuPermission.ROLE_PROBE,
            OuPermission.ROLE_SYS_ADMIN,
        }:
            return True
    return False


def user_can_manage_system(user: AbstractBaseUser) -> bool:
    """檢查是否擁有系統設定（AD 映射、SMTP 設定、帳號管理、審計日誌）權限"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if profile:
        if profile.has_system_manage_access:
            return True
        ad_roles = _get_user_ad_matched_roles(profile)
        if ad_roles & {OuPermission.ROLE_ALL, OuPermission.ROLE_SYS_ADMIN}:
            return True
    return False


def user_can_approve_licenses(user: AbstractBaseUser) -> bool:
    """檢查是否擁有「軟體授權書 (CustomerLicense)」的簽核覆核權限"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if profile:
        if profile.has_license_approve_access:
            return True
        ad_roles = _get_user_ad_matched_roles(profile)
        if OuPermission.ROLE_ALL in ad_roles:
            return True
    return False


def user_can_approve_contracts(user: AbstractBaseUser) -> bool:
    """檢查是否擁有「維護保固合約 (MaintenanceContract)」的簽核覆核權限"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if profile:
        if profile.has_contract_approve_access:
            return True
        ad_roles = _get_user_ad_matched_roles(profile)
        if OuPermission.ROLE_ALL in ad_roles:
            return True
    return False


def user_can_approve_hardware(user: AbstractBaseUser) -> bool:
    """檢查是否擁有「硬體設備出庫 (HardwareOutboundApproval)」的簽核覆核權限"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "is_staff", False):
        return True
    profile = get_user_profile(user)
    if profile:
        if profile.has_hardware_approve_access:
            return True
        ad_roles = _get_user_ad_matched_roles(profile)
        if OuPermission.ROLE_ALL in ad_roles:
            return True
    return False


def is_license_approval_required_for_user(user: AbstractBaseUser) -> bool:
    """判斷該操作者開立/修改軟體授權書核心條件時是否需要進入待覆核"""
    from .models import SystemSetting
    if not SystemSetting.get_settings().require_license_approval:
        return False
    return not user_can_approve_licenses(user)


def is_warranty_approval_required_for_user(user: AbstractBaseUser) -> bool:
    """判斷該操作者開立/修改維護保固合約核心條件時是否需要進入待覆核"""
    from .models import SystemSetting
    if not SystemSetting.get_settings().require_warranty_approval:
        return False
    return not user_can_approve_contracts(user)


def is_hardware_approval_required_for_user(user: AbstractBaseUser) -> bool:
    """判斷該操作者執行硬體出庫/借測/RMA 換機時是否需要進入待覆核"""
    from .models import SystemSetting
    if not SystemSetting.get_settings().require_hardware_approval:
        return False
    return not user_can_approve_hardware(user)


def user_can_edit(user: AbstractBaseUser) -> bool:
    """相容性輔助函式：若有任一模組編輯權限則為 True"""
    return (
        user_can_edit_customers(user)
        or user_can_edit_licenses(user)
        or user_can_edit_products(user)
        or user_can_edit_hardware(user)
        or user_can_manage_system(user)
    )


# ---------------------------------------------------------------------------
# 客戶資料視野隔離 (Customer Data Scope: All vs Followed Only)
# ---------------------------------------------------------------------------

def is_user_followed_only_scope(user: AbstractBaseUser) -> bool:
    """判斷該使用者是否處於「僅限查看已關注客戶」的業務隔離視野模式"""
    if not user or not user.is_authenticated:
        return True
    if user.is_superuser or getattr(user, "is_staff", False):
        return False
    if user_can_manage_system(user):
        return False
    profile = get_user_profile(user)
    if profile and profile.customer_scope == UserProfile.SCOPE_FOLLOWED:
        return True
    return False


def filter_customers_for_user(user: AbstractBaseUser, queryset: QuerySet) -> QuerySet:
    """依據使用者的客戶資料視野 (customer_scope) 過濾 Customer QuerySet"""
    if not user or not user.is_authenticated:
        return queryset.none()
    if is_user_followed_only_scope(user):
        return queryset.filter(followers__user=user).distinct()
    return queryset


def filter_by_customer_scope(
    user: AbstractBaseUser, queryset: QuerySet, customer_lookup: str = "customer"
) -> QuerySet:
    """
    依據使用者的客戶資料視野過濾關聯至 Customer 的模型 QuerySet
    (例如 MaintenanceContract, CustomerLicense)。
    """
    if not user or not user.is_authenticated:
        return queryset.none()
    if is_user_followed_only_scope(user):
        filter_kwargs = {f"{customer_lookup}__followers__user": user}
        return queryset.filter(**filter_kwargs).distinct()
    return queryset


def user_can_access_customer(user: AbstractBaseUser, customer) -> bool:
    """檢查使用者是否有權限檢視/存取特定單一客戶"""
    if not user or not user.is_authenticated or not customer:
        return False
    if not user_can_view_crm(user):
        return False
    if is_user_followed_only_scope(user):
        from apps.customers.models import CustomerFollow
        return CustomerFollow.objects.filter(customer=customer, user=user).exists()
    return True


def get_ad_groups() -> list[str]:
    from django.conf import settings
    from ldap3 import Server, Connection, ALL
    from .models import SystemSetting

    sys_settings = SystemSetting.get_settings()
    ldap_server_uri = sys_settings.ad_ip or getattr(settings, "LDAP_SERVER_URI", "")
    ldap_bind_dn = sys_settings.ldap_bind_dn or getattr(settings, "LDAP_BIND_DN", "")
    ldap_bind_password = sys_settings.ldap_bind_password or getattr(settings, "LDAP_BIND_PASSWORD", "")
    ldap_base_dn = sys_settings.ldap_base_dn or getattr(settings, "LDAP_BASE_DN", "")

    groups = []
    if ldap_server_uri and ldap_bind_dn and ldap_bind_password:
        try:
            server = Server(ldap_server_uri, get_info=ALL)
            with Connection(
                server,
                user=ldap_bind_dn,
                password=ldap_bind_password,
                auto_bind=True,
            ) as conn:
                search_filter = "(objectClass=group)"
                if conn.search(ldap_base_dn, search_filter, attributes=["cn"]):
                    for entry in conn.entries:
                        cn = getattr(entry, "cn", None)
                        if cn:
                            groups.append(str(cn))
        except Exception:
            pass

    if not groups:
        groups = [
            "AllTeams",
            "IT",
            "License",
            "Management",
            "RD",
            "Sales",
            "Tech",
            "VPN",
        ]

    try:
        existing_groups = OuPermission.objects.values_list("ou_path", flat=True)
        groups.extend(existing_groups)
    except Exception:
        pass

    return sorted(list(set(groups)))


def cleanup_expired_audit_logs(retention_days: Optional[int] = None) -> int:
    """
    依據 SystemSetting.audit_log_retention_days 自動刪除超過保留期限的 AuditLog。
    若 retention_days 為 0 或未設定，代表永久保留不刪除。回傳刪除的日誌筆數。
    """
    import datetime
    from django.utils import timezone
    from .models import SystemSetting

    try:
        if retention_days is None:
            retention_days = SystemSetting.get_settings().audit_log_retention_days
        days = int(retention_days or 0)
        if days <= 0:
            return 0
        cutoff = timezone.now() - datetime.timedelta(days=days)
        deleted_count, _ = AuditLog.objects.filter(created_at__lt=cutoff).delete()
        return deleted_count
    except Exception:
        return 0


def log_action(
    user: Optional[AbstractBaseUser],
    action: str,
    target_type: str = "",
    target_identifier: str = "",
    detail: str = "",
    ip_address: Optional[str] = None,
) -> None:
    cleanup_expired_audit_logs()
    AuditLog.objects.create(
        user=user if user and user.is_authenticated else None,
        action=action,
        target_type=target_type,
        target_identifier=target_identifier,
        detail=detail,
        ip_address=ip_address,
    )
