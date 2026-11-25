import json
import re
from django import forms
from apps.core.models import SystemSetting
from .models import (
    Customer,
    ContactPerson,
    CustomerInfoSection,
    MaintenanceContract,
    CustomerActivityLog,
    ContractProbeReplacement,
    CustomerServiceRecord,
)
from apps.licenses.models import CustomerLicense
from apps.hardware.models import HardwareDevice, HardwareDevice as Probe


class CustomerCustomFieldSchemaForm(forms.ModelForm):
    custom_field_schema_raw = forms.CharField(
        label="客戶全域自訂欄位結構",
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = SystemSetting
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.customer_custom_field_schema:
            self.fields["custom_field_schema_raw"].initial = json.dumps(
                self.instance.customer_custom_field_schema, ensure_ascii=False
            )

    def clean_custom_field_schema_raw(self):
        raw = self.cleaned_data.get("custom_field_schema_raw", "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise forms.ValidationError("自訂欄位結構必須是陣列格式")
            seen_keys = set()
            cleaned_list = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key", "")).strip()
                label = str(item.get("label", "")).strip()
                if not key and not label:
                    continue
                if not key:
                    raise forms.ValidationError(f"欄位「{label}」缺少代碼 (Key)")
                if not label:
                    raise forms.ValidationError(f"代碼「{key}」缺少顯示名稱 (Label)")
                if not re.match(r"^[a-zA-Z0-9_]+$", key):
                    raise forms.ValidationError(f"代碼「{key}」格式不正確，僅能包含英文字母、數字與底線")
                if key in seen_keys:
                    raise forms.ValidationError(f"代碼「{key}」重複定義，請確保每個欄位代碼唯一")
                seen_keys.add(key)

                f_type = item.get("type", "text")
                if f_type not in ("text", "number", "select", "multiselect", "boolean", "datetime"):
                    f_type = "text"

                options = item.get("options", [])
                if isinstance(options, str):
                    options = [opt.strip() for opt in options.split(",") if opt.strip()]
                elif isinstance(options, list):
                    options = [str(opt).strip() for opt in options if str(opt).strip()]
                else:
                    options = []

                default_val = item.get("default", "")
                if f_type == "datetime" and isinstance(default_val, str) and default_val.strip():
                    default_val = default_val.strip().replace("T", " ")[:16]

                cleaned_list.append({
                    "key": key,
                    "label": label,
                    "type": f_type,
                    "options": options if f_type in ("select", "multiselect") else [],
                    "default": default_val,
                    "required": bool(item.get("required", False)),
                    "show_in_list": bool(item.get("show_in_list", False)),
                })
            return cleaned_list
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f"欄位結構解析錯誤: {e}")

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.customer_custom_field_schema = self.cleaned_data.get("custom_field_schema_raw", [])
        if commit:
            instance.save()
        return instance


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "code",
            "name",
            "tax_id",
            "parent",
            "contact",
            "phone",
            "email",
            "address",
            "note",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "統一編號或客戶代號 (唯一值)"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "公司或組織名稱"}),
            "tax_id": forms.TextInput(attrs={"class": "form-control", "placeholder": "8 碼統編 (選填)"}),
            "parent": forms.Select(attrs={"class": "form-control", "id": "id_parent"}),
            "contact": forms.TextInput(attrs={"class": "form-control", "placeholder": "主要窗口姓名"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "公司電話 / 窗口電話"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "聯絡 Email"}),
            "address": forms.TextInput(attrs={"class": "form-control", "placeholder": "公司地址"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "客戶備註說明"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["parent"].empty_label = "-- 無上層客戶 (獨立客戶 / 最頂層代理商) --"
        self.fields["parent"].required = False

        parent_qs = Customer.objects.all().order_by("code")
        if user is not None:
            from apps.core.permissions import filter_customers_for_user
            from django.db.models import Q
            scoped_qs = filter_customers_for_user(user, parent_qs)
            if self.instance and self.instance.pk and self.instance.parent_id:
                parent_qs = Customer.objects.filter(
                    Q(pk__in=scoped_qs.values("pk")) | Q(pk=self.instance.parent_id)
                ).order_by("code")
            else:
                parent_qs = scoped_qs.order_by("code")

        if self.instance and self.instance.pk:
            invalid_ids = self.instance.get_descendant_ids() | {self.instance.pk}
            parent_qs = parent_qs.exclude(pk__in=invalid_ids)

        self.fields["parent"].queryset = parent_qs
        self._custom_schema = SystemSetting.get_settings().customer_custom_field_schema or []
        self._attach_dynamic_fields()

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        if not parent:
            return None
        if self.instance and self.instance.pk:
            if parent.pk == self.instance.pk:
                raise forms.ValidationError("上層客戶不可指向自己。")
            descendant_ids = self.instance.get_descendant_ids()
            if parent.pk in descendant_ids:
                raise forms.ValidationError(
                    f"不可將下層客戶「{parent.name} ({parent.code})」設為上層客戶，這會造成上下游循環指向。"
                )
        return parent

    def _attach_dynamic_fields(self):
        is_new = self.instance.pk is None
        custom_vals = (self.instance.custom_values or {}) if not is_new else {}
        for item in self._custom_schema:
            key = item.get("key")
            if not key:
                continue
            field_name = f"dyn_{key}"
            label = item.get("label", key)
            f_type = item.get("type", "text")
            required = bool(item.get("required", False))
            default_val = item.get("default", "")
            initial_val = default_val if is_new else custom_vals.get(key, "")

            if f_type == "number":
                self.fields[field_name] = forms.DecimalField(
                    label=label,
                    required=required,
                    initial=initial_val if initial_val != "" else None,
                    widget=forms.NumberInput(attrs={"class": "form-control", "step": "any"}),
                )
            elif f_type == "select":
                opts = item.get("options", [])
                choices = [("", "-- 請選擇 --")] + [(opt, opt) for opt in opts]
                self.fields[field_name] = forms.ChoiceField(
                    label=label,
                    choices=choices,
                    required=required,
                    initial=initial_val,
                    widget=forms.Select(attrs={"class": "form-control"}),
                )
            elif f_type == "multiselect":
                opts = item.get("options", [])
                choices = [(opt, opt) for opt in opts]
                if isinstance(initial_val, str):
                    init_list = [s.strip() for s in initial_val.split(",") if s.strip()]
                elif isinstance(initial_val, list):
                    init_list = [str(s).strip() for s in initial_val if str(s).strip()]
                else:
                    init_list = []
                self.fields[field_name] = forms.MultipleChoiceField(
                    label=label,
                    choices=choices,
                    required=required,
                    initial=init_list,
                    widget=forms.CheckboxSelectMultiple(attrs={"class": "dyn-multiselect-checkbox"}),
                )
            elif f_type == "boolean":
                init_bool = initial_val in (True, "true", "True", 1, "1", "是")
                self.fields[field_name] = forms.BooleanField(
                    label=label,
                    required=required,
                    initial=init_bool,
                    widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;"}),
                )
            elif f_type == "datetime":
                init_dt_str = str(initial_val or "").strip()
                if init_dt_str and " " in init_dt_str:
                    init_dt_str = init_dt_str.replace(" ", "T")[:16]
                self.fields[field_name] = forms.CharField(
                    label=label,
                    required=required,
                    initial=init_dt_str,
                    widget=forms.DateTimeInput(
                        format="%Y-%m-%dT%H:%M",
                        attrs={"type": "datetime-local", "class": "form-control"},
                    ),
                )
            else:
                self.fields[field_name] = forms.CharField(
                    label=label,
                    required=required,
                    initial=initial_val,
                    widget=forms.TextInput(attrs={"class": "form-control"}),
                )

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip()
        if not code:
            raise forms.ValidationError("客戶代號不能為空白")
        qs = Customer.objects.filter(code__iexact=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                f"客戶代號 '{code}' 已存在於系統中。若您無法於名冊中查閱此客戶，請洽系統管理員將您加入該客戶的關注名單，或使用其他代號。"
            )
        return code

    def save(self, commit=True):
        instance = super().save(commit=False)
        schema_map = {item.get("key"): item for item in self._custom_schema if item.get("key")}
        # 保留不在目前 schema 中的舊 custom_values Key，以免意外遺失歷史資料
        existing_custom = dict(instance.custom_values or {})
        for k in list(schema_map.keys()):
            existing_custom.pop(k, None)

        for key, value in self.cleaned_data.items():
            if key.startswith("dyn_"):
                orig_key = key[4:]
                f_type = schema_map.get(orig_key, {}).get("type", "text")
                if f_type != "boolean" and (value is None or value == "" or value == []):
                    continue
                if hasattr(value, "as_tuple"):
                    num_f = float(value)
                    value = int(num_f) if num_f.is_integer() else num_f
                elif f_type == "datetime" and isinstance(value, str):
                    value = value.strip().replace("T", " ")[:16]
                existing_custom[orig_key] = value

        instance.custom_values = existing_custom
        if commit:
            instance.save()
        return instance


class ContactPersonForm(forms.ModelForm):
    class Meta:
        model = ContactPerson
        fields = ["customer", "name", "title", "phone", "email", "is_primary", "note"]
        widgets = {
            "customer": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "聯絡人姓名"}),
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "職稱或部門 (例如: IT 主管、採購經理)"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "手機或分機號碼"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "聯絡 Email"}),
            "is_primary": forms.CheckboxInput(attrs={"style": "margin-right: 8px;"}),
            "note": forms.TextInput(attrs={"class": "form-control", "placeholder": "備註說明"}),
        }


