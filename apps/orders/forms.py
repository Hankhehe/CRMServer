from django import forms
from django.contrib.auth import get_user_model
from apps.core.models import SystemSetting
from apps.core.permissions import filter_customers_for_user
from apps.customers.models import Customer, ContactPerson
from .models import Order, OrderAttachment, OrderPayment

User = get_user_model()


class CustomerChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"[{obj.code}] {obj.name}"


class SalespersonChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        full_name = obj.get_full_name()
        return f"{full_name} ({obj.username})" if full_name else obj.username


class OrderForm(forms.ModelForm):
    customer = CustomerChoiceField(
        label="採購客戶",
        queryset=Customer.objects.none(),
        widget=forms.Select(attrs={"class": "form-control", "id": "id_customer"}),
    )
    salesperson = SalespersonChoiceField(
        label="負責業務",
        queryset=User.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control", "id": "id_salesperson"}),
    )

    class Meta:
        model = Order
        fields = [
            "order_no",
            "customer",
            "contact_person",
            "contact_name",
            "contact_phone",
            "contact_email",
            "title",
            "quote_date",
            "valid_until",
            "tax_mode",
            "discount_amount",
            "payment_terms",
            "terms_note",
            "internal_note",
            "salesperson",
        ]
        widgets = {
            "order_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: ORD-20261125-0001"}),
            "contact_person": forms.Select(attrs={"class": "form-control", "id": "id_contact_person"}),
            "contact_name": forms.TextInput(attrs={"class": "form-control", "id": "id_contact_name", "placeholder": "聯絡窗口姓名"}),
            "contact_phone": forms.TextInput(attrs={"class": "form-control", "id": "id_contact_phone", "placeholder": "聯絡電話"}),
            "contact_email": forms.EmailInput(attrs={"class": "form-control", "id": "id_contact_email", "placeholder": "電子信箱"}),
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: 2026 年度資安流量探針與授權採購案"}),
            "quote_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "valid_until": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "tax_mode": forms.Select(attrs={"class": "form-control", "id": "id_tax_mode"}),
            "discount_amount": forms.NumberInput(attrs={"class": "form-control", "id": "id_discount_amount", "min": "0", "step": "1"}),
            "payment_terms": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: 驗收後月結 30 天 / 訂金 30% 尾款 70%"}),
            "terms_note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "internal_note": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "僅供公司內部業務/主管/工程師檢視，不會列印於客戶報價單"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        cust_qs = Customer.objects.all().order_by("code")
        if user is not None:
            cust_qs = filter_customers_for_user(user, cust_qs)
        self.fields["customer"].queryset = cust_qs
        self.fields["salesperson"].queryset = User.objects.filter(is_active=True).order_by("username")

        cust_id = None
        if self.data.get("customer"):
            cust_id = self.data.get("customer")
        elif self.instance and self.instance.pk and self.instance.customer_id:
            cust_id = self.instance.customer_id
        elif self.initial.get("customer"):
            c_init = self.initial.get("customer")
            cust_id = c_init.pk if hasattr(c_init, "pk") else c_init

        if cust_id:
            self.fields["contact_person"].queryset = ContactPerson.objects.filter(customer_id=cust_id).order_by("-is_primary", "name")
        else:
            self.fields["contact_person"].queryset = ContactPerson.objects.all().order_by("name")
        self.fields["contact_person"].empty_label = "-- 自行輸入或從客戶聯絡人選取 --"
        self.fields["contact_person"].required = False


class OrderAttachmentForm(forms.ModelForm):
    class Meta:
        model = OrderAttachment
        fields = ["attachment_type", "file", "note"]
        widgets = {
            "attachment_type": forms.Select(attrs={"class": "form-control"}),
            "file": forms.FileInput(attrs={"class": "form-control"}),
            "note": forms.TextInput(attrs={"class": "form-control", "placeholder": "附件說明 (選填)"}),
        }


class OrderPaymentForm(forms.ModelForm):
    class Meta:
        model = OrderPayment
        fields = ["amount", "payment_date", "payment_method", "reference_no", "proof_file", "note"]
        widgets = {
            "amount": forms.NumberInput(attrs={"class": "form-control", "min": "1", "step": "1", "placeholder": "請輸入收款金額"}),
            "payment_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "payment_method": forms.Select(attrs={"class": "form-control"}),
            "reference_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: 匯款帳號後五碼 12345 / 發票號碼"}),
            "proof_file": forms.FileInput(attrs={"class": "form-control"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "收款分期說明或補充備註"}),
        }


class QuotationSettingForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = [
            "seller_company_name",
            "seller_tax_id",
            "seller_phone",
            "seller_address",
            "seller_bank_info",
            "quotation_default_terms",
        ]
        widgets = {
            "seller_company_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: 範例資訊股份有限公司"}),
            "seller_tax_id": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: 12345678"}),
            "seller_phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: (02) 2711-0000"}),
            "seller_address": forms.TextInput(attrs={"class": "form-control", "placeholder": "公司營業登記地址"}),
            "seller_bank_info": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "銀行名稱 / 分行代碼 / 戶名 / 帳號"}),
            "quotation_default_terms": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }
