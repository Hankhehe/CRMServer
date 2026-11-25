from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.numbering import (
    generate_contract_no,
    generate_license_no,
    generate_order_no,
    generate_service_no,
)
from apps.customers.forms import CustomerServiceRecordForm, MaintenanceContractForm
from apps.customers.models import Customer, CustomerServiceRecord, MaintenanceContract
from apps.licenses.forms import CustomerLicenseForm
from apps.licenses.models import CustomerLicense, Product
from apps.orders.forms import OrderForm
from apps.orders.models import Order

User = get_user_model()
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


class DocumentNumberingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="num_admin",
            email="num_admin@example.com",
            password="Password123!",
        )
        self.customer = Customer.objects.create(code="CUST-NUM-01", name="編號測試客戶")
        self.product = Product.objects.create(code="PROD-NUM-01", name="流量分析軟體")
        self.today_str = timezone.now().astimezone(TAIPEI_TZ).strftime("%Y%m%d")

    def test_all_four_generators_follow_prefix_date_sequence(self):
        self.assertEqual(generate_contract_no(), f"W-{self.today_str}-0001")
        self.assertEqual(generate_license_no(), f"L-{self.today_str}-0001")
        self.assertEqual(generate_order_no(), f"SO-{self.today_str}-0001")
        self.assertEqual(generate_service_no(), f"SRV-{self.today_str}-0001")

        # 建立一筆後應自動遞增為 0002
        MaintenanceContract.objects.create(
            customer=self.customer, contract_no=f"W-{self.today_str}-0001", name="維護單1"
        )
        CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no=f"L-{self.today_str}-0001",
            quantity=1,
            is_perpetual=True,
        )
        Order.objects.create(
            customer=self.customer,
            order_no=f"SO-{self.today_str}-0001",
            title="測試訂單1",
            created_by=self.user,
            salesperson=self.user,
        )
        CustomerServiceRecord.objects.create(
            customer=self.customer,
            service_no=f"SRV-{self.today_str}-0001",
            name="專業服務1",
            created_by=self.user,
        )

        self.assertEqual(generate_contract_no(), f"W-{self.today_str}-0002")
        self.assertEqual(generate_license_no(), f"L-{self.today_str}-0002")
        self.assertEqual(generate_order_no(), f"SO-{self.today_str}-0002")
        self.assertEqual(generate_service_no(), f"SRV-{self.today_str}-0002")

    def test_manual_create_views_prefill_auto_numbers(self):
        self.client.force_login(self.user)

        resp_contract = self.client.get(reverse("contract-create"))
        self.assertEqual(resp_contract.status_code, 200)
        self.assertEqual(
            resp_contract.context["form"].initial.get("contract_no"),
            f"W-{self.today_str}-0001",
        )

        resp_license = self.client.get(reverse("license-create"))
        self.assertEqual(resp_license.status_code, 200)
        self.assertEqual(
            resp_license.context["form"].initial.get("license_no"),
            f"L-{self.today_str}-0001",
        )

        resp_order = self.client.get(reverse("order-create"))
        self.assertEqual(resp_order.status_code, 200)
        self.assertContains(resp_order, f"SO-{self.today_str}-0001")

        resp_svc = self.client.get(
            reverse("service-record-create", kwargs={"customer_pk": self.customer.pk})
        )
        self.assertEqual(resp_svc.status_code, 200)
        self.assertEqual(
            resp_svc.context["form"].initial.get("service_no"),
            f"SRV-{self.today_str}-0001",
        )

    def test_duplicate_collision_suggests_next_available_number(self):
        # 1. 維護合約撞號
        c1 = MaintenanceContract.objects.create(
            customer=self.customer, contract_no=f"W-{self.today_str}-0001", name="合約1"
        )
        form_c = MaintenanceContractForm(
            data={
                "customer": self.customer.pk,
                "contract_no": c1.contract_no,
                "name": "重複合約",
            },
            customer=self.customer,
        )
        self.assertFalse(form_c.is_valid())
        self.assertIn("contract_no", form_c.errors)
        self.assertIn(f"W-{self.today_str}-0002", form_c.errors["contract_no"][0])

        # 2. 軟體授權書撞號
        l1 = CustomerLicense.objects.create(
            customer=self.customer,
            product=self.product,
            license_no=f"L-{self.today_str}-0001",
            quantity=1,
            is_perpetual=True,
        )
        form_l = CustomerLicenseForm(
            data={
                "customer": self.customer.pk,
                "product": self.product.pk,
                "license_no": l1.license_no,
                "quantity": 1,
                "unit": "台",
                "is_perpetual": True,
            }
        )
        self.assertFalse(form_l.is_valid())
        self.assertIn("license_no", form_l.errors)
        self.assertIn(f"L-{self.today_str}-0002", form_l.errors["license_no"][0])

        # 3. 訂單撞號
        o1 = Order.objects.create(
            customer=self.customer,
            order_no=f"SO-{self.today_str}-0001",
            title="訂單1",
            created_by=self.user,
            salesperson=self.user,
        )
        form_o = OrderForm(
            data={
                "order_no": o1.order_no,
                "customer": self.customer.pk,
                "title": "重複訂單",
                "quote_date": timezone.now().astimezone(TAIPEI_TZ).date().isoformat(),
                "tax_mode": Order.TAX_EXCLUSIVE,
                "discount_amount": 0,
            },
            user=self.user,
        )
        self.assertFalse(form_o.is_valid())
        self.assertIn("order_no", form_o.errors)
        self.assertIn(f"SO-{self.today_str}-0002", form_o.errors["order_no"][0])

        # 4. 專業服務撞號
        s1 = CustomerServiceRecord.objects.create(
            customer=self.customer,
            service_no=f"SRV-{self.today_str}-0001",
            name="服務1",
            created_by=self.user,
        )
        form_s = CustomerServiceRecordForm(
            data={
                "service_no": s1.service_no,
                "name": "重複服務",
                "status": CustomerServiceRecord.STATUS_PENDING,
                "quantity": 1,
                "unit": "式",
            }
        )
        self.assertFalse(form_s.is_valid())
        self.assertIn("service_no", form_s.errors)
        self.assertIn(f"SRV-{self.today_str}-0002", form_s.errors["service_no"][0])

        # 5. 編輯既有單據本身時不應誤判撞號
        form_c_edit = MaintenanceContractForm(
            data={
                "customer": self.customer.pk,
                "contract_no": c1.contract_no,
                "name": "更新名稱",
            },
            instance=c1,
            customer=self.customer,
        )
        self.assertTrue(form_c_edit.is_valid(), form_c_edit.errors)