class MaintenanceContractForm(forms.ModelForm):
    class Meta:
        model = MaintenanceContract
        fields = [
            "customer",
            "contract_no",
            "name",
            "start_date",
            "end_date",
            "is_terminated",
            "amount",
            "sla_level",
            "licenses",
            "probes",
            "note",
        ]
        widgets = {
            "customer": forms.Select(attrs={"class": "form-control", "id": "id_customer"}),
            "contract_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: MC-2026-001"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "合約全名 (選填)"}),
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control", "id": "id_start_date"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control", "id": "id_end_date"}),
            "is_terminated": forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_is_terminated"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "placeholder": "金額 (NTD)"}),
            "sla_level": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: 8x5 次日到府、24x7 4小時抵達"}),
            "licenses": forms.CheckboxSelectMultiple(),
            "probes": forms.CheckboxSelectMultiple(),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "備註說明"}),
        }

    def __init__(self, *args, **kwargs):
        customer = kwargs.pop("customer", None)
        super().__init__(*args, **kwargs)

        cust = customer
        if not cust and self.instance and self.instance.pk and self.instance.customer_id:
            cust = self.instance.customer
        elif not cust and "customer" in self.initial:
            cust = self.initial["customer"]

        if self.instance and self.instance.pk and self.instance.status == MaintenanceContract.STATUS_TERMINATED:
            self.initial["is_terminated"] = True

        if cust:
            self.fields["licenses"].queryset = CustomerLicense.objects.filter(customer=cust).select_related("product")
            self.fields["probes"].queryset = Probe.objects.filter(
                customer=cust, status__in=[Probe.STATUS_SHIPPED, Probe.STATUS_LOANED], is_deleted=False
            ).select_related("model")
        elif self.is_bound and self.data.get("customer"):
            try:
                selected_cust_id = int(self.data.get("customer"))
                self.fields["licenses"].queryset = CustomerLicense.objects.filter(customer_id=selected_cust_id).select_related("product")
                self.fields["probes"].queryset = Probe.objects.filter(
                    customer_id=selected_cust_id, status__in=[Probe.STATUS_SHIPPED, Probe.STATUS_LOANED], is_deleted=False
                ).select_related("model")
            except (ValueError, TypeError):
                self.fields["licenses"].queryset = CustomerLicense.objects.none()
                self.fields["probes"].queryset = Probe.objects.none()
        else:
            self.fields["licenses"].queryset = CustomerLicense.objects.none()
            self.fields["probes"].queryset = Probe.objects.none()

        self.fields["licenses"].required = False
        self.fields["probes"].required = False

    def clean(self):
        cleaned_data = super().clean()
        customer = cleaned_data.get("customer")
        licenses = cleaned_data.get("licenses")
        probes = cleaned_data.get("probes")

        if customer:
            if licenses:
                invalid_lic = [lic.license_no for lic in licenses if lic.customer_id != customer.id]
                if invalid_lic:
                    self.add_error("licenses", f"所選授權書 ({', '.join(invalid_lic)}) 不屬於客戶 {customer.name}！")
            if probes:
                invalid_probes = [p.code for p in probes if p.customer_id != customer.id]
                if invalid_probes:
                    self.add_error("probes", f"所選設備 ({', '.join(invalid_probes)}) 不屬於客戶 {customer.name}！")
        return cleaned_data


