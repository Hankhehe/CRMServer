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
    user_can_edit_orders,
    user_can_approve_orders,
    user_can_ship_orders,
    user_can_close_orders,
    filter_by_customer_scope,
    is_user_followed_only_scope,
    get_user_role,
)
from .models import SystemSetting
from .versioning import get_version_context


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
        ctx = {
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
            "can_edit_orders": False,
            "can_approve_orders": False,
            "can_ship_orders": False,
            "can_close_orders": False,
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
            "nav_actionable_orders_count": 0,
        }
        ctx.update(get_version_context(include_changelog=False, include_admin_meta=False))
        return ctx

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
    c_edit_ord = user_can_edit_orders(request.user)
    c_app_ord = user_can_approve_orders(request.user)
    c_ship_ord = user_can_ship_orders(request.user)
    c_close_ord = user_can_close_orders(request.user)
    followed_only = is_user_followed_only_scope(request.user)

    nav_pending_licenses_count = 0
    nav_pending_contracts_count = 0
    nav_pending_hardware_count = 0
    nav_actionable_orders_count = 0

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
        if c_view_crm or c_ship_ord or c_app_ord or c_close_ord or c_edit_ord:
            from django.db.models import Q
            from apps.orders.models import Order, OrderPayment
            scoped_orders = filter_by_customer_scope(request.user, Order.objects.all(), customer_lookup="customer")
            q_action = Q()
            has_q = False
            if c_app_ord:
                q_action |= Q(status=Order.STATUS_PENDING_APPROVAL)
                has_q = True
            if c_ship_ord:
                q_action |= Q(status=Order.STATUS_PENDING_SHIPMENT)
                has_q = True
            if c_close_ord:
                q_action |= Q(status=Order.STATUS_PENDING_PAYMENT, payments__status=OrderPayment.STATUS_PENDING)
                has_q = True
            if c_edit_ord:
                q_action |= Q(status=Order.STATUS_REJECTED)
                q_action |= Q(status=Order.STATUS_PENDING_PAYMENT, salesperson=request.user)
                has_q = True
            if has_q:
                nav_actionable_orders_count = scoped_orders.filter(q_action).distinct().count()
    except Exception:
        pass

    ctx = {
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
        "can_edit_orders": c_edit_ord,
        "can_approve_orders": c_app_ord,
        "can_ship_orders": c_ship_ord,
        "can_close_orders": c_close_ord,
        "can_edit": c_cust or c_lic or c_prod or c_probe or c_edit_ord or c_sys,
        "is_followed_only_scope": followed_only,
        "user_role": get_user_role(request.user),
        "idle_timeout_minutes": idle_timeout_minutes,
        "require_license_approval": require_license_approval,
        "require_warranty_approval": require_warranty_approval,
        "require_hardware_approval": require_hardware_approval,
        "nav_pending_licenses_count": nav_pending_licenses_count,
        "nav_pending_contracts_count": nav_pending_contracts_count,
        "nav_pending_hardware_count": nav_pending_hardware_count,
        "nav_actionable_orders_count": nav_actionable_orders_count,
    }
    ctx.update(
        get_version_context(
            include_changelog=True,
            include_admin_meta=(c_sys or request.user.is_superuser),
        )
    )
    return ctx
