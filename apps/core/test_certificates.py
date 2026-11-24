import datetime
import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditLog, SystemSetting, UserProfile
from apps.customers.models import Customer, CustomerFollow, MaintenanceContract
from apps.licenses.models import CustomerLicense, Product

User = get_user_model()


class CertificateExportFeatureTest(TestCase):
    def setUp(self):
        self.temp_media = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.temp_media.name)
        self.override.enable()

        self.admin = User.objects.create_superuser(
            username="cert_admin", password="password123", email="admin@example.com"
        )
        self.viewer = User.objects.create_user(
            username="cert_viewer", password="password123"
        )
        UserProfile.objects.create(
            user=self.viewer,
            role=UserProfile.ROLE_VIEW,
            can_view_crm=True,
            customer_scope=UserProfile.SCOPE_ALL,
        )

        self.scoped_user = User.objects.create_user(
            username="cert_scoped", password="password123"
        )
        UserProfile.objects.create(
            user=self.scoped_user,
            role=UserProfile.ROLE_VIEW,
            can_view_crm=True,
            customer_scope=UserProfile.SCOPE_FOLLOWED,
        )

        self.hw_only = User.objects.create_user(
            username="cert_hw", password="password123"
        )
        UserProfile.objects.create(
            user=self.hw_only,
            role=UserProfile.ROLE_HW_VIEWER,
            can_view_crm=False,
        )

        self.customer = Customer.objects.create(
            code="CUST001",
            name="桃園區公所",
        )
        self.other_customer = Customer.objects.create(
            code="CUST002",
            name="臺灣華歌爾股份有限公司",
        )
        CustomerFollow.objects.create(customer=self.customer, user=self.scoped_user)

        self.product = Product.objects.create(
            code="ATHENAC",
            name="PIXIS AtheNAC™-雅典娜 NAC & IP/MAC 網路安全管理系統-一年使用授權 - 10 U",
            default_unit="套",
        )

        self.sub_license = CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no="L202611002-1",
            license_type=CustomerLicense.TYPE_SUBSCRIPTION,
            quantity=20,
            unit="套",
            start_date=datetime.date(2026, 12, 12),
            end_date=datetime.date(2027, 12, 11),
            is_perpetual=False,
        )
        self.perp_license = CustomerLicense.objects.create(
            customer=self.other_customer,
            product=self.product,
            license_no="L202611099",
            license_type=CustomerLicense.TYPE_PERPETUAL,
            quantity=1000,
            unit="個 MAC",
            is_perpetual=True,
        )

        self.contract_with_lic = MaintenanceContract.objects.create(
            customer=self.other_customer,
            contract_no="W202512007",
            name="年度保固維護合約",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
        )
        self.contract_with_lic.licenses.add(self.perp_license)

        self.contract_no_lic = MaintenanceContract.objects.create(
            customer=self.customer,
            contract_no="W202600001",
            name="獨立設備維護合約",
            start_date=datetime.date(2026, 2, 1),
            end_date=datetime.date(2027, 1, 31),
        )

        self.client = Client()

    def tearDown(self):
        self.override.disable()
        self.temp_media.cleanup()

    def test_license_cert_data_api_subscription_and_perpetual(self):
        self.client.force_login(self.viewer)

        # Subscription license
        resp = self.client.get(
            reverse("cert-data-api", kwargs={"cert_type": "license", "pk": self.sub_license.pk})
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["cert_type"], "license")
        self.assertEqual(data["fields"]["product_name"], self.product.name)
        self.assertEqual(data["fields"]["quantity"], "20套")
        self.assertEqual(data["fields"]["recipient"], "桃園區公所")
        self.assertEqual(data["fields"]["period"], "2026/12/12~2027/12/11")
        self.assertEqual(data["fields"]["cert_no"], "L202611002-1")
        self.assertTrue(data["fields"]["issue_date"].startswith("民國 "))
        self.assertFalse(data["has_custom_bg"])

        # Perpetual license
        resp2 = self.client.get(
            reverse("cert-data-api", kwargs={"cert_type": "license", "pk": self.perp_license.pk})
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["fields"]["period"], "永久授權")

    def test_warranty_cert_data_api_with_and_without_licenses(self):
        self.client.force_login(self.viewer)

        # Contract with linked license
        resp = self.client.get(
            reverse(
                "cert-data-api",
                kwargs={"cert_type": "warranty", "pk": self.contract_with_lic.pk},
            )
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["cert_type"], "warranty")
        self.assertEqual(data["fields"]["product_name"], self.product.name)
        self.assertEqual(data["fields"]["quantity"], "適用 1,000 個 MAC 軟體授權")
        self.assertEqual(data["fields"]["recipient"], "臺灣華歌爾股份有限公司")
        self.assertEqual(data["fields"]["period"], "2026/01/01~2026/12/31")
        self.assertEqual(data["fields"]["cert_no"], "W202512007")
        self.assertEqual(data["fields"]["issue_date"], "")

        # Contract without linked license falls back to contract name
        resp2 = self.client.get(
            reverse(
                "cert-data-api",
                kwargs={"cert_type": "warranty", "pk": self.contract_no_lic.pk},
            )
        )
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertEqual(data2["fields"]["product_name"], "獨立設備維護合約")
        self.assertEqual(data2["fields"]["quantity"], "")

    def test_permissions_and_customer_scope(self):
        # hw_viewer blocked from CRM certificate data
        self.client.force_login(self.hw_only)
        resp = self.client.get(
            reverse("cert-data-api", kwargs={"cert_type": "license", "pk": self.sub_license.pk})
        )
        self.assertEqual(resp.status_code, 403)

        # Scoped user can access followed customer, blocked from unfollowed customer
        self.client.force_login(self.scoped_user)
        ok_resp = self.client.get(
            reverse("cert-data-api", kwargs={"cert_type": "license", "pk": self.sub_license.pk})
        )
        self.assertEqual(ok_resp.status_code, 200)

        denied_resp = self.client.get(
            reverse("cert-data-api", kwargs={"cert_type": "license", "pk": self.perp_license.pk})
        )
        self.assertEqual(denied_resp.status_code, 403)

    def test_audit_log_api(self):
        self.client.force_login(self.viewer)
        payload = {
            "cert_type": "license",
            "object_id": self.sub_license.pk,
            "export_format": "PNG",
            "cert_no": "L202611002-1",
            "recipient": "桃園區公所",
            "product_name": self.product.name,
        }
        resp = self.client.post(
            reverse("cert-audit-api"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))

        log = AuditLog.objects.filter(
            user=self.viewer,
            action="匯出軟體授權證書",
            target_identifier="L202611002-1",
        ).first()
        self.assertIsNotNone(log)
        self.assertIn("格式: PNG", log.detail)
        self.assertIn("桃園區公所", log.detail)

    def test_custom_template_upload_serve_and_reset(self):
        # Non-admin denied
        self.client.force_login(self.viewer)
        resp = self.client.get(reverse("cert-template-settings"))
        self.assertEqual(resp.status_code, 302)

        # Admin upload invalid extension rejected
        self.client.force_login(self.admin)
        bad_file = SimpleUploadedFile("bad.exe", b"not-an-image", content_type="application/octet-stream")
        resp_bad = self.client.post(
            reverse("cert-template-settings"),
            {"action": "upload_license", "license_cert_bg": bad_file},
            follow=True,
        )
        self.assertEqual(resp_bad.status_code, 200)
        self.assertContains(resp_bad, "不支援的檔案格式")

        # Admin upload valid SVG
        svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="70"></svg>'
        good_file = SimpleUploadedFile("custom_bg.svg", svg_bytes, content_type="image/svg+xml")
        resp_ok = self.client.post(
            reverse("cert-template-settings"),
            {"action": "upload_license", "license_cert_bg": good_file},
            follow=True,
        )
        self.assertEqual(resp_ok.status_code, 200)
        self.assertContains(resp_ok, "已成功更新")

        # Data API now reports has_custom_bg=True and bg serve returns 200
        api_resp = self.client.get(
            reverse("cert-data-api", kwargs={"cert_type": "license", "pk": self.sub_license.pk})
        )
        self.assertTrue(api_resp.json()["has_custom_bg"])
        bg_resp = self.client.get(reverse("cert-bg-serve", kwargs={"cert_type": "license"}))
        self.assertEqual(bg_resp.status_code, 200)
        bg_resp.close()

        # Reset back to default
        resp_reset = self.client.post(
            reverse("cert-template-settings"),
            {"action": "reset_license"},
            follow=True,
        )
        self.assertEqual(resp_reset.status_code, 200)
        self.assertContains(resp_reset, "已將「軟體授權證書」還原為系統內建預設底圖")
        bg_resp_after = self.client.get(reverse("cert-bg-serve", kwargs={"cert_type": "license"}))
        self.assertEqual(bg_resp_after.status_code, 404)

    def test_entry_point_pages_render_export_buttons_and_modal(self):
        self.client.force_login(self.viewer)

        for url_name, kwargs, expected_attr in [
            ("license-list", {}, 'data-cert-export="license"'),
            ("license-detail", {"pk": self.sub_license.pk}, 'data-cert-export="license"'),
            ("contract-list", {}, 'data-cert-export="warranty"'),
            ("customer-detail", {"pk": self.customer.pk}, 'data-cert-export="license"'),
            ("customer-detail", {"pk": self.customer.pk}, 'data-cert-export="warranty"'),
        ]:
            resp = self.client.get(reverse(url_name, kwargs=kwargs))
            self.assertEqual(resp.status_code, 200, f"Failed rendering {url_name}")
            self.assertContains(resp, expected_attr)
            self.assertContains(resp, 'id="cert-export-modal"')