class ContractProbeReplacementForm(forms.Form):
    action_type = forms.ChoiceField(
        choices=ContractProbeReplacement.ACTION_CHOICES,
        label="異動類型",
        widget=forms.Select(attrs={"class": "form-control", "id": "rma-action-type"}),
    )
    old_probe = forms.ModelChoiceField(
        queryset=Probe.objects.none(),
        label="換下/退回設備",
        widget=forms.Select(attrs={"class": "form-control", "id": "rma-old-probe"}),
    )
    new_probe = forms.ModelChoiceField(
        queryset=Probe.objects.filter(status=Probe.STATUS_AVAILABLE, is_deleted=False).select_related("model"),
        label="換上新設備 (在庫可用備品)",
        required=False,
        widget=forms.Select(attrs={"class": "form-control", "id": "rma-new-probe"}),
        help_text="僅列出在庫可用設備 (available)",
    )
    reason = forms.CharField(
        label="故障原因與更換說明",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "例如: 連接埠故障無法通訊，換上同型號備品"}),
    )

    def __init__(self, *args, contract=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.contract = contract
        if contract:
            self.fields["old_probe"].queryset = contract.probes.all().select_related("model")

    def clean(self):
        cleaned_data = super().clean()
        action_type = cleaned_data.get("action_type")
        new_probe = cleaned_data.get("new_probe")
        if action_type == ContractProbeReplacement.ACTION_REPLACE:
            if not new_probe:
                self.add_error("new_probe", "執行「故障換機」時必須選擇換上的新備品設備。")
        return cleaned_data


class CustomerImportForm(forms.Form):
    file = forms.FileField(
        label="選擇要匯入的 Excel (.xlsx) 或 CSV (.csv) 檔案",
        help_text="支援標準匯入範本或由本系統匯出的 Excel 檔案。必填欄位：客戶代號、客戶名稱。",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".xlsx,.csv"}),
    )


