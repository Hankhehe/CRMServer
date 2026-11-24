import datetime
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.customers.models import Customer, ContactPerson, MaintenanceContract

User = get_user_model()


class CustomerModelAndViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="testadmin", email="test@example.com", password="password123"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_customer_creation_and_unique_code(self):
        # 1. 建立第一個客戶
        c1 = Customer.objects.create(
            code="23456789",
            name="台積電測試",
            contact="張先生",
            phone="02-12345678",
            email="tsmc@test.com",
            address="新竹科學園區",
        )
        self.assertEqual(str(c1), "23456789 - 台積電測試")
        self.assertEqual(c1.code, "23456789")

        # 2. 測試重複代號 (唯一值限制)
        with self.assertRaises(Exception):
            Customer.objects.create(
                code="23456789",
                name="重複統編測試",
            )

    def test_contact_person_and_sync(self):
        cust = Customer.objects.create(code="CUST-99991", name="聯發科測試")
        contact = ContactPerson.objects.create(
            customer=cust,
            name="林經理",
            title="資訊處長",
            phone="0912345678",
            email="lin@test.com",
            is_primary=True,
        )
        cust.refresh_from_db()
        self.assertEqual(cust.contact, "林經理")
        self.assertEqual(cust.phone, "0912345678")
        self.assertEqual(cust.email, "lin@test.com")

    def test_maintenance_contract_status(self):
        cust = Customer.objects.create(code="CUST-99992", name="鴻海測試")
        today = datetime.date.today()
        # 建立有效合約
        ct = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-TEST-001",
            name="年度維護合約",
            start_date=today - datetime.timedelta(days=30),
            end_date=today + datetime.timedelta(days=20),
            status=MaintenanceContract.STATUS_ACTIVE,
            sla_level="24x7 4小時",
        )
        self.assertFalse(ct.is_expired)
        self.assertTrue(ct.is_expiring_soon)
        self.assertEqual(ct.days_remaining, 20)
        self.assertEqual(ct.status, MaintenanceContract.STATUS_ACTIVE)
        self.assertEqual(ct.dynamic_status, MaintenanceContract.STATUS_ACTIVE)
        self.assertEqual(ct.badge_class, "badge-available")
        self.assertEqual(cust.maintenance_expiry, ct.end_date)

        # 測試待生效合約 (起日為未來)
        ct_pending = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-TEST-002",
            name="待生效合約",
            start_date=today + datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=100),
        )
        self.assertEqual(ct_pending.status, MaintenanceContract.STATUS_PENDING)
        self.assertEqual(ct_pending.dynamic_status, MaintenanceContract.STATUS_PENDING)
        self.assertEqual(ct_pending.badge_class, "badge-secondary")
        self.assertIn("待生效", ct_pending.status_display)

        # 測試已到期合約 (訖日為過去)
        ct_expired = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-TEST-003",
            name="已到期合約",
            start_date=today - datetime.timedelta(days=100),
            end_date=today - datetime.timedelta(days=5),
        )
        self.assertEqual(ct_expired.status, MaintenanceContract.STATUS_EXPIRED)
        self.assertEqual(ct_expired.dynamic_status, MaintenanceContract.STATUS_EXPIRED)
        self.assertEqual(ct_expired.badge_class, "badge-scrapped")
        self.assertIn("已過期", ct_expired.status_display)

        # 測試終止合約 (is_terminated=True)
        ct_terminated = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-TEST-004",
            name="已終止合約",
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=50),
            is_terminated=True,
        )
        self.assertEqual(ct_terminated.status, MaintenanceContract.STATUS_TERMINATED)
        self.assertEqual(ct_terminated.dynamic_status, MaintenanceContract.STATUS_TERMINATED)
        self.assertEqual(ct_terminated.badge_class, "badge-secondary")
        self.assertEqual(ct_terminated.status_display, "已終止")

    def test_customer_list_view(self):
        Customer.objects.create(code="88889999", name="聯電測試")
        resp = self.client.get(reverse("customer-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "聯電測試")
        self.assertContains(resp, "88889999")

        # 測試搜尋
        search_resp = self.client.get(reverse("customer-list") + "?q=88889999")
        self.assertEqual(search_resp.status_code, 200)
        self.assertContains(search_resp, "聯電測試")

    def test_customer_activity_log_lifecycle(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.customers.models import CustomerActivityLog, ActivityAttachment

        cust = Customer.objects.create(code="CUST-ACT-01", name="活動測試客戶")
        contact = ContactPerson.objects.create(customer=cust, name="王組長", is_primary=True)

        # 1. 建立執行軌跡與附件
        test_file = SimpleUploadedFile("meeting_notes.txt", b"Test meeting notes content", content_type="text/plain")
        resp = self.client.post(
            reverse("activity-create", kwargs={"customer_pk": cust.pk}),
            {
                "activity_type": CustomerActivityLog.TYPE_MEETING,
                "contact": contact.pk,
                "follow_up_date": "2026-10-15",
                "content": "與王組長確認需求細節及交付排程",
                "files": [test_file],
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "已成功建立一筆客戶執行軌跡紀錄。")

        log = CustomerActivityLog.objects.filter(customer=cust).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.activity_type, CustomerActivityLog.TYPE_MEETING)
        self.assertEqual(log.contact, contact)
        self.assertEqual(log.content, "與王組長確認需求細節及交付排程")
        self.assertFalse(log.is_edited)
        self.assertEqual(log.badge_class, "badge-primary")
        self.assertEqual(log.attachments.count(), 1)

        att = log.attachments.first()
        self.assertEqual(att.filename, "meeting_notes.txt")
        self.assertIn("B", att.formatted_size)

        # 2. 測試客戶詳情頁渲染
        detail_resp = self.client.get(reverse("customer-detail", kwargs={"pk": cust.pk}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "客戶執行軌跡與討論歷程")
        self.assertContains(detail_resp, "與王組長確認需求細節及交付排程")
        self.assertContains(detail_resp, "meeting_notes.txt")

        # 3. 測試更新軌跡
        update_resp = self.client.post(
            reverse("activity-update", kwargs={"pk": log.pk}),
            {
                "activity_type": CustomerActivityLog.TYPE_CALL,
                "contact": contact.pk,
                "follow_up_date": "2026-10-20",
                "content": "更新內容：改以電話確認合約細節",
            },
            follow=True,
        )
        self.assertEqual(update_resp.status_code, 200)
        log.refresh_from_db()
        self.assertEqual(log.activity_type, CustomerActivityLog.TYPE_CALL)
        self.assertEqual(log.content, "更新內容：改以電話確認合約細節")
        self.assertTrue(log.is_edited)

        # 4. 測試刪除附件
        att_delete_resp = self.client.post(
            reverse("activity-attachment-delete", kwargs={"pk": att.pk}),
            follow=True,
        )
        self.assertEqual(att_delete_resp.status_code, 200)
        self.assertEqual(log.attachments.count(), 0)

        # 5. 測試刪除軌跡
        del_resp = self.client.post(
            reverse("activity-delete", kwargs={"pk": log.pk}),
            follow=True,
        )
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(CustomerActivityLog.objects.filter(customer=cust).count(), 0)

    def test_contract_multi_licenses_and_probes(self):
        from apps.licenses.models import Product, CustomerLicense
        from apps.hardware.models import HardwareDevice as Probe, HardwareModel as ProbeModelDetail, Supplier
        from apps.customers.forms import MaintenanceContractForm

        cust = Customer.objects.create(code="CUST-CTR-01", name="合約標的測試客戶")
        other_cust = Customer.objects.create(code="CUST-CTR-02", name="其他客戶")

        prod1 = Product.objects.create(code="P-01", name="網路分析系統")
        prod2 = Product.objects.create(code="P-02", name="封包過濾系統")

        lic1 = CustomerLicense.objects.create(
            customer=cust, product=prod1, license_no="LIC-001", quantity=2
        )
        lic2 = CustomerLicense.objects.create(
            customer=cust, product=prod2, license_no="LIC-002", quantity=1
        )
        lic_other = CustomerLicense.objects.create(
            customer=other_cust, product=prod1, license_no="LIC-003", quantity=1
        )

        supplier = Supplier.objects.create(name="測試供應商")
        model = ProbeModelDetail.objects.create(name="P-Model-X", port_count=4, supplier=supplier)
        probe1 = Probe.objects.create(
            code="PB-001", model=model, customer=cust, status=Probe.STATUS_SHIPPED
        )
        probe2 = Probe.objects.create(
            code="PB-002", model=model, customer=cust, status=Probe.STATUS_LOANED
        )
        probe_other = Probe.objects.create(
            code="PB-003", model=model, customer=other_cust, status=Probe.STATUS_SHIPPED
        )

        # 1. 建立合約並關聯多個授權與設備
        contract = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-MULTI-001",
            name="維護合約多項標的",
            end_date=datetime.date.today() + datetime.timedelta(days=100),
            status=MaintenanceContract.STATUS_ACTIVE,
        )
        contract.licenses.set([lic1, lic2])
        contract.probes.set([probe1, probe2])

        self.assertEqual(contract.licenses.count(), 2)
        self.assertEqual(contract.probes.count(), 2)
        self.assertIn(lic1, contract.licenses.all())
        self.assertIn(probe1, contract.probes.all())

        # 2. 測試表單跨客戶驗證
        invalid_form_data = {
            "customer": cust.pk,
            "contract_no": "MC-ERR-001",
            "name": "跨客戶錯誤測試",
            "end_date": "2026-12-31",
            "status": "active",
            "licenses": [lic1.pk, lic_other.pk],
            "probes": [probe1.pk, probe_other.pk],
        }
        form = MaintenanceContractForm(data=invalid_form_data, customer=cust)
        self.assertFalse(form.is_valid())
        self.assertIn("licenses", form.errors)
        self.assertIn("probes", form.errors)

        # 3. 測試 Candidate API
        api_resp = self.client.get(reverse("api-customer-contract-candidates", kwargs={"customer_id": cust.pk}))
        self.assertEqual(api_resp.status_code, 200)
        api_data = api_resp.json()
        self.assertEqual(len(api_data["licenses"]), 2)
        self.assertEqual(len(api_data["probes"]), 2)
        lic_nos = [l["license_no"] for l in api_data["licenses"]]
        self.assertIn("LIC-001", lic_nos)
        self.assertIn("LIC-002", lic_nos)

    def test_contract_probe_rma_swap_and_return(self):
        from apps.licenses.models import Product, CustomerLicense
        from apps.hardware.models import HardwareDevice as Probe, HardwareModel as ProbeModelDetail, Supplier, InventoryTransaction
        from apps.customers.models import ContractProbeReplacement, CustomerActivityLog

        cust = Customer.objects.create(code="CUST-RMA-01", name="RMA測試客戶")
        prod = Product.objects.create(code="P-FLOW", name="Flow Inspector")
        lic = CustomerLicense.objects.create(customer=cust, product=prod, license_no="LIC-RMA-01", quantity=1)

        supplier = Supplier.objects.create(name="RMA供應商")
        model = ProbeModelDetail.objects.create(name="Model-RMA", port_count=4, supplier=supplier)

        # 故障舊機 (已出貨給客戶)
        probe_old = Probe.objects.create(
            code="PRB-OLD-01", model=model, customer=cust, status=Probe.STATUS_SHIPPED
        )
        # 倉庫新備品 (庫存中)
        probe_new = Probe.objects.create(
            code="PRB-NEW-01", model=model, customer=None, status=Probe.STATUS_AVAILABLE
        )

        contract = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-RMA-001",
            name="RMA更換合約",
            end_date=datetime.date.today() + datetime.timedelta(days=180),
        )
        contract.probes.add(probe_old)
        contract.licenses.add(lic)

        # 1. 執行「故障換機 (replace)」
        swap_resp = self.client.post(
            reverse("contract-swap-probe", kwargs={"pk": contract.pk}),
            {
                "action_type": ContractProbeReplacement.ACTION_REPLACE,
                "old_probe": probe_old.pk,
                "new_probe": probe_new.pk,
                "reason": "網卡連接埠受損，換上同型備品",
            },
            follow=True,
        )
        self.assertEqual(swap_resp.status_code, 200)

        # 驗證舊設備狀態
        probe_old.refresh_from_db()
        self.assertEqual(probe_old.status, Probe.STATUS_AVAILABLE)
        self.assertIsNone(probe_old.customer)

        # 驗證新設備狀態
        probe_new.refresh_from_db()
        self.assertEqual(probe_new.status, Probe.STATUS_SHIPPED)
        self.assertEqual(probe_new.customer, cust)

        # 驗證合約內容更新
        contract.refresh_from_db()
        self.assertNotIn(probe_old, contract.probes.all())
        self.assertIn(probe_new, contract.probes.all())

        # 驗證退換歷程模型
        replacement_log = ContractProbeReplacement.objects.filter(contract=contract).first()
        self.assertIsNotNone(replacement_log)
        self.assertEqual(replacement_log.action_type, ContractProbeReplacement.ACTION_REPLACE)
        self.assertEqual(replacement_log.old_probe_code, "PRB-OLD-01")
        self.assertEqual(replacement_log.new_probe_code, "PRB-NEW-01")
        self.assertIsNone(replacement_log.transferred_license)

        # 驗證客戶軌跡
        activity = CustomerActivityLog.objects.filter(customer=cust, activity_type=CustomerActivityLog.TYPE_SUPPORT).first()
        self.assertIsNotNone(activity)
        self.assertIn("PRB-OLD-01", activity.content)
        self.assertIn("PRB-NEW-01", activity.content)

        # 2. 測試「設備退回 (return)」
        return_resp = self.client.post(
            reverse("contract-swap-probe", kwargs={"pk": contract.pk}),
            {
                "action_type": ContractProbeReplacement.ACTION_RETURN,
                "old_probe": probe_new.pk,
                "new_probe": "",
                "reason": "客戶縮減規模退回設備",
            },
            follow=True,
        )
        self.assertEqual(return_resp.status_code, 200)

        probe_new.refresh_from_db()
        self.assertEqual(probe_new.status, Probe.STATUS_AVAILABLE)
        self.assertIsNone(probe_new.customer)
        self.assertNotIn(probe_new, contract.probes.all())

    def test_contract_attachment_upload_and_delete(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.customers.models import ContractAttachment

        cust = Customer.objects.create(code="CUST-ATT-01", name="附件測試客戶")
        contract = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-ATT-001",
            name="合約附件測試",
            end_date=datetime.date.today() + datetime.timedelta(days=365),
        )

        test_file = SimpleUploadedFile("contract_doc.pdf", b"%PDF-1.4 test contract content", content_type="application/pdf")
        upload_resp = self.client.post(
            reverse("contract-attachment-upload", kwargs={"contract_pk": contract.pk}),
            {"attachments": [test_file]},
            follow=True,
        )
        self.assertEqual(upload_resp.status_code, 200)
        self.assertEqual(contract.attachments.count(), 1)
        att = contract.attachments.first()
        self.assertEqual(att.filename, "contract_doc.pdf")
        self.assertTrue(att.is_pdf)

        del_resp = self.client.post(reverse("contract-attachment-delete", kwargs={"pk": att.pk}), follow=True)
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(contract.attachments.count(), 0)

    def test_customer_info_section_crud_and_list_filters(self):
        from apps.customers.models import CustomerInfoSection
        from apps.licenses.models import Product, CustomerLicense
        from apps.hardware.models import HardwareDevice, HardwareModel, Supplier

        cust = Customer.objects.create(code="CUST-INFO-01", name="環境區塊測試客戶")

        # 1. 新增自訂命名資訊區塊
        create_resp = self.client.post(
            reverse("info-section-create", kwargs={"customer_pk": cust.pk}),
            {
                "title": "系統環境架構與 IP 規劃",
                "content": "ESXi 8.0 雙節點叢集，管理網段 10.20.30.0/24",
                "sort_order": 1,
            },
            follow=True,
        )
        self.assertEqual(create_resp.status_code, 200)
        self.assertContains(create_resp, "系統環境架構與 IP 規劃")
        self.assertContains(create_resp, "ESXi 8.0 雙節點叢集")

        sec = CustomerInfoSection.objects.get(customer=cust)
        self.assertEqual(sec.title, "系統環境架構與 IP 規劃")

        # 2. 修改區塊
        update_resp = self.client.post(
            reverse("info-section-update", kwargs={"pk": sec.pk}),
            {
                "title": "機房環境與遠端連線資訊",
                "content": "透過 FortiClient VPN 連入，帳號請洽 IT 王組長",
                "sort_order": 2,
            },
            follow=True,
        )
        self.assertEqual(update_resp.status_code, 200)
        sec.refresh_from_db()
        self.assertEqual(sec.title, "機房環境與遠端連線資訊")
        self.assertEqual(sec.sort_order, 2)

        # 3. 測試客戶列表進階篩選 (依產品、硬體型號、維護狀態) 與客戶詳情頁維護中徽章
        prod = Product.objects.create(code="P-FILTER-01", name="流量監控中心")
        lic = CustomerLicense.objects.create(customer=cust, product=prod, license_no="LIC-FLT-01", quantity=1)

        sup = Supplier.objects.create(name="篩選測試供應商")
        hw_model = HardwareModel.objects.create(name="HW-Filter-Pro", port_count=4, supplier=sup)
        dev = HardwareDevice.objects.create(
            code="DEV-FLT-01", model=hw_model, customer=cust, status=HardwareDevice.STATUS_SHIPPED
        )

        contract = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-FLT-01",
            end_date=datetime.date.today() + datetime.timedelta(days=90),
            status=MaintenanceContract.STATUS_ACTIVE,
        )
        contract.licenses.add(lic)
        contract.probes.add(dev)

        # 驗證詳情頁顯示「維護中 (MC-FLT-01)」
        detail_resp = self.client.get(reverse("customer-detail", kwargs={"pk": cust.pk}))
        self.assertContains(detail_resp, "維護中 (MC-FLT-01)")

        # 驗證客戶名冊篩選
        list_by_prod = self.client.get(reverse("customer-list") + f"?product={prod.pk}&contract_status=active&hw_model={hw_model.pk}")
        self.assertEqual(list_by_prod.status_code, 200)
        self.assertContains(list_by_prod, "環境區塊測試客戶")

        # 4. 刪除區塊
        del_resp = self.client.post(reverse("info-section-delete", kwargs={"pk": sec.pk}), follow=True)
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(CustomerInfoSection.objects.filter(customer=cust).count(), 0)

    def test_contract_status_form_and_list_filter(self):
        cust = Customer.objects.create(code="CUST-STAT-01", name="合約狀態測試客戶")
        today = datetime.date.today()

        # 1. 測試以 Form 新增合約，自動計算狀態為生效中
        create_resp = self.client.post(
            reverse("contract-create"),
            {
                "customer": cust.pk,
                "contract_no": "MC-AUTO-001",
                "name": "自動生效合約",
                "start_date": today - datetime.timedelta(days=10),
                "end_date": today + datetime.timedelta(days=60),
                "amount": 50000,
            },
            follow=True,
        )
        self.assertEqual(create_resp.status_code, 200)
        c1 = MaintenanceContract.objects.get(contract_no="MC-AUTO-001")
        self.assertEqual(c1.status, MaintenanceContract.STATUS_ACTIVE)
        self.assertFalse(c1.is_terminated)

        # 2. 測試編輯合約並勾選「終止此合約」
        update_resp = self.client.post(
            reverse("contract-update", kwargs={"pk": c1.pk}),
            {
                "customer": cust.pk,
                "contract_no": "MC-AUTO-001",
                "name": "自動生效合約(已終止)",
                "start_date": today - datetime.timedelta(days=10),
                "end_date": today + datetime.timedelta(days=60),
                "is_terminated": True,
                "amount": 50000,
            },
            follow=True,
        )
        self.assertEqual(update_resp.status_code, 200)
        c1.refresh_from_db()
        self.assertTrue(c1.is_terminated)
        self.assertEqual(c1.status, MaintenanceContract.STATUS_TERMINATED)

        # 3. 測試合約列表頁面篩選 filter=terminated 及 pending
        c_pending = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-PEND-001",
            start_date=today + datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=100),
        )
        resp_term = self.client.get(reverse("contract-list") + "?filter=terminated")
        self.assertEqual(resp_term.status_code, 200)
        self.assertContains(resp_term, "MC-AUTO-001")
        self.assertNotContains(resp_term, "MC-PEND-001")

        resp_pending = self.client.get(reverse("contract-list") + "?filter=pending")
        self.assertEqual(resp_pending.status_code, 200)
        self.assertContains(resp_pending, "MC-PEND-001")
        self.assertNotContains(resp_pending, "MC-AUTO-001")

    def test_contract_search_and_date_range_filter(self):
        cust_a = Customer.objects.create(code="CUST-SRCH-A", name="搜尋測試企業A")
        cust_b = Customer.objects.create(code="CUST-SRCH-B", name="搜尋測試企業B")
        today = datetime.date.today()

        c1 = MaintenanceContract.objects.create(
            customer=cust_a,
            contract_no="MC-2026-ALPHA",
            name="企業核心維護案",
            start_date=today,
            end_date=today + datetime.timedelta(days=180),
        )
        c2 = MaintenanceContract.objects.create(
            customer=cust_b,
            contract_no="MC-2026-BETA",
            name="網路資安專案",
            start_date=today + datetime.timedelta(days=200),
            end_date=today + datetime.timedelta(days=365),
        )

        # 1. 測試合約編號關鍵字搜尋
        resp_kw = self.client.get(reverse("contract-list"), {"search": "ALPHA"})
        self.assertEqual(resp_kw.status_code, 200)
        self.assertContains(resp_kw, "MC-2026-ALPHA")
        self.assertNotContains(resp_kw, "MC-2026-BETA")

        # 2. 測試日期區間涵蓋重疊搜尋
        # 查詢區間包含 c1，但不包含 c2
        q_start = (today + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        q_end = (today + datetime.timedelta(days=60)).strftime("%Y-%m-%d")
        resp_date = self.client.get(
            reverse("contract-list"),
            {"date_from": q_start, "date_to": q_end},
        )
        self.assertEqual(resp_date.status_code, 200)
        self.assertContains(resp_date, "MC-2026-ALPHA")
        self.assertNotContains(resp_date, "MC-2026-BETA")

    def test_customer_activity_logs_dynamic_api(self):
        from apps.customers.models import CustomerActivityLog
        cust = Customer.objects.create(code="CUST-ACT-001", name="動態軌跡測試戶")

        # 建立 15 則軌跡紀錄
        for i in range(1, 16):
            CustomerActivityLog.objects.create(
                customer=cust,
                activity_type=CustomerActivityLog.TYPE_MEETING if i % 2 == 1 else CustomerActivityLog.TYPE_CALL,
                content=f"第 {i} 則討論事項與跟進內容，序號 {i}",
                created_by=self.user,
            )

        # 1. 呼叫第 1 頁 API
        resp_p1 = self.client.get(
            reverse("api-customer-activity-logs", kwargs={"customer_pk": cust.pk}),
            {"page": 1},
        )
        self.assertEqual(resp_p1.status_code, 200)
        data_p1 = resp_p1.json()
        self.assertTrue(data_p1["has_next"])
        self.assertEqual(data_p1["next_page_number"], 2)
        self.assertEqual(data_p1["total_count"], 15)
        self.assertIn("第 15 則討論事項", data_p1["html"])

        # 2. 呼叫第 2 頁 API (載入更多)
        resp_p2 = self.client.get(
            reverse("api-customer-activity-logs", kwargs={"customer_pk": cust.pk}),
            {"page": 2},
        )
        self.assertEqual(resp_p2.status_code, 200)
        data_p2 = resp_p2.json()
        self.assertFalse(data_p2["has_next"])
        self.assertIsNone(data_p2["next_page_number"])
        self.assertIn("第 1 則討論事項", data_p2["html"])

        # 3. 測試條件篩選 (type=meeting: 8 筆)
        resp_filter = self.client.get(
            reverse("api-customer-activity-logs", kwargs={"customer_pk": cust.pk}),
            {"type": "meeting"},
        )
        self.assertEqual(resp_filter.status_code, 200)
        data_filter = resp_filter.json()
        self.assertEqual(data_filter["total_count"], 8)
        self.assertFalse(data_filter["has_next"])

    def test_customer_detail_contracts_and_licenses_history_filter(self):
        from apps.licenses.models import CustomerLicense, Product
        cust = Customer.objects.create(code="CUST-HIST-01", name="合約歷程測試客")
        today = datetime.date.today()

        # 1. 建立合約：1 筆生效中、1 筆待生效、1 筆已到期
        ct_active = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-ACTIVE-01",
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=100),
        )
        ct_pending = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-PENDING-01",
            start_date=today + datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=365),
        )
        ct_expired = MaintenanceContract.objects.create(
            customer=cust,
            contract_no="MC-EXPIRED-01",
            start_date=today - datetime.timedelta(days=200),
            end_date=today - datetime.timedelta(days=10),
        )

        # 2. 建立授權：1 筆永久買斷（生效）、1 筆已到期
        prod = Product.objects.create(name="網路防護系統", code="PROD-SEC-01")
        lic_active = CustomerLicense.objects.create(
            customer=cust,
            product=prod,
            license_no="LIC-ACT-001",
            is_perpetual=True,
        )
        lic_expired = CustomerLicense.objects.create(
            customer=cust,
            product=prod,
            license_no="LIC-EXP-001",
            license_type="subscription",
            is_perpetual=False,
            start_date=today - datetime.timedelta(days=100),
            end_date=today - datetime.timedelta(days=5),
        )

        resp = self.client.get(reverse("customer-detail", kwargs={"pk": cust.pk}))
        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode("utf-8")
        # 驗證「顯示全部」核取方塊與計數器元素存在
        self.assertIn('id="toggle-all-contracts"', content)
        self.assertIn('id="toggle-all-licenses"', content)
        self.assertIn('id="contracts-counter"', content)
        self.assertIn('id="licenses-counter"', content)

        # 驗證合約行與授權行帶有正確的 data-active 標記
        self.assertIn('data-active="1"', content)
        self.assertIn('data-active="0"', content)
        self.assertIn('MC-ACTIVE-01', content)
        self.assertIn('MC-PENDING-01', content)
        self.assertIn('MC-EXPIRED-01', content)
        self.assertIn('LIC-ACT-001', content)
        self.assertIn('LIC-EXP-001', content)

    def test_global_customer_custom_fields_all_types_and_import_export(self):
        import json
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.core.models import SystemSetting

        # 1. 設定全域客戶自訂欄位 (6種型態: text, number, datetime, boolean, select, multiselect)
        schema = [
            {
                "key": "account_manager",
                "label": "負責業務",
                "type": "text",
                "options": [],
                "default": "林專員",
                "required": False,
                "show_in_list": True,
            },
            {
                "key": "employee_scale",
                "label": "員工規模",
                "type": "number",
                "options": [],
                "default": "100",
                "required": False,
                "show_in_list": False,
            },
            {
                "key": "first_onboard_at",
                "label": "首次上線時間",
                "type": "datetime",
                "options": [],
                "default": "2026-10-29T09:30",
                "required": False,
                "show_in_list": True,
            },
            {
                "key": "is_vip",
                "label": "VIP客戶",
                "type": "boolean",
                "options": [],
                "default": "true",
                "required": False,
                "show_in_list": True,
            },
            {
                "key": "industry",
                "label": "產業別",
                "type": "select",
                "options": ["金融業", "製造業", "政府機關"],
                "default": "金融業",
                "required": True,
                "show_in_list": True,
            },
            {
                "key": "tags",
                "label": "環境標籤",
                "type": "multiselect",
                "options": ["HA架構", "跨國專線", "雲端混合", "SOC代管"],
                "default": "HA架構",
                "required": False,
                "show_in_list": True,
            },
        ]

        resp_schema = self.client.post(
            reverse("customer-custom-fields"),
            {"custom_field_schema_raw": json.dumps(schema, ensure_ascii=False)},
            follow=True,
        )
        self.assertEqual(resp_schema.status_code, 200)
        saved_schema = SystemSetting.get_settings().customer_custom_field_schema
        self.assertEqual(len(saved_schema), 6)
        # 驗證 datetime default 自動正規化為 YYYY-MM-DD HH:MM
        self.assertEqual(saved_schema[2]["default"], "2026-10-29 09:30")

        # 2. 測試新增客戶表單預設值載入與儲存（測試非必填欄位 account_manager 清空為空白不強制套回預設）
        create_get = self.client.get(reverse("customer-create"))
        self.assertEqual(create_get.status_code, 200)
        self.assertContains(create_get, "林專員")
        self.assertContains(create_get, "2026-10-29T09:30")

        post_cust_resp = self.client.post(
            reverse("customer-create"),
            {
                "code": "CUST-CF-01",
                "name": "自訂欄位測試金控",
                "tax_id": "12345678",
                "contact": "陳協理",
                "phone": "02-88889999",
                "email": "chen@fhc.test.com",
                "address": "台北市信義區",
                "note": "重點測試客戶",
                "dyn_account_manager": "",  # 刻意清空非必填欄位
                "dyn_employee_scale": "500",
                "dyn_first_onboard_at": "2026-10-29T14:45",
                "dyn_is_vip": "on",
                "dyn_industry": "金融業",
                "dyn_tags": ["HA架構", "SOC代管"],
            },
            follow=True,
        )
        self.assertEqual(post_cust_resp.status_code, 200)
        cust_cf = Customer.objects.get(code="CUST-CF-01")
        self.assertNotIn("account_manager", cust_cf.custom_values)
        self.assertEqual(cust_cf.custom_values.get("employee_scale"), 500)
        self.assertEqual(cust_cf.custom_values.get("first_onboard_at"), "2026-10-29 14:45")
        self.assertTrue(cust_cf.custom_values.get("is_vip"))
        self.assertEqual(cust_cf.custom_values.get("industry"), "金融業")
        self.assertEqual(cust_cf.custom_values.get("tags"), ["HA架構", "SOC代管"])

        # 3. 驗證客戶詳情頁「基本資料」卡片完整顯示所有自訂欄位
        detail_resp = self.client.get(reverse("customer-detail", args=[cust_cf.pk]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "首次上線時間")
        self.assertContains(detail_resp, "2026-10-29 14:45")
        self.assertContains(detail_resp, "員工規模")
        self.assertContains(detail_resp, "500")
        self.assertContains(detail_resp, "HA架構, SOC代管")

        # 4. 驗證客戶名冊列表頁顯示勾選 show_in_list 之標籤、關鍵字搜尋與動態下拉篩選器
        list_resp = self.client.get(reverse("customer-list"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertContains(list_resp, "產業別: 金融業")
        self.assertContains(list_resp, "首次上線時間: 2026-10-29 14:45")
        self.assertContains(list_resp, "環境標籤: HA架構, SOC代管")
        self.assertNotContains(list_resp, "員工規模: 500")  # show_in_list=False

        # 測試關鍵字搜尋自訂欄位值
        q_resp = self.client.get(reverse("customer-list"), {"q": "SOC代管"})
        self.assertContains(q_resp, "自訂欄位測試金控")

        # 測試動態篩選器 (cf_industry, cf_tags, cf_is_vip)
        flt_match = self.client.get(
            reverse("customer-list"),
            {"cf_industry": "金融業", "cf_tags": "SOC代管", "cf_is_vip": "true"},
        )
        self.assertContains(flt_match, "自訂欄位測試金控")

        flt_nomatch = self.client.get(
            reverse("customer-list"),
            {"cf_industry": "製造業"},
        )
        self.assertNotContains(flt_nomatch, "自訂欄位測試金控")

        # 5. 測試匯出 Excel 與下載匯入範本
        export_resp = self.client.get(reverse("customer-export"))
        self.assertEqual(export_resp.status_code, 200)

        tpl_resp = self.client.get(reverse("customer-import-template"))
        self.assertEqual(tpl_resp.status_code, 200)

        # 6. 測試 CSV 批次匯入包含自訂欄位
        csv_content = (
            "客戶代號*,客戶名稱*,統一編號,自訂: 負責業務,自訂: 員工規模,自訂: 首次上線時間,自訂: VIP客戶,自訂: 產業別*,自訂: 環境標籤\n"
            "CUST-CF-02,台積製造測試,87654321,王業務,1200,2026-11-01 10:15,否,製造業,\"HA架構, 跨國專線\"\n"
        ).encode("utf-8-sig")
        upload_file = SimpleUploadedFile("import_cust.csv", csv_content, content_type="text/csv")
        imp_resp = self.client.post(
            reverse("customer-import"),
            {"file": upload_file},
            follow=True,
        )
        self.assertEqual(imp_resp.status_code, 200)
        cust_cf2 = Customer.objects.get(code="CUST-CF-02")
        self.assertEqual(cust_cf2.custom_values.get("account_manager"), "王業務")
        self.assertEqual(cust_cf2.custom_values.get("employee_scale"), 1200)
        self.assertEqual(cust_cf2.custom_values.get("first_onboard_at"), "2026-11-01 10:15")
        self.assertFalse(cust_cf2.custom_values.get("is_vip"))
        self.assertEqual(cust_cf2.custom_values.get("industry"), "製造業")
        self.assertEqual(cust_cf2.custom_values.get("tags"), ["HA架構", "跨國專線"])

    def test_customer_hierarchy_chain_and_permissions_and_import(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.customers.forms import CustomerForm
        from apps.customers.models import CustomerFollow
        from apps.core.models import UserProfile
        from apps.licenses.models import Product, CustomerLicense
        from apps.hardware.models import HardwareDevice as Probe, HardwareModel, Supplier

        # 1. 建立多層上下游關係: 總代理 A -> 經銷商 B -> 終端客戶 C -> 下屬單位 D，以及同層兄弟經銷商 B2 -> 客戶 E
        agent_a = Customer.objects.create(code="AGENT-A", name="頂層總代理商A")
        dist_b = Customer.objects.create(code="DIST-B", name="一級經銷商B", parent=agent_a)
        dist_b2 = Customer.objects.create(code="DIST-B2", name="同層兄弟經銷商B2", parent=agent_a)
        client_c = Customer.objects.create(code="CUST-C", name="終端企業客戶C", parent=dist_b)
        sub_d = Customer.objects.create(code="SUB-D", name="下屬分支單位D", parent=client_c)
        other_e = Customer.objects.create(code="CUST-E", name="兄弟經銷商客戶E", parent=dist_b2)

        # 為 client_c 新增硬體與授權書以驗證樹狀節點統計
        supplier = Supplier.objects.create(name="Hierarchy供應商")
        hw_model = HardwareModel.objects.create(name="Hierarchy-Probe", port_count=4, supplier=supplier)
        Probe.objects.create(code="HW-C-01", model=hw_model, customer=client_c, status=Probe.STATUS_SHIPPED)
        Probe.objects.create(code="HW-C-02", model=hw_model, customer=client_c, status=Probe.STATUS_LOANED)
        prod = Product.objects.create(code="PROD-H1", name="階層測試軟體")
        CustomerLicense.objects.create(customer=client_c, product=prod, license_no="LIC-C-01", quantity=1)

        # 驗證 get_ancestors 與 get_descendant_ids
        self.assertEqual(sub_d.get_ancestors(), [agent_a, dist_b, client_c])
        self.assertEqual(agent_a.get_descendant_ids(), {dist_b.pk, dist_b2.pk, client_c.pk, sub_d.pk, other_e.pk})
        self.assertEqual(dist_b.get_descendant_ids(), {client_c.pk, sub_d.pk})

        # 2. 驗證防呆：禁止自己指向自己、禁止將下層子孫設為自己的上層 (防止循環指向)
        form_self = CustomerForm(
            data={"code": agent_a.code, "name": agent_a.name, "parent": agent_a.pk},
            instance=agent_a,
            user=self.user,
        )
        self.assertFalse(form_self.is_valid())
        self.assertIn("parent", form_self.errors)

        form_cycle = CustomerForm(
            data={"code": agent_a.code, "name": agent_a.name, "parent": sub_d.pk},
            instance=agent_a,
            user=self.user,
        )
        self.assertFalse(form_cycle.is_valid())
        self.assertIn("parent", form_cycle.errors)

        # 3. 驗證經銷商 B 詳情頁關係鍊：包含上層 A、目前 B、下層 C 與 D，且樹狀結構中不包含兄弟經銷商 B2 與 E
        detail_b = self.client.get(reverse("customer-detail", args=[dist_b.pk]))
        self.assertEqual(detail_b.status_code, 200)
        self.assertContains(detail_b, "客戶關係鍊 / 通路階層")
        self.assertContains(detail_b, "頂層總代理商A")
        self.assertContains(detail_b, "終端企業客戶C")
        self.assertContains(detail_b, "下屬分支單位D")
        self.assertEqual(detail_b.context["ancestors_count"], 1)
        self.assertEqual(detail_b.context["direct_children_count"], 1)
        self.assertEqual(detail_b.context["total_descendants_count"], 2)

        def collect_tree_codes(node):
            codes = [node["code"]]
            for ch in node.get("children", []):
                codes.extend(collect_tree_codes(ch))
            return codes

        tree_codes = collect_tree_codes(detail_b.context["hierarchy_root"])
        self.assertEqual(tree_codes, ["AGENT-A", "DIST-B", "CUST-C", "SUB-D"])
        self.assertNotIn("DIST-B2", tree_codes)
        self.assertNotIn("CUST-E", tree_codes)

        # 檢查節點統計數字 (client_c 硬體=2, 授權書=1)
        c_node = detail_b.context["current_hierarchy_node"]["children"][0]
        self.assertEqual(c_node["code"], "CUST-C")
        self.assertEqual(c_node["hardware_count"], 2)
        self.assertEqual(c_node["license_count"], 1)

        # 4. 驗證客戶名冊關鍵字搜尋上層代號/名稱可搜出直屬與間接下層客戶
        list_search = self.client.get(reverse("customer-list"), {"q": "頂層總代理商A"})
        self.assertEqual(list_search.status_code, 200)
        self.assertContains(list_search, "一級經銷商B")
        self.assertContains(list_search, "終端企業客戶C")
        self.assertContains(list_search, "下屬分支單位D")

        # 5. 驗證快捷綁定與解除下層客戶
        free_cust = Customer.objects.create(code="FREE-01", name="獨立待綁定客戶")
        bind_resp = self.client.post(
            reverse("customer-bind-child", args=[dist_b.pk]),
            {"child_customer_id": str(free_cust.pk)},
            follow=True,
        )
        self.assertEqual(bind_resp.status_code, 200)
        free_cust.refresh_from_db()
        self.assertEqual(free_cust.parent_id, dist_b.pk)

        # 測試在快捷綁定中嘗試綁定上層 A 為 B 的下層 -> 應被阻擋
        bind_cycle_resp = self.client.post(
            reverse("customer-bind-child", args=[dist_b.pk]),
            {"child_customer_id": str(agent_a.pk)},
            follow=True,
        )
        self.assertEqual(bind_cycle_resp.status_code, 200)
        agent_a.refresh_from_db()
        self.assertIsNone(agent_a.parent_id)

        # 解除關聯
        unbind_resp = self.client.post(
            reverse("customer-unbind-child", args=[dist_b.pk, free_cust.pk]),
            follow=True,
        )
        self.assertEqual(unbind_resp.status_code, 200)
        free_cust.refresh_from_db()
        self.assertIsNone(free_cust.parent_id)

        # 6. 驗證業務隔離視野 (SCOPE_FOLLOWED)：未關注之上下游節點僅顯示名稱不可點擊
        sales_user = User.objects.create_user(username="scoped_sales", password="password123")
        profile, _ = UserProfile.objects.get_or_create(user=sales_user)
        profile.role = UserProfile.ROLE_CUSTOMER_ADMIN
        profile.customer_scope = UserProfile.SCOPE_FOLLOWED
        profile.save()

        # 僅關注 dist_b 與 client_c，未關注 agent_a 與 sub_d
        CustomerFollow.objects.create(customer=dist_b, user=sales_user)
        CustomerFollow.objects.create(customer=client_c, user=sales_user)

        self.client.force_login(sales_user)
        scoped_detail = self.client.get(reverse("customer-detail", args=[dist_b.pk]))
        self.assertEqual(scoped_detail.status_code, 200)
        root_node = scoped_detail.context["hierarchy_root"]
        self.assertEqual(root_node["code"], "AGENT-A")
        self.assertFalse(root_node["can_access"])  # 未關注上層不可點擊
        b_node = root_node["children"][0]
        self.assertTrue(b_node["can_access"])
        c_node_scoped = b_node["children"][0]
        self.assertTrue(c_node_scoped["can_access"])  # 已關注下層可點擊
        d_node_scoped = c_node_scoped["children"][0]
        self.assertFalse(d_node_scoped["can_access"])  # 未關注孫客戶不可點擊

        # 直接存取未關注的 agent_a 應被拒絕
        denied_resp = self.client.get(reverse("customer-detail", args=[agent_a.pk]))
        self.assertEqual(denied_resp.status_code, 302)

        # 切回管理員測試匯入
        self.client.force_login(self.user)

        # 7. 測試匯入包含上層客戶代號 (同檔案中子客戶在前、母客戶在後也能正確綁定)
        csv_hierarchy = (
            "客戶代號*,客戶名稱*,上層客戶代號\n"
            "IMP-CHILD,匯入下層客戶,IMP-PARENT\n"
            "IMP-PARENT,匯入上層經銷商,AGENT-A\n"
        ).encode("utf-8-sig")
        imp_ok = self.client.post(
            reverse("customer-import"),
            {"file": SimpleUploadedFile("imp_ok.csv", csv_hierarchy, content_type="text/csv")},
            follow=True,
        )
        self.assertEqual(imp_ok.status_code, 200)
        imp_parent = Customer.objects.get(code="IMP-PARENT")
        imp_child = Customer.objects.get(code="IMP-CHILD")
        self.assertEqual(imp_parent.parent_id, agent_a.pk)
        self.assertEqual(imp_child.parent_id, imp_parent.pk)

        # 測試匯入循環指向應被攔截且不寫入
        csv_cycle = (
            "客戶代號*,客戶名稱*,上層客戶代號\n"
            "CYC-1,循環一,CYC-2\n"
            "CYC-2,循環二,CYC-1\n"
        ).encode("utf-8-sig")
        imp_err = self.client.post(
            reverse("customer-import"),
            {"file": SimpleUploadedFile("imp_cycle.csv", csv_cycle, content_type="text/csv")},
        )
        self.assertEqual(imp_err.status_code, 200)
        self.assertContains(imp_err, "循環指向")
        self.assertFalse(Customer.objects.filter(code="CYC-1").exists())






