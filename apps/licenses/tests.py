import datetime
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.customers.models import Customer
from apps.hardware.models import HardwareDevice as Probe, HardwareModel as ProbeModelDetail, Supplier
from apps.licenses.models import (
    Product,
    CustomerLicense,
)

User = get_user_model()


class LicenseAndProductTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username="admin_test", password="adminpassword", email="admin@example.com"
        )
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(
            code="CUST-TEST-001",
            name="測試企業股份有限公司",
            tax_id="12345678",
            contact="張大明",
            phone="0912345678",
            email="test@example.com",
        )

        self.product = Product.objects.create(
            code="FLOW-PROBE-CORE",
            name="資安流量探測與分析核心系統",
            category="資安軟體",
            default_unit="台",
            custom_field_schema=[
                {
                    "key": "max_bandwidth_gbps",
                    "label": "最大處理頻寬 (Gbps)",
                    "type": "number",
                    "default": 10,
                    "show_in_list": True,
                },
                {
                    "key": "ha_mode",
                    "label": "HA 高可用性架構",
                    "type": "select",
                    "options": ["單機模式", "Active-Standby"],
                    "default": "單機模式",
                    "show_in_list": False,
                },
                {
                    "key": "enabled_modules",
                    "label": "啟用模組",
                    "type": "multiselect",
                    "options": ["DDoS 異常流量防護", "SSL 深度解密檢測"],
                    "default": "",
                    "show_in_list": True,
                },
            ],
            is_active=True,
        )

        self.supplier = Supplier.objects.create(
            supplier_code="SUP-TEST", name="原廠硬體供應商"
        )
        self.probe_model = ProbeModelDetail.objects.create(
            model_code="PB-MD-01",
            name="Probe 10G-Rack",
            port_count=4,
            cpu="Xeon 8-Core",
            memory="32GB",
            hdd="1TB SSD",
            supplier=self.supplier,
        )

    def test_product_creation(self):
        self.assertEqual(self.product.code, "FLOW-PROBE-CORE")
        self.assertEqual(len(self.product.custom_field_schema), 3)

    def test_customer_license_lifecycle_and_quota(self):
        # 建立一份授權 5 台 Probe 的永久授權書
        lic = CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no="LIC-2026-TEST-001",
            license_type=CustomerLicense.TYPE_PERPETUAL,
            quantity=5,
            unit="台",
            is_perpetual=True,
            license_key="ABCD-EFGH-1234-5678",
            custom_values={
                "max_bandwidth_gbps": 40,
                "ha_mode": "Active-Standby",
                "enabled_modules": ["DDoS 異常流量防護"],
            },
            status=CustomerLicense.STATUS_ACTIVE,
        )

        self.assertTrue(lic.is_perpetual)
        self.assertFalse(lic.is_expired)
        self.assertEqual(lic.quantity, 5)
        self.assertEqual(lic.status_display, "永久有效")
        self.assertEqual(len(lic.custom_specs_display), 3)
        self.assertEqual(len(lic.list_custom_specs), 2)

    def test_subscription_expiry_calculations(self):
        today = timezone.localdate()
        # 10 天後到期之訂閱授權
        lic_expiring = CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no="LIC-SUB-EXPIRING",
            license_type=CustomerLicense.TYPE_SUBSCRIPTION,
            quantity=2,
            unit="台",
            start_date=today - datetime.timedelta(days=350),
            end_date=today + datetime.timedelta(days=10),
            is_perpetual=False,
            status=CustomerLicense.STATUS_ACTIVE,
        )
        self.assertTrue(lic_expiring.is_expiring_soon)
        self.assertFalse(lic_expiring.is_expired)
        self.assertEqual(lic_expiring.days_remaining, 10)
        self.assertIn("即將到期", lic_expiring.status_display)

        # 逾期授權
        lic_overdue = CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no="LIC-SUB-OVERDUE",
            license_type=CustomerLicense.TYPE_SUBSCRIPTION,
            quantity=1,
            unit="台",
            start_date=today - datetime.timedelta(days=400),
            end_date=today - datetime.timedelta(days=5),
            is_perpetual=False,
        )
        self.assertTrue(lic_overdue.is_expired)
        self.assertEqual(lic_overdue.status, CustomerLicense.STATUS_EXPIRED)
        self.assertIn("已過期", lic_overdue.status_display)

        # 待生效授權
        lic_pending = CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no="LIC-SUB-PENDING",
            license_type=CustomerLicense.TYPE_SUBSCRIPTION,
            quantity=1,
            unit="台",
            start_date=today + datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=375),
            is_perpetual=False,
        )
        self.assertEqual(lic_pending.status, CustomerLicense.STATUS_PENDING)
        self.assertIn("待生效", lic_pending.status_display)

        # 已停用授權 (即使在有效期內，勾選停用一律為已停用)
        lic_disabled = CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no="LIC-SUB-DISABLED",
            license_type=CustomerLicense.TYPE_SUBSCRIPTION,
            quantity=1,
            unit="台",
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=300),
            is_perpetual=False,
            is_disabled=True,
        )
        self.assertEqual(lic_disabled.status, CustomerLicense.STATUS_SUSPENDED)
        self.assertEqual(lic_disabled.status_display, "已停用")

    def test_license_views_and_dynamic_api(self):
        # Test product meta API
        resp = self.client.get(reverse("product-meta-api", kwargs={"pk": self.product.pk}))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["code"], "FLOW-PROBE-CORE")
        self.assertEqual(len(data["custom_field_schema"]), 3)

        # Test license create view GET
        resp = self.client.get(reverse("license-create") + f"?customer={self.customer.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "測試企業股份有限公司")

        # Test license create view POST (Perpetual)
        post_data = {
            "customer": self.customer.pk,
            "product": self.product.pk,
            "license_no": "LIC-POST-2026",
            "quantity": 3,
            "unit": "台",
            "is_perpetual": "on",
            "dyn_max_bandwidth_gbps": "20",
            "dyn_ha_mode": "單機模式",
            "dyn_enabled_modules": ["DDoS 異常流量防護"],
        }
        resp = self.client.post(reverse("license-create"), data=post_data)
        self.assertEqual(resp.status_code, 302)

        new_lic = CustomerLicense.objects.get(license_no="LIC-POST-2026")
        self.assertEqual(new_lic.quantity, 3)
        self.assertTrue(new_lic.is_perpetual)
        self.assertEqual(new_lic.license_type, CustomerLicense.TYPE_PERPETUAL)
        self.assertIsNone(new_lic.end_date)
        self.assertEqual(new_lic.custom_values.get("max_bandwidth_gbps"), 20.0)
        self.assertEqual(new_lic.custom_values.get("enabled_modules"), ["DDoS 異常流量防護"])

        # Test license list view
        resp = self.client.get(reverse("license-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "LIC-POST-2026")
        self.assertContains(resp, "最大處理頻寬 (Gbps): 20.0")
        self.assertNotContains(resp, "HA 高可用性架構: 單機模式")

        # Test license detail view
        resp = self.client.get(reverse("license-detail", kwargs={"pk": new_lic.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "LIC-POST-2026")
        self.assertContains(resp, "DDoS 異常流量防護")
        self.assertContains(resp, "單機模式")

        # Test customer detail view displays the license
        resp = self.client.get(reverse("customer-detail", kwargs={"pk": self.customer.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "LIC-POST-2026")
        self.assertContains(resp, "最大處理頻寬 (Gbps): 20.0")

    def test_customer_license_delete_with_next(self):
        lic = CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no="LIC-TO-DELETE-001",
            license_type=CustomerLicense.TYPE_PERPETUAL,
            quantity=1,
            unit="台",
        )
        # 測試在客戶 Detail 頁面看到刪除連結
        resp = self.client.get(reverse("customer-detail", kwargs={"pk": self.customer.pk}))
        self.assertContains(resp, f"/licenses/{lic.pk}/delete/?next=")

        # 測試 POST 刪除並跳回 next (客戶 Detail)
        next_url = reverse("customer-detail", kwargs={"pk": self.customer.pk})
        del_resp = self.client.post(
            reverse("license-delete", kwargs={"pk": lic.pk}) + f"?next={next_url}",
            follow=True,
        )
        self.assertEqual(del_resp.status_code, 200)
        self.assertFalse(CustomerLicense.objects.filter(pk=lic.pk).exists())
        self.assertRedirects(del_resp, next_url)

    def test_product_form_custom_field_schema_validation(self):
        from apps.licenses.forms import ProductForm
        import json

        # 1. 正常提交 UI 序列化之自訂規格欄位
        valid_schema = [
            {"key": "max_conns", "label": "最大連線數", "type": "number", "options": [], "default": "5000", "required": True},
            {"key": "deploy_tier", "label": "部署層級", "type": "select", "options": ["標準版", "企業版"], "default": "標準版", "required": False},
            {"key": "activated_at", "label": "開通時間", "type": "datetime", "options": [], "default": "2026-05-01T10:00", "required": False},
        ]
        form = ProductForm(data={
            "code": "PROD-VALID-01",
            "name": "有效規格軟體",
            "category": "資安軟體",
            "default_unit": "套",
            "is_active": True,
            "custom_field_schema_raw": json.dumps(valid_schema),
        })
        self.assertTrue(form.is_valid(), form.errors)
        prod = form.save()
        self.assertEqual(len(prod.custom_field_schema), 3)
        self.assertEqual(prod.custom_field_schema[0]["key"], "max_conns")
        self.assertTrue(prod.custom_field_schema[0]["required"])
        self.assertEqual(prod.custom_field_schema[2]["type"], "datetime")
        self.assertEqual(prod.custom_field_schema[2]["default"], "2026-05-01 10:00")

        # 2. 測試重複 Key 應報錯
        dup_schema = [
            {"key": "dup_key", "label": "欄位一", "type": "text"},
            {"key": "dup_key", "label": "欄位二", "type": "text"},
        ]
        dup_form = ProductForm(data={
            "code": "PROD-DUP-01",
            "name": "重複代碼軟體",
            "default_unit": "套",
            "custom_field_schema_raw": json.dumps(dup_schema),
        })
        self.assertFalse(dup_form.is_valid())
        self.assertIn("custom_field_schema_raw", dup_form.errors)
        self.assertIn("重複定義", dup_form.errors["custom_field_schema_raw"][0])

        # 3. 測試非英數字底線 Key 格式錯誤應報錯
        invalid_key_schema = [
            {"key": "invalid-key space!", "label": "特殊字元", "type": "text"},
        ]
        inv_form = ProductForm(data={
            "code": "PROD-INV-01",
            "name": "錯誤代碼軟體",
            "default_unit": "套",
            "custom_field_schema_raw": json.dumps(invalid_key_schema),
        })
        self.assertFalse(inv_form.is_valid())
        self.assertIn("格式不正確", inv_form.errors["custom_field_schema_raw"][0])
