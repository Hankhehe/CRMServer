from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Role, SystemSetting, UserProfile
from apps.customers.models import Customer, MaintenanceContract
from apps.hardware.models import HardwareDevice, HardwareModel, HardwareOutboundApproval, InventoryTransaction
from apps.licenses.models import CustomerLicense, Product

User = get_user_model()


class ApprovalWorkflowTests(TestCase):
    def setUp(self):
        Role.ensure_system_roles()
        self.sys = SystemSetting.get_settings()

        # 建立客戶、產品、硬體規格
        self.customer = Customer.objects.create(code="CUST-APP-01", name="測試簽核企業股份有限公司")
        self.product = Product.objects.create(code="PROD-01", name="核心流量分析系統", default_unit="台")
        self.hw_model = HardwareModel.objects.create(model_code="M-100", name="Probe-X100", port_count=4)

        # 建立一般開單人員 (無覆核權限)
        self.lic_staff = User.objects.create_user(username="lic_staff", password="pw", email="lic_staff@test.local")
        lic_role = Role.objects.get(code="license_admin")
        UserProfile.objects.create(
            user=self.lic_staff,
            role_obj=lic_role,
            role="license_admin",
            can_view_crm=True,
            can_edit_licenses=True,
            can_approve_licenses=False,
            can_approve_contracts=False,
        )

        self.hw_staff = User.objects.create_user(username="hw_staff", password="pw", email="hw_staff@test.local")
        hw_role = Role.objects.get(code="probe_admin")
        UserProfile.objects.create(
            user=self.hw_staff,
            role_obj=hw_role,
            role="probe_admin",
            can_view_crm=True,
            can_edit_probes=True,
            can_approve_hardware=False,
        )

        # 建立覆核主管帳號 (分別擁有三種覆核權限)
        self.approver = User.objects.create_user(username="approver_mgr", password="pw", email="mgr@test.local")
        UserProfile.objects.create(
            user=self.approver,
            role_obj=lic_role,
            role="custom",
            can_view_crm=True,
            can_edit_licenses=True,
            can_edit_probes=True,
            can_approve_licenses=True,
            can_approve_contracts=True,
            can_approve_hardware=True,
        )

    def test_independent_switches_default_off_direct_approve(self):
        """預設簽核開關皆關閉時，無覆核權限人員開單應直接核准生效且可匯出證書"""
        self.assertFalse(self.sys.require_license_approval)
        self.assertFalse(self.sys.require_warranty_approval)
        self.assertFalse(self.sys.require_hardware_approval)

        self.client.login(username="lic_staff", password="pw")
        resp = self.client.post(
            reverse("license-create"),
            {
                "customer": self.customer.pk,
                "product": self.product.pk,
                "license_no": "LIC-OFF-001",
                "quantity": 10,
                "unit": "台",
                "is_perpetual": "on",
                "note": "開關關閉測試",
            },
        )
        self.assertEqual(resp.status_code, 302)
        lic = CustomerLicense.objects.get(license_no="LIC-OFF-001")
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_APPROVED)
        self.assertEqual(self.product.active_license_count, 1)

        cert_resp = self.client.get(reverse("cert-data-api", kwargs={"cert_type": "license", "pk": lic.pk}))
        self.assertEqual(cert_resp.status_code, 200)

    def test_license_approval_flow_filter_cert_block_and_note_edit(self):
        """測試軟體授權書：開關開啟 -> 待覆核禁載證書 -> 退回重審 -> 批次核准 -> 改備註免重審 / 改數量重進待覆核"""
        self.sys.require_license_approval = True
        self.sys.save()

        # 1. 開單人員開立授權 -> 進入 pending
        self.client.login(username="lic_staff", password="pw")
        self.client.post(
            reverse("license-create"),
            {
                "customer": self.customer.pk,
                "product": self.product.pk,
                "license_no": "LIC-APP-001",
                "quantity": 5,
                "unit": "台",
                "is_perpetual": "on",
                "note": "初版",
            },
        )
        lic = CustomerLicense.objects.get(license_no="LIC-APP-001")
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_PENDING)
        self.assertEqual(self.product.active_license_count, 0)

        # 未覆核禁止匯出證書
        cert_resp = self.client.get(reverse("cert-data-api", kwargs={"cert_type": "license", "pk": lic.pk}))
        self.assertEqual(cert_resp.status_code, 403)

        # 授權清單篩選 approval=pending
        list_resp = self.client.get(reverse("license-list") + "?approval=pending")
        self.assertContains(list_resp, "LIC-APP-001")

        # 2. 主管退回 (未填原因應阻擋，填原因轉為 rejected)
        self.client.login(username="approver_mgr", password="pw")
        self.client.post(reverse("license-reject", kwargs={"pk": lic.pk}), {"rejection_reason": ""})
        lic.refresh_from_db()
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_PENDING)

        self.client.post(reverse("license-reject", kwargs={"pk": lic.pk}), {"rejection_reason": "數量誤植請修正為 20"})
        lic.refresh_from_db()
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_REJECTED)
        self.assertEqual(lic.rejection_reason, "數量誤植請修正為 20")

        # 3. 開單人修改重送 -> 自動變回 pending
        self.client.login(username="lic_staff", password="pw")
        self.client.post(
            reverse("license-update", kwargs={"pk": lic.pk}),
            {
                "customer": self.customer.pk,
                "product": self.product.pk,
                "license_no": "LIC-APP-001",
                "quantity": 20,
                "unit": "台",
                "is_perpetual": "on",
                "note": "已修正為 20 台",
            },
        )
        lic.refresh_from_db()
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_PENDING)

        # 4. 主管批次核准 -> approved，可下載證書，計入統計
        self.client.login(username="approver_mgr", password="pw")
        self.client.post(reverse("license-batch-approve"), {"selected_ids": [str(lic.pk)]})
        lic.refresh_from_db()
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_APPROVED)
        self.assertEqual(self.product.active_license_count, 1)
        cert_ok = self.client.get(reverse("cert-data-api", kwargs={"cert_type": "license", "pk": lic.pk}))
        self.assertEqual(cert_ok.status_code, 200)

        # 5. 開單人僅修改「備註」 -> 維持 approved
        self.client.login(username="lic_staff", password="pw")
        self.client.post(
            reverse("license-update", kwargs={"pk": lic.pk}),
            {
                "customer": self.customer.pk,
                "product": self.product.pk,
                "license_no": "LIC-APP-001",
                "quantity": 20,
                "unit": "台",
                "is_perpetual": "on",
                "note": "補充聯絡備註不改規格",
            },
        )
        lic.refresh_from_db()
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_APPROVED)

        # 6. 開單人修改「數量」(核心欄位) -> 自動變回 pending
        self.client.post(
            reverse("license-update", kwargs={"pk": lic.pk}),
            {
                "customer": self.customer.pk,
                "product": self.product.pk,
                "license_no": "LIC-APP-001",
                "quantity": 50,
                "unit": "台",
                "is_perpetual": "on",
                "note": "擴充數量",
            },
        )
        lic.refresh_from_db()
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_PENDING)

    def test_approver_self_create_auto_approved(self):
        """具備覆核權限之主管自己開單時，即使簽核開關開啟也應自動覆核通過"""
        self.sys.require_license_approval = True
        self.sys.save()

        self.client.login(username="approver_mgr", password="pw")
        self.client.post(
            reverse("license-create"),
            {
                "customer": self.customer.pk,
                "product": self.product.pk,
                "license_no": "LIC-MGR-001",
                "quantity": 8,
                "unit": "台",
                "is_perpetual": "on",
            },
        )
        lic = CustomerLicense.objects.get(license_no="LIC-MGR-001")
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_APPROVED)

    def test_warranty_contract_approval_and_cert_block(self):
        """測試維護保固合約：開關獨立控制、待覆核禁匯出保固證書、核准後生效"""
        self.sys.require_warranty_approval = True
        self.sys.save()

        today = timezone.localdate()
        end_d = today + timedelta(days=365)

        self.client.login(username="lic_staff", password="pw")
        self.client.post(
            reverse("contract-create"),
            {
                "customer": self.customer.pk,
                "contract_no": "MC-APP-001",
                "name": "年度保固合約",
                "start_date": today.strftime("%Y-%m-%d"),
                "end_date": end_d.strftime("%Y-%m-%d"),
                "sla_level": "8x5",
                "note": "初始備註",
            },
        )
        ct = MaintenanceContract.objects.get(contract_no="MC-APP-001")
        self.assertEqual(ct.approval_status, MaintenanceContract.APPROVAL_PENDING)
        self.assertIsNone(self.customer.maintenance_expiry)

        cert_resp = self.client.get(reverse("cert-data-api", kwargs={"cert_type": "warranty", "pk": ct.pk}))
        self.assertEqual(cert_resp.status_code, 403)

        # 主管核准
        self.client.login(username="approver_mgr", password="pw")
        self.client.post(reverse("contract-approve", kwargs={"pk": ct.pk}))
        ct.refresh_from_db()
        self.assertEqual(ct.approval_status, MaintenanceContract.APPROVAL_APPROVED)
        self.assertEqual(self.customer.maintenance_expiry, end_d)

        cert_ok = self.client.get(reverse("cert-data-api", kwargs={"cert_type": "warranty", "pk": ct.pk}))
        self.assertEqual(cert_ok.status_code, 200)

    def test_hardware_outbound_prelock_reject_unlock_and_approve_rma(self):
        """測試硬體設備出庫預鎖防呆：正式出貨/借測待審鎖定、退回自動釋放回庫存、核准正式出庫與 RMA 換機簽核"""
        self.sys.require_hardware_approval = True
        self.sys.save()

        dev1 = HardwareDevice.objects.create(code="SN-HW-001", model=self.hw_model, status=HardwareDevice.STATUS_AVAILABLE)
        dev2 = HardwareDevice.objects.create(code="SN-HW-002", model=self.hw_model, status=HardwareDevice.STATUS_AVAILABLE)

        # 1. 庫存人員執行正式出貨 -> 預鎖為 pending_ship，尚未產生 OUT 交易紀錄
        self.client.login(username="hw_staff", password="pw")
        self.client.post(
            reverse("hardware-transaction-create"),
            {
                "type": InventoryTransaction.TYPE_OUT,
                "customer": self.customer.pk,
                "device": dev1.pk,
                "note": "出貨申請測試",
            },
        )
        dev1.refresh_from_db()
        self.assertEqual(dev1.status, HardwareDevice.STATUS_PENDING_SHIP)
        self.assertEqual(dev1.transactions.filter(type=InventoryTransaction.TYPE_OUT).count(), 0)

        # 2. 主管退回 -> 自動解鎖釋放回 available
        self.client.login(username="approver_mgr", password="pw")
        self.client.post(reverse("hardware-reject", kwargs={"pk": dev1.pk}), {"rejection_reason": "客戶暫緩收貨"})
        dev1.refresh_from_db()
        self.assertEqual(dev1.status, HardwareDevice.STATUS_AVAILABLE)
        self.assertIsNone(dev1.customer)

        # 3. 重新發起客戶借測申請 -> pending_loan -> 主管核准 -> 正式 loaned 並寫入 LOAN 交易
        return_date = timezone.localdate() + timedelta(days=14)
        self.client.login(username="hw_staff", password="pw")
        self.client.post(
            reverse("hardware-transaction-create"),
            {
                "type": InventoryTransaction.TYPE_LOAN,
                "customer": self.customer.pk,
                "device": dev1.pk,
                "expected_return_at": return_date.strftime("%Y-%m-%d"),
                "note": "借測兩週",
            },
        )
        dev1.refresh_from_db()
        self.assertEqual(dev1.status, HardwareDevice.STATUS_PENDING_LOAN)

        self.client.login(username="approver_mgr", password="pw")
        self.client.post(reverse("hardware-approve", kwargs={"pk": dev1.pk}))
        dev1.refresh_from_db()
        self.assertEqual(dev1.status, HardwareDevice.STATUS_LOANED)
        self.assertEqual(dev1.customer, self.customer)
        self.assertEqual(dev1.transactions.filter(type=InventoryTransaction.TYPE_LOAN).count(), 1)

        # 4. 維護合約 RMA 故障換機簽核 (dev1 換成新備品 dev2)
        ct = MaintenanceContract.objects.create(
            customer=self.customer,
            contract_no="MC-RMA-01",
            end_date=timezone.localdate() + timedelta(days=180),
            approval_status=MaintenanceContract.APPROVAL_APPROVED,
        )
        ct.probes.add(dev1)

        self.client.login(username="lic_staff", password="pw")
        self.client.post(
            reverse("contract-swap-probe", kwargs={"pk": ct.pk}),
            {
                "action_type": "replace",
                "old_probe": dev1.pk,
                "new_probe": dev2.pk,
                "reason": "Port 1 故障換機",
            },
        )
        dev1.refresh_from_db()
        dev2.refresh_from_db()
        # 尚未核准前：新備品 dev2 鎖為 pending_ship，舊機 dev1 仍維持原狀態
        self.assertEqual(dev2.status, HardwareDevice.STATUS_PENDING_SHIP)
        self.assertEqual(dev1.status, HardwareDevice.STATUS_LOANED)

        # 主管核准 RMA 出庫 -> 舊機 dev1 退回 available，新機 dev2 變 shipped 並掛入合約
        self.client.login(username="approver_mgr", password="pw")
        self.client.post(reverse("hardware-approve", kwargs={"pk": dev2.pk}))
        dev1.refresh_from_db()
        dev2.refresh_from_db()
        self.assertEqual(dev1.status, HardwareDevice.STATUS_AVAILABLE)
        self.assertEqual(dev2.status, HardwareDevice.STATUS_SHIPPED)
        self.assertIn(dev2, ct.probes.all())
        self.assertNotIn(dev1, ct.probes.all())
