import datetime
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.customers.models import Customer
from apps.hardware.models import HardwareDevice, HardwareModel, Supplier, InventoryTransaction

User = get_user_model()


class HardwareModelAndInventoryTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="testadmin", email="test@example.com", password="password123"
        )
        self.client = Client()
        self.client.force_login(self.user)

        self.supplier = Supplier.objects.create(name="華碩伺服器", supplier_code="ASUS01")
        self.model = HardwareModel.objects.create(
            name="Hardware-X200",
            port_count=8,
            cpu="Intel Xeon",
            memory="32GB",
            hdd="1TB SSD",
            supplier=self.supplier,
        )
        self.customer = Customer.objects.create(code="CUST-TEST", name="中華電信測試")

    def test_hardware_in_and_auto_transaction(self):
        # 建立硬體設備時應自動建立進貨紀錄
        d = HardwareDevice(
            code="HW-2026-0001",
            model=self.model,
            supplier=self.supplier,
            status=HardwareDevice.STATUS_AVAILABLE,
        )
        d._created_by = self.user
        d.save()

        self.assertEqual(HardwareDevice.objects.filter(code="HW-2026-0001").count(), 1)
        self.assertEqual(d.transactions.count(), 1)
        first_txn = d.transactions.first()
        self.assertEqual(first_txn.type, InventoryTransaction.TYPE_IN)

    def test_hardware_loan_and_return_cycle(self):
        d = HardwareDevice(
            code="HW-2026-0002",
            model=self.model,
            supplier=self.supplier,
            status=HardwareDevice.STATUS_AVAILABLE,
        )
        d._created_by = self.user
        d.save()

        # 借測給客戶
        d.status = HardwareDevice.STATUS_LOANED
        d.customer = self.customer
        d.expected_return_at = datetime.date.today() + datetime.timedelta(days=14)
        d.save()

        d.refresh_from_db()
        self.assertEqual(d.status, HardwareDevice.STATUS_LOANED)
        self.assertEqual(d.customer, self.customer)
        # 應有 2 筆交易: IN 與 LOAN
        self.assertEqual(d.transactions.count(), 2)
        latest_txn = d.transactions.order_by("-occurred_at", "-id").first()
        self.assertEqual(latest_txn.type, InventoryTransaction.TYPE_LOAN)

        # 歸還庫存
        d.status = HardwareDevice.STATUS_AVAILABLE
        d.customer = None
        d.expected_return_at = None
        d.save()

        d.refresh_from_db()
        self.assertEqual(d.status, HardwareDevice.STATUS_AVAILABLE)
        self.assertIsNone(d.customer)
        # 應有 3 筆交易: IN, LOAN, RETURN_LOAN
        self.assertEqual(d.transactions.count(), 3)
        return_txn = d.transactions.order_by("-occurred_at", "-id").first()
        self.assertEqual(return_txn.type, InventoryTransaction.TYPE_RETURN_LOAN)

    def test_hardware_views(self):
        d = HardwareDevice.objects.create(
            code="HW-VIEW-01",
            model=self.model,
            status=HardwareDevice.STATUS_AVAILABLE,
        )
        self.assertEqual(d.effective_supplier, self.supplier)

        resp = self.client.get(reverse("hardware-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "HW-VIEW-01")

        detail_resp = self.client.get(reverse("hardware-detail", args=[d.pk]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "HW-VIEW-01")
        self.assertContains(detail_resp, "Hardware-X200")
        self.assertContains(detail_resp, "華碩伺服器")

    def test_hardware_dynamic_custom_fields_and_override(self):
        # 1. 設定硬體型號自訂擴充規格欄位
        self.model.custom_field_schema = [
            {"key": "power_supply", "label": "電源規格", "type": "text", "default": "雙電源 550W", "show_in_list": False},
            {"key": "rack_unit", "label": "機架高度", "type": "select", "options": ["1U", "2U"], "default": "1U", "show_in_list": True},
        ]
        self.model.save()

        # 2. 測試型號 Meta API
        meta_resp = self.client.get(reverse("api-hardware-model-meta", kwargs={"pk": self.model.pk}))
        self.assertEqual(meta_resp.status_code, 200)
        meta_data = meta_resp.json()
        self.assertEqual(len(meta_data["custom_field_schema"]), 2)
        self.assertEqual(meta_data["custom_field_schema"][0]["key"], "power_supply")

        # 3. 透過表單建立設備並覆寫單一自訂欄位 (rack_unit 改為 2U，power_supply 留空繼承預設)
        post_data = {
            "code": "HW-DYN-001",
            "model": self.model.pk,
            "supplier": self.supplier.pk,
            "status": HardwareDevice.STATUS_AVAILABLE,
            "dyn_power_supply": "",
            "dyn_rack_unit": "2U",
        }
        create_resp = self.client.post(reverse("hardware-create"), data=post_data, follow=True)
        self.assertEqual(create_resp.status_code, 200)

        dev = HardwareDevice.objects.get(code="HW-DYN-001")
        self.assertTrue(dev.is_specs_overridden)
        self.assertEqual(dev.custom_values.get("rack_unit"), "2U")
        self.assertNotIn("power_supply", dev.custom_values)

        specs = {s["key"]: s for s in dev.effective_custom_specs}
        self.assertEqual(specs["power_supply"]["value"], "雙電源 550W")
        self.assertFalse(specs["power_supply"]["is_overridden"])
        self.assertEqual(specs["rack_unit"]["value"], "2U")
        self.assertTrue(specs["rack_unit"]["is_overridden"])

        # 4. 檢視設備詳情頁是否正確渲染自訂規格與覆寫標記
        detail_resp = self.client.get(reverse("hardware-detail", args=[dev.pk]))
        self.assertContains(detail_resp, "電源規格")
        self.assertContains(detail_resp, "雙電源 550W")
        self.assertContains(detail_resp, "機架高度")
        self.assertContains(detail_resp, "2U")

        # 5. 檢視列表頁僅顯示勾選 show_in_list=True 之自訂規格
        list_resp = self.client.get(reverse("hardware-list"))
        self.assertContains(list_resp, "機架高度: 2U")
        self.assertNotContains(list_resp, "電源規格: 雙電源 550W")

    def test_supplier_delete_protection_and_success(self):
        # 建立一個無關聯的獨立供應商
        hp_supplier = Supplier.objects.create(name="HP Enterprise", supplier_code="HPE999")
        
        # 測試 GET 刪除確認頁
        resp = self.client.get(reverse("hardware-supplier-delete", args=[hp_supplier.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "HP Enterprise")
        self.assertContains(resp, "確認刪除")

        # 測試 POST 刪除成功
        del_resp = self.client.post(reverse("hardware-supplier-delete", args=[hp_supplier.pk]), follow=True)
        self.assertEqual(del_resp.status_code, 200)
        self.assertFalse(Supplier.objects.filter(pk=hp_supplier.pk).exists())

        # 測試有型號關聯的供應商無法刪除
        blocked_resp = self.client.post(reverse("hardware-supplier-delete", args=[self.supplier.pk]), follow=True)
        self.assertEqual(blocked_resp.status_code, 200)
        self.assertTrue(Supplier.objects.filter(pk=self.supplier.pk).exists())

    def test_device_return_shipped(self):
        # 建立已出貨給客戶的設備
        d = HardwareDevice.objects.create(
            code="HW-RETURN-01",
            model=self.model,
            supplier=self.supplier,
            status=HardwareDevice.STATUS_SHIPPED,
            customer=self.customer,
        )

        # GET 退貨確認頁
        get_resp = self.client.get(reverse("hardware-device-return", args=[d.pk]))
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, "HW-RETURN-01")
        self.assertContains(get_resp, "確認退貨入庫")

        # POST 辦理出貨退貨
        next_url = reverse("customer-detail", args=[self.customer.pk])
        post_resp = self.client.post(
            reverse("hardware-device-return", args=[d.pk]) + f"?next={next_url}",
            data={"note": "專案結束退貨"},
            follow=True,
        )
        self.assertEqual(post_resp.status_code, 200)
        self.assertRedirects(post_resp, next_url)

        d.refresh_from_db()
        self.assertEqual(d.status, HardwareDevice.STATUS_AVAILABLE)
        self.assertIsNone(d.customer)

        # 檢查異動交易紀錄
        latest_txn = d.transactions.order_by("-occurred_at", "-id").first()
        self.assertEqual(latest_txn.type, InventoryTransaction.TYPE_RETURN_OUT)
        self.assertIn("專案結束退貨", latest_txn.note)

    def test_device_return_loaned(self):
        # 建立借測中的設備
        d = HardwareDevice.objects.create(
            code="HW-LOAN-RETURN-01",
            model=self.model,
            supplier=self.supplier,
            status=HardwareDevice.STATUS_LOANED,
            customer=self.customer,
            expected_return_at=datetime.date.today() + datetime.timedelta(days=7),
        )

        # POST 辦理借測歸還
        next_url = reverse("customer-detail", args=[self.customer.pk])
        post_resp = self.client.post(
            reverse("hardware-device-return", args=[d.pk]),
            data={"note": "借測期滿歸還", "next": next_url},
            follow=True,
        )
        self.assertEqual(post_resp.status_code, 200)
        self.assertRedirects(post_resp, next_url)

        d.refresh_from_db()
        self.assertEqual(d.status, HardwareDevice.STATUS_AVAILABLE)
        self.assertIsNone(d.customer)
        self.assertIsNone(d.expected_return_at)

        latest_txn = d.transactions.order_by("-occurred_at", "-id").first()
        self.assertEqual(latest_txn.type, InventoryTransaction.TYPE_RETURN_LOAN)

    def test_device_create_with_customer_initial_and_next(self):
        # 測試帶 customer 與 next 參數造訪新增設備
        url = reverse("hardware-create") + f"?customer={self.customer.pk}&next=/customers/{self.customer.pk}/"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'value="{self.customer.pk}" selected')

        # 測試 POST 建立新設備並跳回 next
        post_data = {
            "code": "HW-NEW-CUST-01",
            "model": self.model.pk,
            "status": HardwareDevice.STATUS_SHIPPED,
            "customer": self.customer.pk,
            "next": f"/customers/{self.customer.pk}/",
        }
        create_resp = self.client.post(reverse("hardware-create"), data=post_data, follow=True)
        self.assertEqual(create_resp.status_code, 200)
        self.assertRedirects(create_resp, f"/customers/{self.customer.pk}/")

        new_dev = HardwareDevice.objects.get(code="HW-NEW-CUST-01")
        self.assertEqual(new_dev.customer, self.customer)
        self.assertEqual(new_dev.status, HardwareDevice.STATUS_SHIPPED)

    def test_customer_detail_hardware_buttons_and_modal(self):
        # 建立一台出貨設備
        HardwareDevice.objects.create(
            code="HW-CUST-VIEW-01",
            model=self.model,
            supplier=self.supplier,
            status=HardwareDevice.STATUS_SHIPPED,
            customer=self.customer,
        )
        resp = self.client.get(reverse("customer-detail", args=[self.customer.pk]))
        self.assertEqual(resp.status_code, 200)
        # 檢查不應有新增硬體設備按鈕
        self.assertNotContains(resp, "新增硬體設備")
        # 檢查是否有庫存出貨按鈕
        self.assertContains(resp, "庫存出貨")
        txn_url = reverse("hardware-transaction-create")
        self.assertContains(resp, f"{txn_url}?customer={self.customer.pk}&type=OUT&next=")
        # 檢查退貨按鈕與退貨 Modal
        self.assertContains(resp, "openReturnDeviceModal")
        self.assertContains(resp, "returnDeviceModal")

    def test_hardware_model_form_custom_field_schema_validation(self):
        from apps.hardware.forms import HardwareModelForm
        import json

        # 1. 正常提交 UI 序列化之硬體自訂規格欄位
        valid_schema = [
            {"key": "power_supply", "label": "電源供應器", "type": "text", "options": [], "default": "雙電源 550W", "required": False},
            {"key": "fiber_ports", "label": "光纖埠數", "type": "select", "options": ["2x 10G", "4x 10G"], "default": "2x 10G", "required": True},
        ]
        form = HardwareModelForm(data={
            "model_code": "MD-TEST-FORM-01",
            "name": "測試規格型號",
            "port_count": 8,
            "custom_field_schema_raw": json.dumps(valid_schema),
        })
        self.assertTrue(form.is_valid(), form.errors)
        hw_m = form.save()
        self.assertEqual(len(hw_m.custom_field_schema), 2)
        self.assertEqual(hw_m.custom_field_schema[1]["key"], "fiber_ports")
        self.assertEqual(hw_m.custom_field_schema[1]["options"], ["2x 10G", "4x 10G"])

        # 2. 測試重複 Key 應報錯
        dup_schema = [
            {"key": "slot_type", "label": "插槽規格 1", "type": "text"},
            {"key": "slot_type", "label": "插槽規格 2", "type": "text"},
        ]
        dup_form = HardwareModelForm(data={
            "model_code": "MD-DUP-01",
            "name": "重複代碼型號",
            "custom_field_schema_raw": json.dumps(dup_schema),
        })
        self.assertFalse(dup_form.is_valid())
        self.assertIn("custom_field_schema_raw", dup_form.errors)
        self.assertIn("重複定義", dup_form.errors["custom_field_schema_raw"][0])


    def test_hardware_datetime_custom_field(self):
        self.model.custom_field_schema = [
            {"key": "inspect_at", "label": "檢驗時間", "type": "datetime", "default": "2026-04-01 09:00", "show_in_list": True},
        ]
        self.model.save()

        post_data = {
            "code": "HW-DT-001",
            "model": self.model.pk,
            "supplier": self.supplier.pk,
            "status": HardwareDevice.STATUS_AVAILABLE,
            "dyn_inspect_at": "2026-06-15T14:30",
        }
        create_resp = self.client.post(reverse("hardware-create"), data=post_data, follow=True)
        self.assertEqual(create_resp.status_code, 200)

        dev = HardwareDevice.objects.get(code="HW-DT-001")
        self.assertEqual(dev.custom_values.get("inspect_at"), "2026-06-15 14:30")
        specs = {s["key"]: s for s in dev.effective_custom_specs}
        self.assertEqual(specs["inspect_at"]["value"], "2026-06-15 14:30")
        self.assertTrue(specs["inspect_at"]["is_overridden"])
