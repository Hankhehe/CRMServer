import datetime
import json
import re
from typing import Optional
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.customers.models import Customer
from .models import (
    InventoryTransaction,
    HardwareDevice,
    HardwareModel,
    Supplier,
)


class CustomerModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"[{obj.code}] {obj.name}"


class HardwareDeviceForm(forms.ModelForm):
    customer = CustomerModelChoiceField(
        label="關聯客戶",
        queryset=Customer.objects.all().order_by("code"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = HardwareDevice
        fields = [
            "code",
            "model",
            "supplier",
            "status",
            "acquired_at",
            "customer",
            "expected_return_at",
            "port_count",
            "cpu",
            "memory",
            "hdd",
            "note",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control"}),
            "model": forms.Select(attrs={"class": "form-control", "id": "id_hardware_model_select"}),
            "supplier": forms.Select(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "acquired_at": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}),
            "expected_return_at": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}),
            "port_count": forms.NumberInput(attrs={"class": "form-control"}),
            "cpu": forms.TextInput(attrs={"class": "form-control"}),
            "memory": forms.TextInput(attrs={"class": "form-control"}),
            "hdd": forms.TextInput(attrs={"class": "form-control"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target_model_id = None
        if "model" in self.data:
            try:
                target_model_id = int(self.data.get("model"))
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.model_id:
            target_model_id = self.instance.model_id
        elif "initial" in kwargs and "model" in kwargs["initial"]:
            m = kwargs["initial"]["model"]
            target_model_id = m.id if isinstance(m, HardwareModel) else m

        if target_model_id:
            try:
                selected_model = HardwareModel.objects.get(pk=target_model_id)
                self._attach_dynamic_fields(selected_model)
            except HardwareModel.DoesNotExist:
                pass

        # 避免手動直接挑選 pending_ship / pending_loan，除非該設備目前正處於該鎖定狀態
        current_st = self.instance.status if (self.instance and self.instance.pk) else None
        allowed_choices = []
        for st_val, st_label in HardwareDevice.STATUS_CHOICES:
            if st_val in (HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN):
                if current_st == st_val:
                    allowed_choices.append((st_val, st_label))
            else:
                allowed_choices.append((st_val, st_label))
        self.fields["status"].choices = allowed_choices

    def _attach_dynamic_fields(self, hw_model: HardwareModel):
        schema = hw_model.custom_field_schema or []
        custom_vals = self.instance.custom_values or {}
        for item in schema:
            key = item.get("key")
            if not key:
                continue
            field_name = f"dyn_{key}"
            label = item.get("label", key)
            f_type = item.get("type", "text")
            default_val = item.get("default", "")
            initial_val = custom_vals.get(key, "")
            placeholder_text = f"預設: {default_val} (留空繼承預設)" if default_val != "" else "留空繼承型號預設"

            if f_type == "number":
                self.fields[field_name] = forms.DecimalField(
                    label=f"{label} 覆寫",
                    required=False,
                    initial=initial_val if initial_val != "" else None,
                    widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": placeholder_text}),
                    help_text=f"型號預設值: {default_val if default_val != '' else '無'}",
                )
            elif f_type == "select":
                opts = item.get("options", [])
                choices = [("", f"-- 繼承預設 ({default_val or '未設定'}) --")] + [(opt, opt) for opt in opts]
                self.fields[field_name] = forms.ChoiceField(
                    label=f"{label} 覆寫",
                    choices=choices,
                    required=False,
                    initial=initial_val,
                    widget=forms.Select(attrs={"class": "form-control"}),
                    help_text=f"型號預設值: {default_val if default_val != '' else '無'}",
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
                default_display = ", ".join(default_val) if isinstance(default_val, list) else (default_val if default_val != "" else "無")
                self.fields[field_name] = forms.MultipleChoiceField(
                    label=f"{label} 覆寫",
                    choices=choices,
                    required=False,
                    initial=init_list,
                    widget=forms.CheckboxSelectMultiple(attrs={"class": "dyn-multiselect-checkbox"}),
                    help_text=f"型號預設值: {default_display} (若全部不勾選則繼承預設)",
                )
            elif f_type == "boolean":
                choices = [
                    ("", f"-- 繼承預設 ({'是' if default_val else '否'}) --"),
                    ("true", "是 (True)"),
                    ("false", "否 (False)"),
                ]
                init_str = ""
                if key in custom_vals and custom_vals[key] is not None and custom_vals[key] != "":
                    init_str = "true" if custom_vals[key] in (True, "true", "True", 1) else "false"
                self.fields[field_name] = forms.ChoiceField(
                    label=f"{label} 覆寫",
                    choices=choices,
                    required=False,
                    initial=init_str,
                    widget=forms.Select(attrs={"class": "form-control"}),
                )
            elif f_type == "datetime":
                init_dt_str = str(initial_val or "").strip()
                if init_dt_str and " " in init_dt_str:
                    init_dt_str = init_dt_str.replace(" ", "T")[:16]
                def_dt_display = str(default_val or "").strip().replace("T", " ")[:16]
                self.fields[field_name] = forms.CharField(
                    label=f"{label} 覆寫",
                    required=False,
                    initial=init_dt_str,
                    widget=forms.DateTimeInput(
                        format="%Y-%m-%dT%H:%M",
                        attrs={"type": "datetime-local", "class": "form-control"},
                    ),
                    help_text=f"型號預設值: {def_dt_display if def_dt_display else '無'} (留空繼承預設)",
                )
            else:
                self.fields[field_name] = forms.CharField(
                    label=f"{label} 覆寫",
                    required=False,
                    initial=initial_val,
                    widget=forms.TextInput(attrs={"class": "form-control", "placeholder": placeholder_text}),
                    help_text=f"型號預設值: {default_val if default_val != '' else '無'}",
                )

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        customer = cleaned_data.get("customer")
        expected_return_at = cleaned_data.get("expected_return_at")

        if status in [
            HardwareDevice.STATUS_SHIPPED,
            HardwareDevice.STATUS_LOANED,
            HardwareDevice.STATUS_PENDING_SHIP,
            HardwareDevice.STATUS_PENDING_LOAN,
        ]:
            if not customer:
                self.add_error("customer", "出貨或借測狀態下必須選擇客戶")

        if status in [HardwareDevice.STATUS_LOANED, HardwareDevice.STATUS_PENDING_LOAN]:
            if not expected_return_at:
                self.add_error("expected_return_at", "借出狀態下必須填寫預計歸還日")

        if status in [HardwareDevice.STATUS_AVAILABLE, HardwareDevice.STATUS_SCRAPPED]:
            cleaned_data["customer"] = None

        if status not in [HardwareDevice.STATUS_LOANED, HardwareDevice.STATUS_PENDING_LOAN]:
            cleaned_data["expected_return_at"] = None

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.supplier and instance.model:
            instance.supplier = instance.model.supplier
        if instance.status in [HardwareDevice.STATUS_AVAILABLE, HardwareDevice.STATUS_SCRAPPED]:
            instance.customer = None
        if instance.status not in [HardwareDevice.STATUS_LOANED, HardwareDevice.STATUS_PENDING_LOAN]:
            instance.expected_return_at = None

        # Collect dynamic specification overrides
        custom_data = {}
        schema_map = {}
        if instance.model and instance.model.custom_field_schema:
            schema_map = {item.get("key"): item for item in instance.model.custom_field_schema if item.get("key")}
        for key, value in self.cleaned_data.items():
            if key.startswith("dyn_"):
                orig_key = key[4:]
                if value is None or value == "" or value == []:
                    continue
                if hasattr(value, "as_tuple"):
                    value = float(value)
                elif schema_map.get(orig_key, {}).get("type") == "datetime" and isinstance(value, str):
                    value = value.strip().replace("T", " ")[:16]
                custom_data[orig_key] = value
        instance.custom_values = custom_data

        if commit:
            instance.save()
        return instance


class TransactionForm(forms.Form):
    type = forms.ChoiceField(
        label="交易類型",
        choices=InventoryTransaction.TYPE_CHOICES,
        required=True,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    customer = CustomerModelChoiceField(
        label="交易客戶 (代號/名稱)",
        queryset=Customer.objects.all().order_by("code"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    device = forms.ModelChoiceField(
        label="庫存設備 (序號)",
        queryset=HardwareDevice.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    supplier = forms.ModelChoiceField(
        label="進貨供應商",
        queryset=Supplier.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    model = forms.ModelChoiceField(
        label="規格型號",
        queryset=HardwareModel.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    code = forms.CharField(
        label="設備序號",
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    acquired_at = forms.DateField(
        label="進貨日期",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    expected_return_at = forms.DateField(
        label="預計歸還日",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    note = forms.CharField(
        label="交易備註",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type_code = None
        customer = None
        if self.data:
            type_code = self.data.get("type")
            customer_id = self.data.get("customer")
            if customer_id:
                try:
                    customer = Customer.objects.get(pk=customer_id)
                except (Customer.DoesNotExist, ValueError):
                    pass
        elif "initial" in kwargs:
            type_code = kwargs["initial"].get("type")
            cust_val = kwargs["initial"].get("customer")
            if isinstance(cust_val, Customer):
                customer = cust_val
            elif cust_val:
                try:
                    customer = Customer.objects.get(pk=cust_val)
                except (Customer.DoesNotExist, ValueError):
                    pass

        if type_code:
            self.fields["device"].queryset = self._get_device_queryset(type_code, customer)
        else:
            self.fields["device"].queryset = HardwareDevice.objects.select_related("model", "supplier").order_by("code")

    def _get_device_queryset(self, type_code, customer):
        qs = HardwareDevice.objects.select_related("model", "supplier")
        status_map = {
            InventoryTransaction.TYPE_OUT: HardwareDevice.STATUS_AVAILABLE,
            InventoryTransaction.TYPE_LOAN: HardwareDevice.STATUS_AVAILABLE,
            InventoryTransaction.TYPE_RETURN_OUT: HardwareDevice.STATUS_SHIPPED,
            InventoryTransaction.TYPE_RETURN_LOAN: HardwareDevice.STATUS_LOANED,
        }
        if status := status_map.get(type_code):
            qs = qs.filter(status=status)
            if customer and type_code in (
                InventoryTransaction.TYPE_RETURN_OUT,
                InventoryTransaction.TYPE_RETURN_LOAN,
            ):
                qs = qs.filter(customer=customer)
        elif type_code == InventoryTransaction.TYPE_SCRAP:
            qs = qs.exclude(
                status__in=[
                    HardwareDevice.STATUS_SCRAPPED,
                    HardwareDevice.STATUS_PENDING_SHIP,
                    HardwareDevice.STATUS_PENDING_LOAN,
                ]
            )
        elif type_code == InventoryTransaction.TYPE_IN:
            qs = HardwareDevice.objects.none()
        return qs.order_by("code")

    def clean(self):
        cleaned = super().clean()
        type_code = cleaned.get("type")
        device = cleaned.get("device")
        customer = cleaned.get("customer")
        code = (cleaned.get("code") or "").strip()
        model = cleaned.get("model")
        expected_return_at = cleaned.get("expected_return_at")

        if not type_code:
            raise ValidationError("必須選擇交易類型")

        self.fields["device"].queryset = self._get_device_queryset(type_code, customer)

        if type_code == InventoryTransaction.TYPE_IN:
            if not code:
                self.add_error("code", "進貨必須填寫設備序號")
            if not model:
                self.add_error("model", "進貨必須選擇規格")
            if code and HardwareDevice.objects.filter(code=code).exists():
                self.add_error("code", "此序號已存在庫存中")
        elif type_code == InventoryTransaction.TYPE_SCRAP:
            if not device:
                self.add_error("device", "請選擇欲報廢的設備")
            elif device.status == HardwareDevice.STATUS_SCRAPPED:
                self.add_error("device", "此設備已是報廢狀態")
            elif device.status in (HardwareDevice.STATUS_PENDING_SHIP, HardwareDevice.STATUS_PENDING_LOAN):
                self.add_error("device", "此設備目前處於出庫待覆核鎖定中，請先退回出庫簽核單再行報廢")
        else:
            if not customer:
                self.add_error("customer", "請先選擇客戶")
            if not device:
                self.add_error("device", "請選擇庫存設備")

            if device:
                if type_code in (InventoryTransaction.TYPE_OUT, InventoryTransaction.TYPE_LOAN):
                    if device.status != HardwareDevice.STATUS_AVAILABLE:
                        self.add_error("device", "僅可出貨/借出狀態為庫存中的設備")
                if type_code == InventoryTransaction.TYPE_RETURN_OUT:
                    if device.status != HardwareDevice.STATUS_SHIPPED:
                        self.add_error("device", "退貨僅能選擇已出貨的設備")
                    if customer and device.customer_id != customer.id:
                        self.add_error("device", "退貨僅能選擇該客戶的出貨設備")
                if type_code == InventoryTransaction.TYPE_RETURN_LOAN:
                    if device.status != HardwareDevice.STATUS_LOANED:
                        self.add_error("device", "歸還僅能選擇已借出的設備")
                    if customer and device.customer_id != customer.id:
                        self.add_error("device", "歸還僅能選擇該客戶的借出設備")

            if type_code == InventoryTransaction.TYPE_LOAN and not expected_return_at:
                self.add_error("expected_return_at", "借出必須填寫預計歸還日")

        return cleaned

    def save(self, user=None, ip_address=None):
        type_code = self.cleaned_data["type"]
        note = self.cleaned_data.get("note", "")
        customer = self.cleaned_data.get("customer")
        device = self.cleaned_data.get("device")
        acquired_at = self.cleaned_data.get("acquired_at")
        expected_return_at = self.cleaned_data.get("expected_return_at")

        if type_code == InventoryTransaction.TYPE_IN:
            model_obj = self.cleaned_data.get("model")
            device = HardwareDevice(
                code=self.cleaned_data["code"].strip(),
                model=model_obj,
                supplier=model_obj.supplier if model_obj else None,
                status=HardwareDevice.STATUS_AVAILABLE,
                customer=None,
                acquired_at=acquired_at,
                note=note,
            )
            device._created_by = user if user and user.is_authenticated else None
            device.save()
        else:
            if type_code in (InventoryTransaction.TYPE_OUT, InventoryTransaction.TYPE_LOAN):
                from apps.core.permissions import is_hardware_approval_required_for_user
                if user and is_hardware_approval_required_for_user(user):
                    from .approval_service import create_hardware_outbound_approval
                    from .models import HardwareOutboundApproval
                    req_type = (
                        HardwareOutboundApproval.REQUEST_LOAN
                        if type_code == InventoryTransaction.TYPE_LOAN
                        else HardwareOutboundApproval.REQUEST_SHIP
                    )
                    approval_obj = create_hardware_outbound_approval(
                        device=device,
                        customer=customer,
                        request_type=req_type,
                        applicant=user,
                        expected_return_at=expected_return_at,
                        note=note,
                        ip_address=ip_address,
                    )
                    approval_obj._is_pending_approval = True
                    return approval_obj

                device.status = HardwareDevice.STATUS_SHIPPED
                if type_code == InventoryTransaction.TYPE_LOAN:
                    device.status = HardwareDevice.STATUS_LOANED
                    device.expected_return_at = expected_return_at
                device.customer = customer
            elif type_code in (InventoryTransaction.TYPE_RETURN_OUT, InventoryTransaction.TYPE_RETURN_LOAN):
                device.status = HardwareDevice.STATUS_AVAILABLE
                device.customer = None
                device.expected_return_at = None
            elif type_code == InventoryTransaction.TYPE_SCRAP:
                device.status = HardwareDevice.STATUS_SCRAPPED
                device.customer = None
                device.expected_return_at = None

            device._created_by = user if user and user.is_authenticated else None
            device._transaction_note = note
            device.save()

        transaction = device.transactions.order_by("-occurred_at", "-id").first()
        return transaction


class HardwareModelForm(forms.ModelForm):
    custom_field_schema_raw = forms.CharField(
        label="自訂擴充規格欄位",
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = HardwareModel
        fields = ["model_code", "name", "port_count", "cpu", "memory", "hdd", "supplier", "note"]
        widgets = {
            "model_code": forms.TextInput(attrs={"class": "form-control", "placeholder": "型號代碼 (唯一值)"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "型號名稱"}),
            "port_count": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "cpu": forms.TextInput(attrs={"class": "form-control"}),
            "memory": forms.TextInput(attrs={"class": "form-control"}),
            "hdd": forms.TextInput(attrs={"class": "form-control"}),
            "supplier": forms.Select(attrs={"class": "form-control"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["port_count"].required = False
        if self.instance.pk and self.instance.custom_field_schema:
            self.fields["custom_field_schema_raw"].initial = json.dumps(
                self.instance.custom_field_schema, ensure_ascii=False
            )

    def clean_port_count(self):
        val = self.cleaned_data.get("port_count")
        return val if val is not None else 0

    def clean_custom_field_schema_raw(self):
        raw = self.cleaned_data.get("custom_field_schema_raw", "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise forms.ValidationError("自訂規格欄位必須是陣列格式")
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
            raise forms.ValidationError(f"規格欄位解析錯誤: {e}")

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.custom_field_schema = self.cleaned_data.get("custom_field_schema_raw", [])
        if commit:
            instance.save()
        return instance


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact", "email", "phone", "address", "note"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "contact": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "address": forms.TextInput(attrs={"class": "form-control"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class HardwareImportForm(forms.Form):
    file = forms.FileField(
        label="選擇要匯入的 Excel (.xlsx) 或 CSV (.csv) 檔案",
        help_text="支援標準匯入範本或由本系統匯出的 Excel 檔案。必填欄位：設備序號/資產編號、設備型號名稱。",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".xlsx,.csv"}),
    )


class ModelImportForm(forms.Form):
    file = forms.FileField(
        label="選擇規格型號清單檔案",
        help_text="支援 CSV 或 Excel (.xlsx)，欄位: name, port_count, cpu, memory, hdd, supplier_name, note",
        widget=forms.FileInput(attrs={"class": "form-control"}),
    )


# Backward compatibility aliases
ProbeForm = HardwareDeviceForm
ProbeModelForm = HardwareModelForm
ProbeImportForm = HardwareImportForm
