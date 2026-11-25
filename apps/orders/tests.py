import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Role, SystemSetting, UserProfile
from apps.customers.models import Customer, CustomerFollow, MaintenanceContract
from apps.hardware.models import HardwareDevice, HardwareModel, InventoryTransaction
from apps.licenses.models import CustomerLicense, Product
from apps.orders.models import Order, OrderAttachment, OrderItem, OrderPayment, OrderVersion

User = get_user_model()


class OrderWorkflowTestCase(TestCase):
    def setUp(self):
        Role.ensure_system_roles()

        self.sales_user = User.objects.create_user(username="sales1", password="Password123!", first_name="業務王")
        sales_role = Role.objects.get(code="sales_rep")
        UserProfile.objects.create(
            user=self.sales_user,
            role_obj=sales_role,
            role=sales_role.code,
            customer_scope=sales_role.customer_scope,
            can_view_crm=True,
            can_edit_customers=True,
            can_edit_orders=True,
        )

        self.other_sales = User.objects.create_user(username="sales2", password="Password123!")
        UserProfile.objects.create(
            user=self.other_sales,
            role_obj=sales_role,
            role=sales_role.code,
            customer_scope="followed",
            can_view_crm=True,
            can_edit_customers=True,
            can_edit_orders=True,
        )

        self.manager_user = User.objects.create_user(username="mgr1", password="Password123!", first_name="主管陳")
        mgr_role = Role.objects.get(code="sales_manager")
        UserProfile.objects.create(
            user=self.manager_user,
            role_obj=mgr_role,
            role=mgr_role.code,
            customer_scope="all",
            can_view_crm=True,
            can_edit_orders=True,
            can_approve_orders=True,
            can_approve_licenses=True,
            can_approve_contracts=True,
        )

        self.engineer_user = User.objects.create_user(username="eng1", password="Password123!", first_name="工程師林")
        eng_role = Role.objects.get(code="probe_admin")
        UserProfile.objects.create(
            user=self.engineer_user,
            role_obj=eng_role,
            role=eng_role.code,
            customer_scope="all",
            can_view_crm=True,
            can_edit_probes=True,
            can_ship_orders=True,
        )

        self.finance_user = User.objects.create_user(username="fin1", password="Password123!", first_name="管理部張")
        fin_role = Role.objects.get(code="finance_admin")
        UserProfile.objects.create(
            user=self.finance_user,
            role_obj=fin_role,
            role=fin_role.code,
            customer_scope="all",
            can_view_crm=True,
            can_close_orders=True,
        )

        self.customer = Customer.objects.create(
            code="80123456",
            name="測試客戶股份有限公司",
            contact="張經理",
            phone="02-2222-3333",
            email="client@example.com",
        )
        self.other_customer = Customer.objects.create(code="99887766", name="隔離測試客戶")
        CustomerFollow.objects.create(customer=self.customer, user=self.sales_user)

        self.product = Product.objects.create(code="FLOW-PRO", name="流量分析核心軟體", default_unit="套")
        self.hw_model = HardwareModel.objects.create(model_code="NP-100", name="NP-1000 探針設備")
        self.dev1 = HardwareDevice.objects.create(code="SN-001", model=self.hw_model, status=HardwareDevice.STATUS_AVAILABLE)
        self.dev2 = HardwareDevice.objects.create(code="SN-002", model=self.hw_model, status=HardwareDevice.STATUS_AVAILABLE)
        self.dev3 = HardwareDevice.objects.create(code="SN-003", model=self.hw_model, status=HardwareDevice.STATUS_AVAILABLE)

    def _create_order_via_post(self, include_hardware=True):
        self.client.force_login(self.sales_user)
        order_targets = "r_1,r_3" if include_hardware else "r_1"
        post_data = {
            "order_no": "SO-20261125-0001",
            "customer": self.customer.pk,
            "contact_name": "張經理",
            "contact_phone": "02-2222-3333",
            "contact_email": "client@example.com",
            "title": "年度資安探針採購案",
            "quote_date": "2026-11-25",
            "valid_until": "2026-12-02",
            "tax_mode": "tax_exclusive",
            "discount_amount": "0",
            "payment_terms": "訂金 30% + 尾款 70%",
            "terms_note": "標準報價條款",
            "salesperson": self.sales_user.pk,
            "item_row_uid": ["r_1", "r_2"] + (["r_3"] if include_hardware else []),
            "item_type": ["license", "warranty"] + (["hardware"] if include_hardware else []),
            "item_name": ["流量分析核心軟體", "維護保固"] + (["NP-1000 探針設備"] if include_hardware else []),
            "item_description": ["永久授權", "5x8 服務"] + (["含機架配件"] if include_hardware else []),
            "item_product": [str(self.product.pk), ""] + ([""] if include_hardware else []),
            "item_license_type": ["perpetual", "perpetual"] + ([""] if include_hardware else []),
            "item_hardware_model": ["", ""] + ([str(self.hw_model.pk)] if include_hardware else []),
            "item_start_date": ["2026-11-25", ""] + ([""] if include_hardware else []),
            "item_end_date": ["", ""] + ([""] if include_hardware else []),
            "item_warranty_days": ["", "365"] + ([""] if include_hardware else []),
            "item_warranty_order_targets": ["", order_targets] + ([""] if include_hardware else []),
            "item_warranty_existing_licenses": ["", ""] + ([""] if include_hardware else []),
            "item_warranty_existing_devices": ["", ""] + ([""] if include_hardware else []),
            "item_sla_level": ["", "5x8"] + ([""] if include_hardware else []),
            "item_quantity": ["1", "1"] + (["2"] if include_hardware else []),
            "item_unit": ["套", "式"] + (["台"] if include_hardware else []),
            "item_unit_price": ["100000", "20000"] + (["50000"] if include_hardware else []),
        }
        resp = self.client.post(reverse("order-create"), data=post_data)
        self.assertEqual(resp.status_code, 302)
        return Order.objects.get(order_no="SO-20261125-0001")

    def test_order_creation_totals_and_version_snapshot_on_price_cut(self):
        order = self._create_order_via_post(include_hardware=True)
        # 100000 + 20000 + 2*50000 = 220000; 5% tax = 11000; total = 231000
        self.assertEqual(order.subtotal, Decimal("220000"))
        self.assertEqual(order.tax_amount, Decimal("11000"))
        self.assertEqual(order.total_amount, Decimal("231000"))
        self.assertEqual(order.current_version, 1)
        self.assertEqual(order.versions.count(), 1)

        # 一般儲存不跳版號
        self.client.force_login(self.sales_user)
        edit_url = reverse("order-update", kwargs={"pk": order.pk})
        base_payload = {
            "order_no": order.order_no,
            "customer": self.customer.pk,
            "contact_name": "張經理",
            "title": order.title,
            "quote_date": "2026-11-25",
            "valid_until": "2026-12-02",
            "tax_mode": "tax_exclusive",
            "discount_amount": "0",
            "payment_terms": order.payment_terms,
            "terms_note": "修改後條款",
            "salesperson": self.sales_user.pk,
            "item_row_uid": ["r_1", "r_2", "r_3"],
            "item_type": ["license", "warranty", "hardware"],
            "item_name": ["流量分析核心軟體", "維護保固", "NP-1000 探針設備"],
            "item_description": ["永久授權", "5x8", ""],
            "item_product": [str(self.product.pk), "", ""],
            "item_license_type": ["perpetual", "perpetual", ""],
            "item_hardware_model": ["", "", str(self.hw_model.pk)],
            "item_start_date": ["2026-11-25", "", ""],
            "item_end_date": ["", "", ""],
            "item_warranty_days": ["", "365", ""],
            "item_warranty_order_targets": ["", "r_1,r_3", ""],
            "item_warranty_existing_licenses": ["", "", ""],
            "item_warranty_existing_devices": ["", "", ""],
            "item_sla_level": ["", "5x8", ""],
            "item_quantity": ["1", "1", "2"],
            "item_unit": ["套", "式", "台"],
            "item_unit_price": ["100000", "20000", "50000"],
            "save_mode": "save_draft",
        }
        self.client.post(edit_url, data=base_payload)
        order.refresh_from_db()
        self.assertEqual(order.current_version, 1)
        self.assertEqual(order.versions.count(), 1)

        # 客戶砍價折讓 20000，並點擊「儲存並產生新版報價單」跳 v2
        base_payload["discount_amount"] = "20000"
        base_payload["save_mode"] = "new_version"
        base_payload["version_note"] = "客戶議價折讓兩萬"
        self.client.post(edit_url, data=base_payload)
        order.refresh_from_db()
        self.assertEqual(order.current_version, 2)
        self.assertEqual(order.total_amount, Decimal("210000"))  # (220000 - 20000) * 1.05
        v1 = order.versions.get(version_no=1)
        v2 = order.versions.get(version_no=2)
        self.assertEqual(v1.total_amount, Decimal("231000"))
        self.assertEqual(v2.total_amount, Decimal("210000"))
        self.assertEqual(v2.change_note, "客戶議價折讓兩萬")

        # 列印頁可調閱 v1 舊版與 v2 新版
        resp_v1 = self.client.get(reverse("order-quotation-version-print", kwargs={"pk": order.pk, "version_no": 1}))
        self.assertEqual(resp_v1.status_code, 200)
        self.assertContains(resp_v1, "SO-20261125-0001-v1")
        self.assertContains(resp_v1, "貳拾參萬壹仟元整")

    def test_flexible_submission_and_full_end_to_end_workflow(self):
        order = self._create_order_via_post(include_hardware=True)
        self.client.force_login(self.sales_user)

        # 未附回簽單且未填說明 -> 阻擋送審，仍維持 draft
        self.client.post(reverse("order-submit", kwargs={"pk": order.pk}), data={"no_signed_quote_reason": ""})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_DRAFT)

        # 上傳回簽報價單佐證附件後成功送審
        signed_pdf = SimpleUploadedFile("signed_quote.pdf", b"%PDF-1.4 signed", content_type="application/pdf")
        self.client.post(
            reverse("order-attachment-upload", kwargs={"pk": order.pk}),
            data={"attachment_type": "signed_quote", "file": signed_pdf, "note": "客戶回簽"},
        )
        self.assertTrue(order.has_signed_quote_attachment)
        self.client.post(reverse("order-submit", kwargs={"pk": order.pk}), data={})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_APPROVAL)

        # 送審後業務不可再修改
        resp_edit = self.client.get(reverse("order-update", kwargs={"pk": order.pk}))
        self.assertEqual(resp_edit.status_code, 302)

        # 非主管業務不可核准
        self.client.post(reverse("order-approve", kwargs={"pk": order.pk}))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_APPROVAL)

        # 主管核准 -> 自動開立授權書與維護合約（直接核准生效）且進入待出貨
        self.client.force_login(self.manager_user)
        self.client.post(reverse("order-approve", kwargs={"pk": order.pk}))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_SHIPMENT)

        lic = CustomerLicense.objects.get(source_order=order)
        ct = MaintenanceContract.objects.get(source_order=order)
        self.assertEqual(lic.approval_status, CustomerLicense.APPROVAL_APPROVED)
        self.assertEqual(ct.approval_status, MaintenanceContract.APPROVAL_APPROVED)
        self.assertIn(lic, ct.licenses.all())

        # 驗證證書資料 API 可預覽匯出
        cert_resp = self.client.get(reverse("cert-data-api", kwargs={"cert_type": "license", "pk": lic.pk}))
        self.assertEqual(cert_resp.status_code, 200)

        # 工程師檢查出貨：選不足數量 (只選 1 台) 會被阻擋
        self.client.force_login(self.engineer_user)
        hw_item = order.items.get(item_type=OrderItem.TYPE_HARDWARE)
        self.client.post(
            reverse("order-ship", kwargs={"pk": order.pk}),
            data={f"devices_item_{hw_item.pk}": [str(self.dev1.pk)], "shipment_note": "少選一台測試"},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_SHIPMENT)

        # 工程師選滿 2 台並順手填入授權金鑰 -> 設備出貨、掛入合約、狀態轉待收款
        self.client.post(
            reverse("order-ship", kwargs={"pk": order.pk}),
            data={
                f"devices_item_{hw_item.pk}": [str(self.dev1.pk), str(self.dev2.pk)],
                f"license_key_{lic.pk}": "KEY-XYZ-999",
                "shipment_note": "已驗測包裝出貨",
            },
        )
        order.refresh_from_db()
        self.dev1.refresh_from_db()
        self.dev2.refresh_from_db()
        lic.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_PAYMENT)
        self.assertEqual(self.dev1.status, HardwareDevice.STATUS_SHIPPED)
        self.assertEqual(self.dev1.source_order, order)
        self.assertEqual(self.dev2.status, HardwareDevice.STATUS_SHIPPED)
        self.assertIn(self.dev1, ct.probes.all())
        self.assertIn(self.dev2, ct.probes.all())
        self.assertEqual(lic.license_key, "KEY-XYZ-999")
        self.assertEqual(InventoryTransaction.objects.filter(source_order=order, type=InventoryTransaction.TYPE_OUT).count(), 2)

        # 業務分期兩筆回報收款（100,000 + 131,000）
        self.client.force_login(self.sales_user)
        self.client.post(
            reverse("order-payment-report", kwargs={"pk": order.pk}),
            data={
                "amount": "100000",
                "payment_date": "2026-11-26",
                "payment_method": "bank_transfer",
                "reference_no": "11111",
                "note": "第一期款",
            },
        )
        pm1 = OrderPayment.objects.get(order=order, reference_no="11111")

        # 管理部確認第一期入帳 -> 尚欠 131000，維持待收款
        self.client.force_login(self.finance_user)
        self.client.post(reverse("order-payment-confirm", kwargs={"payment_id": pm1.pk}), data={"auto_close": "1"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_PAYMENT)
        self.assertEqual(order.unpaid_balance, Decimal("131000"))

        # 業務回報第二期尾款 131,000
        self.client.force_login(self.sales_user)
        self.client.post(
            reverse("order-payment-report", kwargs={"pk": order.pk}),
            data={
                "amount": "131000",
                "payment_date": "2026-11-27",
                "payment_method": "bank_transfer",
                "reference_no": "22222",
                "note": "尾款",
            },
        )
        pm2 = OrderPayment.objects.get(order=order, reference_no="22222")

        # 管理部確認尾款入帳 -> 自動結案
        self.client.force_login(self.finance_user)
        self.client.post(reverse("order-payment-confirm", kwargs={"payment_id": pm2.pk}), data={"auto_close": "1"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_CLOSED)
        self.assertEqual(order.unpaid_balance, Decimal("0"))

    def test_pure_software_order_no_signed_quote_reason_and_cancel_recycle(self):
        order = self._create_order_via_post(include_hardware=False)
        self.client.force_login(self.sales_user)

        # 未附回簽單但填寫急單說明 -> 彈性放行送審
        self.client.post(
            reverse("order-submit", kwargs={"pk": order.pk}),
            data={"no_signed_quote_reason": "急單，客戶已用 Email 確認購買"},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_APPROVAL)

        # 主管退回 -> 業務可修改後重送 -> 主管核准
        self.client.force_login(self.manager_user)
        self.client.post(reverse("order-reject", kwargs={"pk": order.pk}), data={"rejection_reason": "請確認效期"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_REJECTED)

        self.client.force_login(self.sales_user)
        self.client.post(reverse("order-submit", kwargs={"pk": order.pk}), data={})
        self.client.force_login(self.manager_user)
        self.client.post(reverse("order-approve", kwargs={"pk": order.pk}))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_SHIPMENT)

        # 純軟體訂單仍須工程師確認交付後才轉待收款
        self.client.force_login(self.engineer_user)
        self.client.post(reverse("order-ship", kwargs={"pk": order.pk}), data={"shipment_note": "已交付軟體與金鑰"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING_PAYMENT)

        # 若後續因故作廢，主管執行作廢應自動停用授權並終止維護合約
        self.client.force_login(self.manager_user)
        self.client.post(reverse("order-cancel", kwargs={"pk": order.pk}), data={"cancel_reason": "客戶專案取消"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_CANCELLED)
        lic = CustomerLicense.objects.get(source_order=order)
        ct = MaintenanceContract.objects.get(source_order=order)
        self.assertTrue(lic.is_disabled)
        self.assertTrue(ct.is_terminated)

    def test_followed_scope_isolation(self):
        order = self._create_order_via_post(include_hardware=False)
        # other_sales 為業務隔離模式且未關注 self.customer，應無法看見或存取該訂單
        from django.test import Client
        other_client = Client()
        other_client.force_login(self.other_sales)
        list_resp = other_client.get(reverse("order-list"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.context["total_count"], 0)
        self.assertNotContains(list_resp, order.order_no)
        detail_resp = other_client.get(reverse("order-detail", kwargs={"pk": order.pk}))
        self.assertEqual(detail_resp.status_code, 302)

    def test_order_create_defaults_so_prefix_and_7_day_validity(self):
        from zoneinfo import ZoneInfo
        from django.utils import timezone
        from apps.orders.models import generate_order_no

        self.client.force_login(self.sales_user)
        resp = self.client.get(reverse("order-create"))
        self.assertEqual(resp.status_code, 200)

        today_tw = timezone.now().astimezone(ZoneInfo("Asia/Taipei")).date()
        form = resp.context["form"]
        self.assertTrue(generate_order_no().startswith(f"SO-{today_tw.strftime('%Y%m%d')}-"))
        self.assertEqual(form.initial["quote_date"], today_tw)
        self.assertEqual(form.initial["valid_until"], today_tw + datetime.timedelta(days=7))

    def test_name_and_unit_locked_except_service(self):
        self.client.force_login(self.sales_user)
        post_data = {
            "order_no": "SO-20261125-0099",
            "customer": self.customer.pk,
            "title": "欄位鎖定測試訂單",
            "quote_date": "2026-11-25",
            "valid_until": "2026-12-02",
            "tax_mode": "tax_exclusive",
            "discount_amount": "0",
            "salesperson": self.sales_user.pk,
            "item_row_uid": ["r_1", "r_2", "r_3", "r_4"],
            "item_type": ["license", "hardware", "warranty", "service"],
            "item_name": ["竄改軟體名", "竄改硬體名", "竄改保固名", "客製化顧問導入"],
            "item_description": ["", "", "", "到場教育訓練"],
            "item_product": [str(self.product.pk), "", "", ""],
            "item_license_type": ["perpetual", "", "", ""],
            "item_hardware_model": ["", str(self.hw_model.pk), "", ""],
            "item_start_date": ["", "", "", ""],
            "item_end_date": ["", "", "", ""],
            "item_warranty_days": ["", "", "365", ""],
            "item_warranty_order_targets": ["", "", "r_1", ""],
            "item_warranty_existing_licenses": ["", "", "", ""],
            "item_warranty_existing_devices": ["", "", "", ""],
            "item_sla_level": ["", "", "5x8", ""],
            "item_quantity": ["1", "1", "1", "2"],
            "item_unit": ["竄改單位A", "竄改單位B", "竄改單位C", "人天"],
            "item_unit_price": ["10000", "20000", "5000", "8000"],
        }
        resp = self.client.post(reverse("order-create"), data=post_data)
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.get(order_no="SO-20261125-0099")
        items = list(order.items.all())

        # 軟體授權與硬體設備強制帶入主檔名稱與單位
        self.assertEqual(items[0].name, self.product.name)
        self.assertEqual(items[0].unit, self.product.default_unit)
        self.assertEqual(items[1].name, self.hw_model.name)
        self.assertEqual(items[1].unit, "台")
        # 維護保固自動組合名稱且單位鎖定為「式」
        self.assertIn(self.product.name, items[2].name)
        self.assertIn("365天", items[2].name)
        self.assertEqual(items[2].unit, "式")
        # 僅專業服務/其他保留自訂名稱與單位
        self.assertEqual(items[3].name, "客製化顧問導入")
        self.assertEqual(items[3].unit, "人天")

    def test_warranty_requires_target_and_valid_days(self):
        self.client.force_login(self.sales_user)
        # 未勾選任何標的 -> 阻擋建單
        post_data = {
            "order_no": "SO-20261125-0088",
            "customer": self.customer.pk,
            "title": "缺少標的測試",
            "quote_date": "2026-11-25",
            "valid_until": "2026-12-02",
            "tax_mode": "tax_exclusive",
            "discount_amount": "0",
            "salesperson": self.sales_user.pk,
            "item_row_uid": ["r_1"],
            "item_type": ["warranty"],
            "item_name": ["維護保固"],
            "item_description": [""],
            "item_product": [""],
            "item_license_type": [""],
            "item_hardware_model": [""],
            "item_start_date": [""],
            "item_end_date": [""],
            "item_warranty_days": ["365"],
            "item_warranty_order_targets": [""],
            "item_warranty_existing_licenses": [""],
            "item_warranty_existing_devices": [""],
            "item_sla_level": ["5x8"],
            "item_quantity": ["1"],
            "item_unit": ["式"],
            "item_unit_price": ["15000"],
        }
        resp = self.client.post(reverse("order-create"), data=post_data)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Order.objects.filter(order_no="SO-20261125-0088").exists())
        self.assertTrue(resp.context["item_errors"])

    def test_warranty_only_order_for_existing_customer_products_and_approval_date_calc(self):
        from zoneinfo import ZoneInfo
        from django.utils import timezone

        # 建立客戶既購已核准授權與已出貨硬體
        existing_lic = CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no="LIC-OLD-0001",
            license_type=CustomerLicense.TYPE_PERPETUAL,
            quantity=1,
            unit="套",
            approval_status=CustomerLicense.APPROVAL_APPROVED,
            is_disabled=False,
        )
        existing_dev = HardwareDevice.objects.create(
            code="SN-OLD-001",
            model=self.hw_model,
            status=HardwareDevice.STATUS_SHIPPED,
            customer=self.customer,
        )

        self.client.force_login(self.sales_user)
        post_data = {
            "order_no": "SO-20261125-0077",
            "customer": self.customer.pk,
            "title": "既有產品續保專案",
            "quote_date": "2026-11-25",
            "valid_until": "2026-12-02",
            "tax_mode": "tax_exclusive",
            "discount_amount": "0",
            "salesperson": self.sales_user.pk,
            "item_row_uid": ["r_1"],
            "item_type": ["warranty"],
            "item_name": ["維護保固"],
            "item_description": ["年度維護續約"],
            "item_product": [""],
            "item_license_type": [""],
            "item_hardware_model": [""],
            "item_start_date": [""],
            "item_end_date": [""],
            "item_warranty_days": ["180"],
            "item_warranty_order_targets": [""],
            "item_warranty_existing_licenses": [str(existing_lic.pk)],
            "item_warranty_existing_devices": [str(existing_dev.pk)],
            "item_sla_level": ["7x24"],
            "item_quantity": ["1"],
            "item_unit": ["式"],
            "item_unit_price": ["30000"],
        }
        resp = self.client.post(reverse("order-create"), data=post_data)
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.get(order_no="SO-20261125-0077")
        w_item = order.items.get()

        # 審核前不應有起訖日，僅記錄保固天數與既購標的
        self.assertEqual(w_item.warranty_days, 180)
        self.assertIsNone(w_item.start_date)
        self.assertIsNone(w_item.end_date)
        self.assertIn(existing_lic, w_item.warranty_existing_licenses.all())
        self.assertIn(existing_dev, w_item.warranty_existing_devices.all())

        # 送審並由主管核准 -> 依審核當日起算 180 天並自動開立 MA- 維護合約
        self.client.post(
            reverse("order-submit", kwargs={"pk": order.pk}),
            data={"no_signed_quote_reason": "續約已確認"},
        )
        self.client.force_login(self.manager_user)
        self.client.post(reverse("order-approve", kwargs={"pk": order.pk}))

        today_tw = timezone.now().astimezone(ZoneInfo("Asia/Taipei")).date()
        expected_end = today_tw + datetime.timedelta(days=180)
        w_item.refresh_from_db()
        ct = MaintenanceContract.objects.get(source_order=order)
        self.assertTrue(ct.contract_no.startswith("MA-"))
        self.assertEqual(ct.start_date, today_tw)
        self.assertEqual(ct.end_date, expected_end)
        self.assertEqual(w_item.start_date, today_tw)
        self.assertEqual(w_item.end_date, expected_end)
        self.assertIn(existing_lic, ct.licenses.all())
        self.assertIn(existing_dev, ct.probes.all())

    def test_warranty_selective_same_order_hardware_binding(self):
        hw_model_b = HardwareModel.objects.create(model_code="NP-200", name="NP-2000 備援設備")
        dev_b = HardwareDevice.objects.create(code="SN-B-001", model=hw_model_b, status=HardwareDevice.STATUS_AVAILABLE)

        self.client.force_login(self.sales_user)
        post_data = {
            "order_no": "SO-20261125-0066",
            "customer": self.customer.pk,
            "title": "指定硬體保固綁定測試",
            "quote_date": "2026-11-25",
            "valid_until": "2026-12-02",
            "tax_mode": "tax_exclusive",
            "discount_amount": "0",
            "salesperson": self.sales_user.pk,
            "item_row_uid": ["r_1", "r_2", "r_3"],
            "item_type": ["hardware", "hardware", "warranty"],
            "item_name": ["", "", ""],
            "item_description": ["", "", "僅保固第一項硬體"],
            "item_product": ["", "", ""],
            "item_license_type": ["", "", ""],
            "item_hardware_model": [str(self.hw_model.pk), str(hw_model_b.pk), ""],
            "item_start_date": ["", "", ""],
            "item_end_date": ["", "", ""],
            "item_warranty_days": ["", "", "365"],
            "item_warranty_order_targets": ["", "", "r_1"],
            "item_warranty_existing_licenses": ["", "", ""],
            "item_warranty_existing_devices": ["", "", ""],
            "item_sla_level": ["", "", "5x8"],
            "item_quantity": ["1", "1", "1"],
            "item_unit": ["台", "台", "式"],
            "item_unit_price": ["50000", "60000", "10000"],
        }
        resp = self.client.post(reverse("order-create"), data=post_data)
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.get(order_no="SO-20261125-0066")

        self.client.post(reverse("order-submit", kwargs={"pk": order.pk}), data={"no_signed_quote_reason": "急單"})
        self.client.force_login(self.manager_user)
        self.client.post(reverse("order-approve", kwargs={"pk": order.pk}))

        hw_item_1 = order.items.get(sort_order=1)
        hw_item_2 = order.items.get(sort_order=2)
        self.client.force_login(self.engineer_user)
        self.client.post(
            reverse("order-ship", kwargs={"pk": order.pk}),
            data={
                f"devices_item_{hw_item_1.pk}": [str(self.dev1.pk)],
                f"devices_item_{hw_item_2.pk}": [str(dev_b.pk)],
                "shipment_note": "雙機出貨",
            },
        )
        ct = MaintenanceContract.objects.get(source_order=order)
        self.assertIn(self.dev1, ct.probes.all())
        self.assertNotIn(dev_b, ct.probes.all())

    def test_professional_service_order_lifecycle_and_customer_page_attachments(self):
        from apps.customers.models import CustomerServiceRecord, ServiceRecordAttachment

        # 1. 建立包含「專業服務/其他」且填寫服務日期的訂單
        self.client.force_login(self.sales_user)
        post_data = {
            "order_no": "SO-20261125-0055",
            "customer": self.customer.pk,
            "title": "資安健檢與導入服務案",
            "quote_date": "2026-11-25",
            "valid_until": "2026-12-02",
            "tax_mode": "tax_exclusive",
            "discount_amount": "0",
            "salesperson": self.sales_user.pk,
            "item_row_uid": ["r_1"],
            "item_type": ["service"],
            "item_name": ["駐點導入與教育訓練"],
            "item_description": ["含教育訓練手冊"],
            "item_product": [""],
            "item_license_type": [""],
            "item_hardware_model": [""],
            "item_start_date": ["2026-12-05"],
            "item_end_date": [""],
            "item_warranty_days": [""],
            "item_warranty_order_targets": [""],
            "item_warranty_existing_licenses": [""],
            "item_warranty_existing_devices": [""],
            "item_sla_level": [""],
            "item_quantity": ["2"],
            "item_unit": ["人天"],
            "item_unit_price": ["15000"],
        }
        resp = self.client.post(reverse("order-create"), data=post_data)
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.get(order_no="SO-20261125-0055")

        # 2. 送審並由主管核准 -> 自動開立 SRV- 專業服務紀錄（待執行）
        self.client.post(reverse("order-submit", kwargs={"pk": order.pk}), data={"no_signed_quote_reason": "客戶確認"})
        self.client.force_login(self.manager_user)
        self.client.post(reverse("order-approve", kwargs={"pk": order.pk}))

        svc = CustomerServiceRecord.objects.get(source_order=order)
        self.assertTrue(svc.service_no.startswith("SRV-"))
        self.assertEqual(svc.name, "駐點導入與教育訓練")
        self.assertEqual(svc.service_date, datetime.date(2026, 12, 5))
        self.assertEqual(svc.status, CustomerServiceRecord.STATUS_PENDING)
        self.assertEqual(svc.amount, Decimal("30000"))

        # 3. 單一客戶頁面與訂單詳情頁皆可看見該專業服務方塊與單號
        self.client.force_login(self.sales_user)
        cust_resp = self.client.get(reverse("customer-detail", kwargs={"pk": self.customer.pk}))
        self.assertEqual(cust_resp.status_code, 200)
        self.assertContains(cust_resp, "專業服務 / 其他紀錄")
        self.assertContains(cust_resp, svc.service_no)
        self.assertContains(cust_resp, "駐點導入與教育訓練")

        order_resp = self.client.get(reverse("order-detail", kwargs={"pk": order.pk}))
        self.assertEqual(order_resp.status_code, 200)
        self.assertContains(order_resp, svc.service_no)

        # 4. 上傳附件並編輯備註與日期
        att_file = SimpleUploadedFile("acceptance_report.pdf", b"%PDF-1.4 report", content_type="application/pdf")
        up_resp = self.client.post(
            reverse("service-attachment-upload", kwargs={"service_pk": svc.pk}),
            data={"attachments": [att_file]},
        )
        self.assertEqual(up_resp.status_code, 302)
        self.assertEqual(ServiceRecordAttachment.objects.filter(service_record=svc).count(), 1)
        att = ServiceRecordAttachment.objects.get(service_record=svc)
        dl_resp = self.client.get(reverse("service-attachment-download", kwargs={"pk": att.pk}))
        self.assertEqual(dl_resp.status_code, 200)

        # 5. 工程師確認交付 -> 自動標記專業服務為已完成並填入完工日
        self.client.force_login(self.engineer_user)
        self.client.post(reverse("order-ship", kwargs={"pk": order.pk}), data={"shipment_note": "教育訓練已完成"})
        svc.refresh_from_db()
        self.assertEqual(svc.status, CustomerServiceRecord.STATUS_COMPLETED)
        self.assertIsNotNone(svc.completed_at)

        # 6. 訂單作廢 -> 自動將專業服務標記為已取消
        self.client.force_login(self.manager_user)
        self.client.post(reverse("order-cancel", kwargs={"pk": order.pk}), data={"cancel_reason": "測試作廢"})
        svc.refresh_from_db()
        self.assertEqual(svc.status, CustomerServiceRecord.STATUS_CANCELLED)

        # 7. 手動在客戶頁新增專業服務紀錄（附帶附件）直接生效
        self.client.force_login(self.sales_user)
        manual_file = SimpleUploadedFile("manual_note.txt", b"service log", content_type="text/plain")
        create_resp = self.client.post(
            reverse("service-record-create", kwargs={"customer_pk": self.customer.pk}),
            data={
                "service_no": "SRV-MANUAL-0001",
                "name": "臨時顧問諮詢",
                "description": "遠端架構檢視",
                "service_date": "2026-11-25",
                "status": CustomerServiceRecord.STATUS_IN_PROGRESS,
                "quantity": "1",
                "unit": "式",
                "amount": "5000",
                "note": "客戶臨時加購",
                "attachments": [manual_file],
            },
        )
        self.assertEqual(create_resp.status_code, 302)
        manual_svc = CustomerServiceRecord.objects.get(service_no="SRV-MANUAL-0001")
        self.assertEqual(manual_svc.attachments.count(), 1)
        self.assertEqual(manual_svc.note, "客戶臨時加購")