class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "widget",
            MultipleFileInput(attrs={"class": "form-control", "multiple": True}),
        )
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = single_file_clean(data, initial)
        return result


class CustomerActivityLogForm(forms.ModelForm):
    files = MultipleFileField(
        required=False,
        help_text="可選取一個或多個檔案進行上傳",
    )

    class Meta:
        model = CustomerActivityLog
        fields = ["activity_type", "contact", "follow_up_date", "content"]
        widgets = {
            "activity_type": forms.Select(attrs={"class": "form-control"}),
            "contact": forms.Select(attrs={"class": "form-control"}),
            "follow_up_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}),
            "content": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "輸入與客戶討論事項、會議記錄、進度追蹤或備忘事項...",
            }),
        }

    def __init__(self, *args, customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        if customer:
            self.fields["contact"].queryset = customer.contacts.all()
        elif self.instance and self.instance.pk and self.instance.customer:
            self.fields["contact"].queryset = self.instance.customer.contacts.all()
        else:
            self.fields["contact"].queryset = ContactPerson.objects.none()
        self.fields["contact"].empty_label = "-- 關聯窗口 (選填) --"


class CustomerInfoSectionForm(forms.ModelForm):
    class Meta:
        model = CustomerInfoSection
        fields = ["title", "content", "sort_order"]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "區塊名稱 (例如：客戶環境架構、部署主機 IP 清單、遠端連線資訊)",
                }
            ),
            "content": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "輸入此區塊的結構化現況說明、環境參數或注意事項...",
                }
            ),
            "sort_order": forms.NumberInput(
                attrs={"class": "form-control", "min": 0}
            ),
        }


class CustomerServiceRecordForm(forms.ModelForm):
    class Meta:
        model = CustomerServiceRecord
        fields = [
            "service_no",
            "name",
            "description",
            "service_date",
            "status",
            "completed_at",
            "quantity",
            "unit",
            "amount",
            "assigned_to",
            "note",
        ]
        widgets = {
            "service_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: SRV-20261125-0001"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: 資安健檢 / 系統導入教育訓練"}),
            "description": forms.TextInput(attrs={"class": "form-control", "placeholder": "服務規格或內容簡述"}),
            "service_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "completed_at": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "unit": forms.TextInput(attrs={"class": "form-control", "placeholder": "式 / 人天 / 小時"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": 1, "placeholder": "選填"}),
            "assigned_to": forms.Select(attrs={"class": "form-control"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "服務執行細節、交付紀錄或驗收備註..."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.fields["assigned_to"].queryset = User.objects.filter(is_active=True).order_by("username")
        self.fields["assigned_to"].empty_label = "-- 未指派 (選填) --"
        self.fields["assigned_to"].required = False


