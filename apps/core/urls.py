from django.urls import path
from . import views
from . import certificate_views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("change-password/", views.change_own_password, name="change-own-password"),
    path("account-center/", views.account_center, name="account-center"),
    # Accounts & Roles
    path("accounts/", views.account_list, name="account-list"),
    path("accounts/create/", views.account_create, name="account-create"),
    path("accounts/<int:pk>/edit/", views.account_update, name="account-update"),
    path("accounts/<int:pk>/delete/", views.account_delete, name="account-delete"),
    path("accounts/<int:pk>/change-password/", views.account_change_password, name="account-change-password"),
    path("roles/create/", views.role_create, name="role-create"),
    path("roles/<int:pk>/edit/", views.role_update, name="role-update"),
    path("roles/<int:pk>/delete/", views.role_delete, name="role-delete"),
    # Permissions & LDAP
    path("permissions/", views.ou_permission_list, name="ou-permissions"),
    path("permissions/<int:pk>/delete/", views.ou_permission_delete, name="ou-permission-delete"),
    path("smtp-settings/", views.smtp_setting_view, name="smtp-settings"),
    # Certificate Export & Templates
    path("certificate-templates/", certificate_views.cert_template_settings_view, name="cert-template-settings"),
    path("certificates/data/<str:cert_type>/<int:pk>/", certificate_views.cert_data_api, name="cert-data-api"),
    path("certificates/audit/", certificate_views.cert_audit_api, name="cert-audit-api"),
    path("certificates/bg/<str:cert_type>/", certificate_views.cert_bg_serve, name="cert-bg-serve"),
    # Audit, Migration & Backup
    path("audit-logs/", views.audit_log_list, name="audit-log-list"),
    path("audit-logs/export/", views.audit_log_export, name="audit-log-export"),
    path("db-migration/", views.db_migration_view, name="db-migration"),
    path("backup/", views.backup_management_view, name="backup-management"),
]
