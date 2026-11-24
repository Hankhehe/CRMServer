from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from ldap3 import ALL, Connection, Server

from .models import Role, UserProfile, OuPermission, SystemSetting
from .permissions import extract_ou_path


class LocalModelBackend(ModelBackend):
    """
    Authenticate against the local database, but only if account_type is 'local'.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        account_type = kwargs.get("account_type", "local")
        if account_type != "local":
            return None
        return super().authenticate(request, username, password, **kwargs)


class ActiveDirectoryBackend(ModelBackend):
    """
    Authenticate against Active Directory using ldap3 without python-ldap.
    Uses a service account to search for the user DN, then binds with
    the user's credentials for verification.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        account_type = kwargs.get("account_type")
        if account_type and account_type != "ad":
            return None

        if not username or not password:
            return None

        django_username = f"{username}_ad"
        User = get_user_model()

        # Check if user exists locally and is deactivated
        try:
            user_obj = User.objects.get(username=django_username)
            if not user_obj.is_active:
                return None
        except User.DoesNotExist:
            pass

        sys_settings = SystemSetting.get_settings()
        ldap_server_uri = sys_settings.ad_ip or getattr(settings, "LDAP_SERVER_URI", "")
        ldap_bind_dn = sys_settings.ldap_bind_dn or getattr(settings, "LDAP_BIND_DN", "")
        ldap_bind_password = sys_settings.ldap_bind_password or getattr(settings, "LDAP_BIND_PASSWORD", "")
        ldap_base_dn = sys_settings.ldap_base_dn or getattr(settings, "LDAP_BASE_DN", "")

        server = Server(ldap_server_uri, get_info=ALL)
        try:
            with Connection(
                server,
                user=ldap_bind_dn,
                password=ldap_bind_password,
                auto_bind=True,
            ) as conn:
                search_filter = f"(sAMAccountName={username})"
                if not conn.search(
                    ldap_base_dn, search_filter, attributes=["*", "memberOf"]
                ):
                    return None
                if not conn.entries:
                    return None
                entry = conn.entries[0]
                user_dn = entry.entry_dn
        except Exception:
            return None

        # Verify credentials by binding
        try:
            with Connection(server, user=user_dn, password=password, auto_bind=True):
                pass
        except Exception:
            return None

        # Parse group memberships
        member_of = getattr(entry, "memberOf", [])
        group_names = []
        for g_dn in member_of:
            for part in str(g_dn).split(","):
                part = part.strip()
                if part.upper().startswith("CN="):
                    group_names.append(part[3:])
                    break

        configured_groups = OuPermission.objects.values_list("ou_path", flat=True)
        configured_groups_lower = [g.strip().lower() for g in configured_groups]
        matched_roles = []
        if configured_groups_lower:
            user_groups_lower = [g.lower() for g in group_names]
            for g in group_names:
                for perm in OuPermission.objects.all():
                    if perm.ou_path.strip().lower() == g.lower():
                        matched_roles.append(perm.role)

            if not any(g in configured_groups_lower for g in user_groups_lower):
                return None

        user_obj, _ = User.objects.get_or_create(username=django_username)
        user_obj.first_name = getattr(entry, "givenName", "") or ""
        user_obj.last_name = getattr(entry, "sn", "") or ""
        user_obj.email = getattr(entry, "mail", "") or ""
        user_obj.save()

        # Update profile
        profile, created = UserProfile.objects.get_or_create(user=user_obj)
        dn_str = str(user_dn)
        profile.distinguished_name = dn_str
        profile.ou_path = extract_ou_path(dn_str)
        profile.ad_groups = ",".join(group_names)

        if created:
            Role.ensure_system_roles()
            if len(matched_roles) == 1:
                profile.role = matched_roles[0]
                profile.role_obj = Role.objects.filter(code=matched_roles[0]).first()
                profile.apply_role_defaults()
            elif len(matched_roles) > 1:
                # 跨多個 AD 安全群組：自動聯集 (Union) 所有群組對應之權限旗標
                union_flags = {
                    "can_view_crm": False,
                    "can_edit_customers": False,
                    "can_edit_licenses": False,
                    "can_edit_products": False,
                    "can_edit_crm": False,
                    "can_edit_probes": False,
                    "can_manage_system": False,
                    "can_approve_licenses": False,
                    "can_approve_contracts": False,
                    "can_approve_hardware": False,
                }
                for r in matched_roles:
                    r_flags = UserProfile.get_role_default_flags(r)
                    for k, v in r_flags.items():
                        if v:
                            union_flags[k] = True
                
                profile.role = UserProfile.match_role_from_flags(union_flags)
                profile.role_obj = Role.objects.filter(code=profile.role).first()
                for k, v in union_flags.items():
                    setattr(profile, k, v)
            elif not matched_roles:
                profile.role = UserProfile.ROLE_VIEW
                profile.role_obj = Role.objects.filter(code="view").first()
                profile.apply_role_defaults()
        else:
            # 既有帳號登入：若管理者曾在後台手動指派角色，尊重手動設定不予覆寫
            if not profile.role_obj and profile.role:
                profile.role_obj = Role.objects.filter(code=profile.role).first()

        profile.save()
        return user_obj
