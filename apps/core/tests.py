from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.core.models import Role, UserProfile, OuPermission, AuditLog, SystemSetting
from apps.core.permissions import (
    user_can_edit_crm,
    user_can_edit_probe,
    user_can_manage_system,
)

User = get_user_model()


class CorePermissionsAndDashboardTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin", password="password123", email="admin@test.com"
        )
        self.crm_user = User.objects.create_user(
            username="crm_user", password="password123"
        )
        UserProfile.objects.create(
            user=self.crm_user,
            role=UserProfile.ROLE_CRM,
            can_edit_crm=True,
            can_edit_probes=False,
            can_manage_system=False,
        )

        self.probe_user = User.objects.create_user(
            username="probe_user", password="password123"
        )
        UserProfile.objects.create(
            user=self.probe_user,
            role=UserProfile.ROLE_PROBE,
            can_edit_crm=False,
            can_edit_probes=True,
            can_manage_system=False,
        )

        self.view_user = User.objects.create_user(
            username="view_user", password="password123"
        )
        UserProfile.objects.create(
            user=self.view_user,
            role=UserProfile.ROLE_VIEW,
            can_edit_crm=False,
            can_edit_probes=False,
            can_manage_system=False,
        )

    def test_permission_logic(self):
        # Admin has all permissions
        self.assertTrue(user_can_edit_crm(self.admin))
        self.assertTrue(user_can_edit_probe(self.admin))
        self.assertTrue(user_can_manage_system(self.admin))

        # CRM user has CRM permission only
        self.assertTrue(user_can_edit_crm(self.crm_user))
        self.assertFalse(user_can_edit_probe(self.crm_user))
        self.assertFalse(user_can_manage_system(self.crm_user))

        # Probe user has Probe permission only
        self.assertFalse(user_can_edit_crm(self.probe_user))
        self.assertTrue(user_can_edit_probe(self.probe_user))
        self.assertFalse(user_can_manage_system(self.probe_user))

        # View only user
        self.assertFalse(user_can_edit_crm(self.view_user))
        self.assertFalse(user_can_edit_probe(self.view_user))
        self.assertFalse(user_can_manage_system(self.view_user))

    def test_dashboard_view(self):
        client = Client()
        client.force_login(self.admin)
        resp = client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CRM & 資產總覽儀表板")


class BackupAndRestoreTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="backup_admin", password="password123", email="admin@test.com"
        )
        self.normal_user = User.objects.create_user(
            username="normal_user", password="password123"
        )
        self.client = Client()

    def test_backup_view_access(self):
        # Unauthenticated user redirected to login
        resp = self.client.get(reverse("backup-management"))
        self.assertEqual(resp.status_code, 302)

        # Normal user redirected to dashboard (forbidden)
        self.client.force_login(self.normal_user)
        resp = self.client.get(reverse("backup-management"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("dashboard"), resp.url)

        # Superuser allowed
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("backup-management"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "資料備份與還原")

    def test_backup_create_download_and_delete_flow(self):
        self.client.force_login(self.admin)

        # 1. Create backup via POST
        resp = self.client.post(reverse("backup-management"), {"action": "create_backup"}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "成功建立系統備份")

        from apps.core.backup_service import list_backups, get_backup_file, delete_backup
        backups = list_backups()
        self.assertGreaterEqual(len(backups), 1)
        latest_filename = backups[0]["filename"]

        # 2. Path traversal security test
        self.assertIsNone(get_backup_file("../evil.zip"))
        self.assertIsNone(get_backup_file("/etc/passwd.zip"))

        # 3. Download backup
        resp = self.client.post(
            reverse("backup-management"),
            {"action": "download_backup", "filename": latest_filename},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("zip", resp.headers.get("Content-Type", "").lower())
        resp.close()

        # 4. Delete backup
        resp = self.client.post(
            reverse("backup-management"),
            {"action": "delete_backup", "filename": latest_filename},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "已成功刪除備份檔")

    def test_db_migration_view_postgres_support(self):
        # 測試超級管理員訪問資料庫轉換頁面
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("db-migration"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "PostgreSQL")
        self.assertContains(resp, "資料庫轉移與切換")

        # 測試未安裝 psycopg2 時測試連線的回傳提示
        test_resp = self.client.post(
            reverse("db-migration"),
            {
                "action": "test_postgres_connection",
                "host": "127.0.0.1",
                "port": "5432",
                "name": "test_crm",
                "user": "postgres",
                "password": "",
            },
        )
        self.assertEqual(test_resp.status_code, 200)
        data = test_resp.json()
        self.assertIn("success", data)

    def test_db_migrator_service_functions(self):
        from apps.core.db_migrator import (
            export_current_data,
            get_target_db_settings,
            recreate_target_database,
        )

        # 1. 測試目前資料庫完整導出
        p, msg = export_current_data("default")
        self.assertIsNotNone(p)
        self.assertTrue(p.exists())
        self.assertGreater(p.stat().st_size, 0)
        p.unlink()

        # 2. 測試產生各資料庫設定字典包含 TIME_ZONE 等關鍵設定
        pg_cfg = get_target_db_settings("postgres", {"host": "127.0.0.1", "name": "TestDB"})
        self.assertEqual(pg_cfg["ENGINE"], "django.db.backends.postgresql")
        self.assertIn("TIME_ZONE", pg_cfg)
        self.assertIn("AUTOCOMMIT", pg_cfg)

        ms_cfg = get_target_db_settings("mssql", {"host": "127.0.0.1", "name": "TestDB"})
        self.assertEqual(ms_cfg["ENGINE"], "mssql")
        self.assertIn("TIME_ZONE", ms_cfg)

        sqlite_cfg = get_target_db_settings("sqlite", {})
        self.assertEqual(sqlite_cfg["ENGINE"], "django.db.backends.sqlite3")

        # 3. 測試不支援的資料庫類型
        ok, err_msg = recreate_target_database("oracle", {})
        self.assertFalse(ok)
        self.assertIn("不支援", err_msg)


class GranularRBACAndCustomerScopeTest(TestCase):
    """測試六種角色 (RBAC) 與客戶負責範圍 (customer_scope) 權限隔離機制"""

    def setUp(self):
        from apps.customers.models import Customer, CustomerFollow, MaintenanceContract
        from apps.licenses.models import Product, CustomerLicense
        from apps.hardware.models import HardwareModel

        # 建立測試資料
        self.cust_a = Customer.objects.create(code="CUST-A", name="甲科技股份有限公司")
        self.cust_b = Customer.objects.create(code="CUST-B", name="乙系統有限公司")
        self.prod = Product.objects.create(code="PROD-01", name="DPI 核心軟體", default_unit="套")
        self.hw_model = HardwareModel.objects.create(model_code="HW-1000", name="Probe 10G", port_count=4)

        self.lic_a = CustomerLicense.objects.create(
            license_no="LIC-A-001", customer=self.cust_a, product=self.prod, quantity=1, is_perpetual=True
        )
        self.lic_b = CustomerLicense.objects.create(
            license_no="LIC-B-001", customer=self.cust_b, product=self.prod, quantity=1, is_perpetual=True
        )
        self.contract_a = MaintenanceContract.objects.create(
            contract_no="MC-A-001", customer=self.cust_a, name="甲科技合約"
        )
        self.contract_b = MaintenanceContract.objects.create(
            contract_no="MC-B-001", customer=self.cust_b, name="乙系統合約"
        )

        # 建立六種角色帳號
        self.sys_admin = self._make_user("sys_admin_u", UserProfile.ROLE_SYS)
        self.customer_admin = self._make_user("cust_admin_u", UserProfile.ROLE_CUSTOMER_ADMIN)
        self.license_admin = self._make_user("lic_admin_u", UserProfile.ROLE_LICENSE_ADMIN)
        self.probe_admin = self._make_user("probe_admin_u", UserProfile.ROLE_PROBE)
        self.view_user = self._make_user("view_u", UserProfile.ROLE_VIEW)
        self.hw_viewer = self._make_user("hw_viewer_u", UserProfile.ROLE_HW_VIEWER)

        # 建立兩位設定為「僅限已關注客戶 (followed)」的業務帳號
        self.sales_1 = self._make_user(
            "sales_1", UserProfile.ROLE_CUSTOMER_ADMIN, customer_scope=UserProfile.SCOPE_FOLLOWED
        )
        self.sales_2 = self._make_user(
            "sales_2", UserProfile.ROLE_VIEW, customer_scope=UserProfile.SCOPE_FOLLOWED
        )
        # sales_1 只關注 cust_a；sales_2 只關注 cust_b
        CustomerFollow.objects.create(customer=self.cust_a, user=self.sales_1)
        CustomerFollow.objects.create(customer=self.cust_b, user=self.sales_2)

    def _make_user(self, username, role, customer_scope=UserProfile.SCOPE_ALL):
        Role.ensure_system_roles()
        role_obj = Role.objects.filter(code=role).first()
        u = User.objects.create_user(username=username, password="password123")
        p = UserProfile.objects.create(user=u, role=role, role_obj=role_obj, customer_scope=customer_scope)
        p.apply_role_defaults()
        p.save()
        return u

    def test_six_roles_permission_matrix(self):
        from apps.core.permissions import (
            user_can_view_crm,
            user_can_edit_customers,
            user_can_edit_licenses,
            user_can_edit_products,
            user_can_edit_probe,
            user_can_edit_hardware_models,
            user_can_manage_system,
        )

        # 1. sys_admin: 可管理系統設定、軟體產品主檔、硬體規格主檔；不可改客戶與開授權
        self.assertTrue(user_can_view_crm(self.sys_admin))
        self.assertTrue(user_can_manage_system(self.sys_admin))
        self.assertTrue(user_can_edit_products(self.sys_admin))
        self.assertTrue(user_can_edit_hardware_models(self.sys_admin))
        self.assertFalse(user_can_edit_customers(self.sys_admin))
        self.assertFalse(user_can_edit_licenses(self.sys_admin))
        self.assertFalse(user_can_edit_probe(self.sys_admin))

        # 2. customer_admin: 可編輯客戶與聯絡人；不可開授權、不可改產品、不可管硬體
        self.assertTrue(user_can_view_crm(self.customer_admin))
        self.assertTrue(user_can_edit_customers(self.customer_admin))
        self.assertFalse(user_can_edit_licenses(self.customer_admin))
        self.assertFalse(user_can_edit_products(self.customer_admin))
        self.assertFalse(user_can_edit_probe(self.customer_admin))

        # 3. license_admin: 可開立授權與維護合約；不可改客戶基本資料、不可改產品主檔
        self.assertTrue(user_can_view_crm(self.license_admin))
        self.assertTrue(user_can_edit_licenses(self.license_admin))
        self.assertFalse(user_can_edit_customers(self.license_admin))
        self.assertFalse(user_can_edit_products(self.license_admin))

        # 4. probe_admin: 可管理硬體設備與硬體規格主檔；不可改客戶與授權
        self.assertTrue(user_can_view_crm(self.probe_admin))
        self.assertTrue(user_can_edit_probe(self.probe_admin))
        self.assertTrue(user_can_edit_hardware_models(self.probe_admin))
        self.assertFalse(user_can_edit_customers(self.probe_admin))
        self.assertFalse(user_can_edit_licenses(self.probe_admin))

        # 5. view: 可瀏覽 CRM 與硬體，無編輯權限
        self.assertTrue(user_can_view_crm(self.view_user))
        self.assertFalse(user_can_edit_customers(self.view_user))
        self.assertFalse(user_can_edit_licenses(self.view_user))
        self.assertFalse(user_can_edit_probe(self.view_user))

        # 6. hw_viewer: 僅可查看硬體設備，不可瀏覽 CRM
        self.assertFalse(user_can_view_crm(self.hw_viewer))
        self.assertFalse(user_can_edit_probe(self.hw_viewer))

    def test_hw_viewer_menu_and_url_restrictions(self):
        client = Client()
        client.force_login(self.hw_viewer)

        # 造訪首頁 dashboard 自動導向至 hardware-dashboard
        resp = client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("hardware-dashboard"))

        # 造訪客戶名冊、合約、授權皆被阻擋並導向 hardware-dashboard
        for url_name in ["customer-list", "contract-list", "license-list", "product-list"]:
            r = client.get(reverse(url_name))
            self.assertEqual(r.status_code, 302)
            self.assertEqual(r.url, reverse("hardware-dashboard"))

        # 造訪硬體儀表板與設備清單正常 (200)，且側邊欄不包含客戶名冊連結
        hw_resp = client.get(reverse("hardware-dashboard"))
        self.assertEqual(hw_resp.status_code, 200)
        self.assertNotContains(hw_resp, "CRM 客戶與維護合約")
        self.assertContains(hw_resp, "硬體設備與資產")

    def test_license_admin_vs_customer_admin_endpoints(self):
        client = Client()

        # license_admin 不能新增客戶，但可以新增授權書與維護合約
        client.force_login(self.license_admin)
        resp_cust = client.post(reverse("customer-create"), {"code": "NEW-1", "name": "新客戶"})
        self.assertEqual(resp_cust.status_code, 302)
        self.assertEqual(resp_cust.url, reverse("customer-list"))

        resp_prod = client.post(reverse("product-create"), {"code": "P2", "name": "Prod2", "default_unit": "套"})
        self.assertEqual(resp_prod.status_code, 403)

        resp_lic = client.get(reverse("license-create"))
        self.assertEqual(resp_lic.status_code, 200)

        # sys_admin 可以新增軟體產品主檔與硬體規格主檔
        client.force_login(self.sys_admin)
        resp_prod_ok = client.get(reverse("product-create"))
        self.assertEqual(resp_prod_ok.status_code, 200)
        resp_hw_model_ok = client.get(reverse("hardware-model-create"))
        self.assertEqual(resp_hw_model_ok.status_code, 200)

    def test_customer_scope_followed_isolation_and_engineer_all_access(self):
        from apps.customers.models import Customer, CustomerFollow

        client = Client()

        # 1. 業務 sales_1 (僅限關注客戶，已關注 cust_a)：
        client.force_login(self.sales_1)
        resp_list = client.get(reverse("customer-list"))
        self.assertEqual(resp_list.status_code, 200)
        self.assertContains(resp_list, "甲科技股份有限公司")
        self.assertNotContains(resp_list, "乙系統有限公司")

        # 嘗試直接輸入網址查看未關注的 cust_b -> 被攔截導回 customer-list
        resp_detail_b = client.get(reverse("customer-detail", args=[self.cust_b.pk]))
        self.assertEqual(resp_detail_b.status_code, 302)
        self.assertEqual(resp_detail_b.url, reverse("customer-list"))

        # 合約清單與授權清單也只會看到 cust_a 的資料
        resp_contracts = client.get(reverse("contract-list"))
        self.assertContains(resp_contracts, "MC-A-001")
        self.assertNotContains(resp_contracts, "MC-B-001")

        resp_licenses = client.get(reverse("license-list"))
        self.assertContains(resp_licenses, "LIC-A-001")
        self.assertNotContains(resp_licenses, "LIC-B-001")

        # 業務 sales_1 新增客戶時，系統自動將其加入關注名單，新增後立即可見
        resp_create = client.post(
            reverse("customer-create"),
            {"code": "CUST-C", "name": "丙企業股份有限公司", "tax_id": "12345678"},
        )
        self.assertEqual(resp_create.status_code, 302)
        cust_c = Customer.objects.get(code="CUST-C")
        self.assertTrue(CustomerFollow.objects.filter(customer=cust_c, user=self.sales_1).exists())

        # 業務 sales_1 嘗試自行切換關注狀態 -> 應被拒絕 (403 / redirect)
        resp_toggle = client.post(
            reverse("customer-follow-toggle", args=[self.cust_b.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp_toggle.status_code, 403)

        # 2. 工程師 view_user (預設 customer_scope='all')：可查看所有客戶 (含未關注的 cust_a 與 cust_b) 並留言支援
        client.force_login(self.view_user)
        resp_eng_list = client.get(reverse("customer-list"))
        self.assertContains(resp_eng_list, "甲科技股份有限公司")
        self.assertContains(resp_eng_list, "乙系統有限公司")

        resp_eng_detail_b = client.get(reverse("customer-detail", args=[self.cust_b.pk]))
        self.assertEqual(resp_eng_detail_b.status_code, 200)
        self.assertContains(resp_eng_detail_b, "乙系統有限公司")

    def test_custom_role_and_template_fine_tuning(self):
        Role.ensure_system_roles()
        # 1. 測試 match_role_from_flags
        std_flags = UserProfile.get_role_default_flags(UserProfile.ROLE_CUSTOMER_ADMIN)
        self.assertEqual(UserProfile.match_role_from_flags(std_flags), UserProfile.ROLE_CUSTOMER_ADMIN)

        # 2. 測試表單選擇角色
        from apps.core.forms import LocalUserEditForm
        client = Client()
        client.force_login(self.sys_admin)

        role_cust = Role.objects.get(code="customer_admin")
        form = LocalUserEditForm(
            data={
                "first_name": "Test",
                "last_name": "User",
                "email": "test@example.com",
                "role_obj": role_cust.pk,
                "is_active": True,
            },
            instance=self.view_user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved_user = form.save()
        saved_profile = saved_user.profile
        self.assertEqual(saved_profile.role_obj, role_cust)
        self.assertTrue(saved_profile.has_customer_edit_access)
        self.assertFalse(saved_profile.has_probe_edit_access)
        self.assertIn("客戶資料", saved_profile.active_permission_labels)

        # 3. 測試帳號列表頁面呈現角色
        resp_list = client.get(reverse("account-list"))
        self.assertEqual(resp_list.status_code, 200)
        self.assertContains(resp_list, "客戶資料管理員")


class RoleManagementAndPureRoleDrivenTest(TestCase):
    def setUp(self):
        Role.ensure_system_roles()
        self.sys_admin = User.objects.create_superuser(
            username="sys_admin_role", password="password123", email="admin@test.com"
        )
        self.sys_profile = UserProfile.objects.create(
            user=self.sys_admin,
            role_obj=Role.objects.get(code="sys_admin"),
            role="sys_admin",
            can_manage_system=True,
        )
        self.client = Client()
        self.client.force_login(self.sys_admin)

    def test_role_creation_and_system_protection(self):
        # 1. 建立自訂角色
        resp = self.client.post(
            reverse("role-create"),
            {
                "name": "北區業務技術專員",
                "code": "north_sales_tech",
                "description": "負責北區客戶維護與借測",
                "customer_scope": "followed",
                "can_view_crm": True,
                "can_edit_customers": True,
                "can_edit_licenses": False,
                "can_edit_products": False,
                "can_edit_probes": True,
                "can_manage_system": False,
            },
        )
        self.assertEqual(resp.status_code, 302)
        role = Role.objects.get(code="north_sales_tech")
        self.assertEqual(role.name, "北區業務技術專員")
        self.assertEqual(role.customer_scope, "followed")
        self.assertTrue(role.can_edit_customers)
        self.assertTrue(role.can_edit_probes)
        self.assertFalse(role.is_system)

        # 2. 測試系統角色防刪除保護
        sys_role = Role.objects.get(code="all_admin")
        del_resp = self.client.get(reverse("role-delete", args=[sys_role.pk]))
        self.assertEqual(del_resp.status_code, 302)
        self.assertTrue(Role.objects.filter(code="all_admin").exists())

    def test_pure_role_driven_real_time_sync(self):
        # 1. 建立新角色
        custom_role = Role.objects.create(
            name="軟體出貨助理",
            code="soft_shipping",
            can_view_crm=True,
            can_edit_customers=False,
            can_edit_licenses=True,
            can_edit_products=False,
            can_edit_probes=False,
            can_manage_system=False,
            customer_scope="all",
        )

        # 2. 建立套用此角色的帳號
        test_u = User.objects.create_user(username="test_assistant", password="password123")
        test_p = UserProfile.objects.create(user=test_u, role_obj=custom_role, role=custom_role.code)

        # 驗證初使權限
        self.assertTrue(test_p.has_license_edit_access)
        self.assertFalse(test_p.has_probe_edit_access)

        # 3. 管理者在角色管理頁面將「硬體庫存」權限勾選加入該角色
        resp_update = self.client.post(
            reverse("role-update", args=[custom_role.pk]),
            {
                "name": "軟體與硬體出貨助理",
                "code": "soft_shipping",
                "description": "兼任硬體出貨",
                "customer_scope": "all",
                "can_view_crm": True,
                "can_edit_customers": False,
                "can_edit_licenses": True,
                "can_edit_products": False,
                "can_edit_probes": True,  # 新增勾選
                "can_manage_system": False,
            },
        )
        self.assertEqual(resp_update.status_code, 302)

        # 4. 驗證帳號端即時自動同步（無須重新編輯 test_u 帳號）
        test_p.refresh_from_db()
        self.assertEqual(test_p.role_obj.name, "軟體與硬體出貨助理")
        self.assertTrue(test_p.has_license_edit_access)
        self.assertTrue(test_p.has_probe_edit_access)

        # 5. 測試使用中角色防刪除保護
        resp_del_in_use = self.client.get(reverse("role-delete", args=[custom_role.pk]))
        self.assertEqual(resp_del_in_use.status_code, 302)
        self.assertTrue(Role.objects.filter(code="soft_shipping").exists())

    def test_account_and_role_tabs_navigation(self):
        # 測試 Tab 切換顯示
        resp_acc = self.client.get(reverse("account-list") + "?tab=accounts")
        self.assertEqual(resp_acc.status_code, 200)
        self.assertContains(resp_acc, "帳號列表")

        resp_role = self.client.get(reverse("account-list") + "?tab=roles")
        self.assertEqual(resp_role.status_code, 200)
        self.assertContains(resp_role, "系統角色主檔清單")
        self.assertContains(resp_role, "全域超級管理員")

    def test_auto_migrate_detector_and_backup_engine_detection(self):
        from apps.core.auto_migrate import ensure_db_migrated, get_migrations_disk_signature
        from apps.core.backup_service import get_current_db_engine
        from unittest.mock import patch

        sig = get_migrations_disk_signature()
        self.assertIsInstance(sig, tuple)
        self.assertGreater(len(sig), 0)

        # Force check when all migrations are already applied -> returns False
        applied = ensure_db_migrated(force_check=True)
        self.assertFalse(applied)

        # Repeated check with same signature -> returns False without DB hit
        applied_cached = ensure_db_migrated(force_check=False)
        self.assertFalse(applied_cached)

        # Test get_current_db_engine supports sqlite, mssql, postgres
        with patch.dict("django.conf.settings.DATABASES", {"default": {"ENGINE": "django.db.backends.postgresql"}}):
            self.assertEqual(get_current_db_engine(), "postgres")
        with patch.dict("django.conf.settings.DATABASES", {"default": {"ENGINE": "mssql"}}):
            self.assertEqual(get_current_db_engine(), "mssql")
        with patch.dict("django.conf.settings.DATABASES", {"default": {"ENGINE": "django.db.backends.sqlite3"}}):
            self.assertEqual(get_current_db_engine(), "sqlite")

    def test_audit_log_retention_and_auto_cleanup(self):
        import datetime
        from django.utils import timezone
        from apps.core.models import SystemSetting
        from apps.core.permissions import log_action, cleanup_expired_audit_logs

        now = timezone.now()
        old_log = AuditLog.objects.create(user=self.sys_admin, action="舊操作紀錄")
        AuditLog.objects.filter(pk=old_log.pk).update(created_at=now - datetime.timedelta(days=40))

        recent_log = AuditLog.objects.create(user=self.sys_admin, action="近期操作紀錄")
        AuditLog.objects.filter(pk=recent_log.pk).update(created_at=now - datetime.timedelta(days=5))

        # 1. 預設 retention_days = 0 (永久保留)，不應刪除任何日誌
        deleted = cleanup_expired_audit_logs()
        self.assertEqual(deleted, 0)
        self.assertTrue(AuditLog.objects.filter(pk=old_log.pk).exists())

        # 2. 透過 /audit-logs/ POST 設定保留 30 天 -> 超過 30 天的 old_log 立即自動刪除，recent_log 保留
        resp = self.client.post(
            reverse("audit-log-list"),
            {"action": "update_retention", "retention_days": "30"},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "保留最近 30 天")
        self.assertEqual(SystemSetting.get_settings().audit_log_retention_days, 30)
        self.assertFalse(AuditLog.objects.filter(pk=old_log.pk).exists())
        self.assertTrue(AuditLog.objects.filter(pk=recent_log.pk).exists())

        # 3. 測試自訂天數 (custom_days = 3) 且當有新 log_action 觸發時自動刪除過期日誌
        resp_custom = self.client.post(
            reverse("audit-log-list"),
            {"action": "update_retention", "retention_days": "custom", "custom_days": "3"},
            follow=True,
        )
        self.assertEqual(resp_custom.status_code, 200)
        self.assertEqual(SystemSetting.get_settings().audit_log_retention_days, 3)
        self.assertFalse(AuditLog.objects.filter(pk=recent_log.pk).exists())

        # 新增一筆 10 天前的日誌，再呼叫 log_action 時應被自動清理
        expired_log_2 = AuditLog.objects.create(user=self.sys_admin, action="過期測試2")
        AuditLog.objects.filter(pk=expired_log_2.pk).update(created_at=now - datetime.timedelta(days=10))
        log_action(user=self.sys_admin, action="觸發自動清理的新動作")
        self.assertFalse(AuditLog.objects.filter(pk=expired_log_2.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="觸發自動清理的新動作").exists())


class IdleAutoLogoutTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin", password="password123", email="admin@test.com"
        )
        self.client = Client()
        self.client.force_login(self.admin)
        SystemSetting.objects.all().delete()
        self.setting = SystemSetting.get_settings()

    def test_system_setting_default_and_update(self):
        self.assertEqual(self.setting.idle_timeout_minutes, 10)
        self.setting.idle_timeout_minutes = 15
        self.setting.save()
        updated = SystemSetting.get_settings()
        self.assertEqual(updated.idle_timeout_minutes, 15)

    def test_idle_timeout_form_validation(self):
        from apps.core.forms import IdleTimeoutSettingForm

        # Valid cases
        form = IdleTimeoutSettingForm(data={"idle_timeout_minutes": 10})
        self.assertTrue(form.is_valid())

        form = IdleTimeoutSettingForm(data={"idle_timeout_minutes": 1})
        self.assertTrue(form.is_valid())

        form = IdleTimeoutSettingForm(data={"idle_timeout_minutes": 1440})
        self.assertTrue(form.is_valid())

        # Invalid: 0 (not allowed)
        form = IdleTimeoutSettingForm(data={"idle_timeout_minutes": 0})
        self.assertFalse(form.is_valid())

        # Invalid: negative
        form = IdleTimeoutSettingForm(data={"idle_timeout_minutes": -5})
        self.assertFalse(form.is_valid())

        # Invalid: > 1440
        form = IdleTimeoutSettingForm(data={"idle_timeout_minutes": 1500})
        self.assertFalse(form.is_valid())

    def test_account_list_timeout_tab(self):
        # GET ?tab=timeout
        resp = self.client.get(reverse("account-list") + "?tab=timeout")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "閒置登出時間設定")
        self.assertContains(resp, "系統閒置自動登出設定")

        # POST save_idle_timeout
        resp = self.client.post(
            reverse("account-list") + "?tab=timeout",
            {"action": "save_idle_timeout", "idle_timeout_minutes": "20"},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SystemSetting.get_settings().idle_timeout_minutes, 20)
        self.assertContains(resp, "閒置自動登出時間已成功更新為 20 分鐘")

        # Check AuditLog
        log = AuditLog.objects.filter(action="更新閒置登出時間設定").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.target_identifier, "20 分鐘")

    def test_session_idle_timeout_middleware(self):
        import time
        from django.test import RequestFactory
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.contrib.auth.middleware import AuthenticationMiddleware
        from apps.core.middleware import SessionIdleTimeoutMiddleware

        rf = RequestFactory()

        # 1. Normal active request: sets last_activity
        request = rf.get("/dashboard/")
        request.user = self.admin
        
        # Add session
        session_middleware = SessionMiddleware(lambda r: None)
        session_middleware.process_request(request)
        request.session.save()

        from django.http import HttpResponse
        middleware = SessionIdleTimeoutMiddleware(lambda r: HttpResponse("OK"))
        middleware(request)

        self.assertIn("last_activity", request.session)
        initial_activity = request.session["last_activity"]

        # 2. Simulated inactive request within 10 minutes (e.g. 5 minutes later)
        request.session["last_activity"] = time.time() - 300  # 5 min elapsed
        middleware(request)
        self.assertTrue(request.user.is_authenticated)

        # 3. Simulated inactive request beyond 10 minutes (e.g. 11 minutes later)
        request.session["last_activity"] = time.time() - 660  # 11 min elapsed
        response = middleware(request)

        # Should redirect to login and logout user
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))
        self.assertFalse(request.user.is_authenticated)

        # AuditLog should have recorded standard "使用者登出"
        logout_log = AuditLog.objects.filter(action="使用者登出", user=self.admin).first()
        self.assertIsNotNone(logout_log)

    def test_logout_view_auto_param(self):
        # Test normal logout produces flash message
        resp = self.client.get(reverse("logout"), follow=True)
        self.assertEqual(resp.status_code, 200)
        messages = list(resp.context.get("messages", []))
        self.assertTrue(any("您已成功登出系統" in str(m) for m in messages))

        # Re-login
        self.client.force_login(self.admin)

        # Test auto logout does NOT produce flash message
        resp = self.client.get(reverse("logout") + "?auto=1", follow=True)
        self.assertEqual(resp.status_code, 200)
        messages = list(resp.context.get("messages", []))
        self.assertFalse(any("您已成功登出系統" in str(m) for m in messages))







