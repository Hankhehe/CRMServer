from .permissions import (
    user_can_view_crm,
    user_can_edit_customers,
    user_can_edit_licenses,
    user_can_edit_products,
    user_can_edit_probe,
    user_can_edit_hardware_models,
    user_can_edit_crm,
    user_can_manage_system,
    user_can_approve_licenses,
    user_can_approve_contracts,
    user_can_approve_hardware,
    filter_by_customer_scope,
    is_user_followed_only_scope,
    get_user_role,
)
from .models import SystemSetting


def permission_context(request):
    try:
        sys_settings = SystemSetting.get_settings()
        idle_timeout_minutes = sys_settings.idle_timeout_minutes or 10
        require_license_approval = bool(sys_settings.require_license_approval)
        require_warranty_approval = bool(sys_settings.require_warranty_approval)
        require_hardware_approval = bool(sys_settings.require_hardware_approval)
    except Exception:
        idle_timeout_minutes = 10
        require_license_approval = False
        require_warranty_approval = False
        require_hardware_approval = False

    if not request.user.is_authenticated:
        return {
            "can_view_crm": False,
            "can_edit_customers": False,
            "can_edit_licenses": False,
            "can_edit_contracts": False,
            "can_edit_products": False,
            "can_edit_probe": False,
            "can_edit_hardware": False,
            "can_edit_hardware_models": False,
            "can_edit_crm": False,
            "can_manage_system": False,
            "can_approve_licenses": False,
            "can_approve_contracts": False,
            "can_approve_hardware": False,
            "can_edit": False,
            "is_followed_only_scope": False,
            "user_role": "訪客",
            "idle_timeout_minutes": idle_timeout_minutes,
            "require_license_approval": require_license_approval,
            "require_warranty_approval": require_warranty_approval,
            "require_hardware_approval": require_hardware_approval,
            "nav_pending_licenses_count": 0,
            "nav_pending_contracts_count": 0,
            "nav_pending_hardware_count": 0,
        }

    c_view_crm = user_can_view_crm(request.user)
    c_cust = user_can_edit_customers(request.user)
    c_lic = user_can_edit_licenses(request.user)
    c_prod = user_can_edit_products(request.user)
    c_probe = user_can_edit_probe(request.user)
    c_hw_models = user_can_edit_hardware_models(request.user)
    c_crm = user_can_edit_crm(request.user)
    c_sys = user_can_manage_system(request.user)
    c_app_lic = user_can_approve_licenses(request.user)
    c_app_con = user_can_approve_contracts(request.user)
    c_app_hw = user_can_approve_hardware(request.user)
    followed_only = is_user_followed_only_scope(request.user)

    nav_pending_licenses_count = 0
    nav_pending_contracts_count = 0
    nav_pending_hardware_count = 0

    try:
        if c_app_lic and c_view_crm:
            from apps.licenses.models import CustomerLicense
            qs_lic = filter_by_customer_scope(
                request.user, CustomerLicense.objects.filter(approval_status="pending")
            )
            nav_pending_licenses_count = qs_lic.count()
        if c_app_con and c_view_crm:
            from apps.customers.models import MaintenanceContract
            qs_con = filter_by_customer_scope(
                request.user, MaintenanceContract.objects.filter(approval_status="pending")
            )
            nav_pending_contracts_count = qs_con.count()
        if c_app_hw:
            from apps.hardware.models import HardwareDevice
            nav_pending_hardware_count = HardwareDevice.objects.filter(
                is_deleted=False,
                status__in=[HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN],
            ).count()
    except Exception:
        pass

    return {
        "can_view_crm": c_view_crm,
        "can_edit_customers": c_cust,
        "can_edit_licenses": c_lic,
        "can_edit_contracts": c_lic,
        "can_edit_products": c_prod,
        "can_edit_probe": c_probe,
        "can_edit_hardware": c_probe,
        "can_edit_hardware_models": c_hw_models,
        "can_edit_crm": c_crm,
        "can_manage_system": c_sys,
        "can_approve_licenses": c_app_lic,
        "can_approve_contracts": c_app_con,
        "can_approve_hardware": c_app_hw,
        "can_edit": c_cust or c_lic or c_prod or c_probe or c_sys,
        "is_followed_only_scope": followed_only,
        "user_role": get_user_role(request.user),
        "idle_timeout_minutes": idle_timeout_minutes,
        "require_license_approval": require_license_approval,
        "require_warranty_approval": require_warranty_approval,
        "require_hardware_approval": require_hardware_approval,
        "nav_pending_licenses_count": nav_pending_licenses_count,
        "nav_pending_contracts_count": nav_pending_contracts_count,
        "nav_pending_hardware_count": nav_pending_hardware_count,
    }
