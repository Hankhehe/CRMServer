import json
import re
from django import forms
from apps.customers.models import Customer
from .models import Product, CustomerLicense


class CustomerModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"[{obj.code}] {obj.name}"


class ProductForm(forms.ModelForm):
    custom_field_schema_raw = forms.CharField(
        label="自訂欄位規格",
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = Product
        fields = [
            "code",
            "name",
            "category",
            "default_unit",
            "is_active",
            "note",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: FLOW-PROBE-CORE"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "產品名稱"}),
            "category": forms.TextInput(attrs={"class": "form-control", "placeholder": "產品類別 (例如: 流量分析、資安防護)"}),
            "default_unit": forms.TextInput(attrs={"class": "form-control", "placeholder": "台 / 節點 / 核心"}),
            "is_active": forms.CheckboxInput(attrs={"style": "margin-right: 8px;"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "產品說明與備註"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.custom_field_schema:
            self.fields["custom_field_schema_raw"].initial = json.dumps(
                self.instance.custom_field_schema, ensure_ascii=False
            )

    def clean_custom_field_schema_raw(self):
        raw = self.cleaned_data.get("custom_field_schema_raw", "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise forms.ValidationError("自訂欄位規格必須是陣列格式")
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
            raise forms.ValidationError(f"欄位規格解析錯誤: {e}")

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.custom_field_schema = self.cleaned_data.get("custom_field_schema_raw", [])
        if commit:
            instance.save()
        return instance


class CustomerLicenseForm(forms.ModelForm):
    customer = CustomerModelChoiceField(
        label="採購客戶",
        queryset=Customer.objects.all().order_by("code"),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    product = forms.ModelChoiceField(
        label="授權產品",
        queryset=Product.objects.filter(is_active=True).order_by("code"),
        widget=forms.Select(attrs={"class": "form-control", "id": "id_product_select"}),
    )

    class Meta:
        model = CustomerLicense
        fields = [
            "customer",
            "product",
            "license_no",
            "quantity",
            "unit",
            "start_date",
            "end_date",
            "is_perpetual",
            "is_disabled",
            "license_key",
            "note",
        ]
        widgets = {
            "license_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: L-20261125-0001"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "unit": forms.TextInput(attrs={"class": "form-control", "placeholder": "台 / 節點"}),
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}),
            "is_perpetual": forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_is_perpetual"}),
            "is_disabled": forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_is_disabled"}),
            "license_key": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "授權金鑰、憑證字串或序號"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "授權備註說明"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        target_product_id = None
        if "product" in self.data:
            try:
                target_product_id = int(self.data.get("product"))
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.product_id:
            target_product_id = self.instance.product_id
        elif "initial" in kwargs and "product" in kwargs["initial"]:
            p = kwargs["initial"]["product"]
            target_product_id = p.id if isinstance(p, Product) else p

        if target_product_id:
            try:
                selected_product = Product.objects.get(pk=target_product_id)
                self._attach_dynamic_fields(selected_product)
            except Product.DoesNotExist:
                pass

    def clean_license_no(self):
        from apps.core.numbering import generate_license_no, validate_unique_doc_no

        return validate_unique_doc_no(
            CustomerLicense,
            "license_no",
            self.cleaned_data.get("license_no", ""),
            instance_pk=self.instance.pk if self.instance else None,
            generator=generate_license_no,
            label="軟體授權書編號",
        )

    def _attach_dynamic_fields(self, product: Product):
        schema = product.custom_field_schema or []
        custom_vals = self.instance.custom_values or {}
        for item in schema:
            key = item.get("key")
            if not key:
                continue
            field_name = f"dyn_{key}"
            label = item.get("label", key)
            f_type = item.get("type", "text")
            required = item.get("required", False)
            initial_val = custom_vals.get(key, item.get("default"))

            if f_type == "number":
                self.fields[field_name] = forms.DecimalField(
                    label=label,
                    required=required,
                    initial=initial_val,
                    widget=forms.NumberInput(attrs={"class": "form-control"}),
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
                self.fields[field_name] = forms.BooleanField(
                    label=label,
                    required=required,
                    initial=bool(initial_val),
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

    def clean(self):
        cleaned_data = super().clean()
        is_perpetual = cleaned_data.get("is_perpetual")
        end_date = cleaned_data.get("end_date")

        if is_perpetual:
            cleaned_data["end_date"] = None
        elif not end_date:
            self.add_error("end_date", "非永久買斷授權請設定授權到期日")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.is_perpetual:
            instance.license_type = CustomerLicense.TYPE_PERPETUAL
            instance.end_date = None
        else:
            instance.license_type = CustomerLicense.TYPE_SUBSCRIPTION

        # Collect dynamic fields into custom_values
        custom_data = {}
        schema_map = {}
        if instance.product and instance.product.custom_field_schema:
            schema_map = {item.get("key"): item for item in instance.product.custom_field_schema if item.get("key")}
        for key, value in self.cleaned_data.items():
            if key.startswith("dyn_"):
                orig_key = key[4:]
                if isinstance(value, forms.Field):
                    continue
                f_type = schema_map.get(orig_key, {}).get("type", "text")
                if f_type != "boolean" and (value is None or value == "" or value == []):
                    continue
                # serialize decimal to float/int if needed
                if hasattr(value, "as_tuple"):
                    value = float(value)
                elif f_type == "datetime" and isinstance(value, str):
                    value = value.strip().replace("T", " ")[:16]
                custom_data[orig_key] = value
        instance.custom_values = custom_data

        if commit:
            instance.save()

        return instance
