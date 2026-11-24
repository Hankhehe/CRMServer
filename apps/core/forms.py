from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from .models import AuditLog, OuPermission, Role, SystemSetting, UserProfile
from .permissions import get_ad_groups

User = get_user_model()


class CustomAuthenticationForm(AuthenticationForm):
    account_type = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        account_type = self.data.get("account_type", "local")

        if username is not None and password:
            self.user_cache = authenticate(
                self.request,
                username=username,
                password=password,
                account_type=account_type,
            )
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            else:
                self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class OuPermissionForm(forms.ModelForm):
    ou_path = forms.ChoiceField(
        label="AD 安全群組",
        choices=[],
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="選擇 AD 群組套用對應的權限角色",
    )

    class Meta:
        model = OuPermission
        fields = ["ou_path", "role", "note"]
        widgets = {
            "role": forms.Select(attrs={"class": "form-control"}),
            "note": forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        groups = get_ad_groups()
        self.fields["ou_path"].choices = [(g, g) for g in groups]


class SystemSettingForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = [
            "ad_ip",
            "ldap_bind_dn",
            "ldap_bind_password",
            "ldap_base_dn",
            "admin_ou",
        ]
        widgets = {
            "ad_ip": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "ldap://192.168.10.201"}
            ),
            "ldap_bind_dn": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "CN=admin,CN=Users,DC=example,DC=com"}
            ),
            "ldap_bind_password": forms.PasswordInput(
                render_value=True, attrs={"class": "form-control"}
            ),
            "ldap_base_dn": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "DC=example,DC=com"}
            ),
            "admin_ou": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "IT"}
            ),
        }


class SmtpSettingForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = [
            "smtp_host",
            "smtp_port",
            "smtp_use_tls",
            "smtp_use_ssl",
            "smtp_user",
            "smtp_password",
            "smtp_from_email",
            "site_base_url",
        ]
        widgets = {
            "smtp_host": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "例如: smtp.office365.com 或 smtp.gmail.com"}
            ),
            "smtp_port": forms.NumberInput(
                attrs={"class": "form-control", "placeholder": "587"}
            ),
            "smtp_use_tls": forms.CheckboxInput(
                attrs={"style": "margin-right: 6px;"}
            ),
            "smtp_use_ssl": forms.CheckboxInput(
                attrs={"style": "margin-right: 6px;"}
            ),
            "smtp_user": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "notify@example.com"}
            ),
            "smtp_password": forms.PasswordInput(
                render_value=True, attrs={"class": "form-control", "placeholder": "SMTP 密碼或應用程式密碼"}
            ),
            "smtp_from_email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "crm-notify@example.com (選填)"}
            ),
            "site_base_url": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "http://127.0.0.1:8000"}
            ),
        }


class AccountNotificationPreferenceForm(forms.Form):
    email = forms.EmailField(
        label="接收通知之電子郵件 (Email)",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "請輸入您的公司或個人信箱以接收通知"}),
    )
    first_name = forms.CharField(
        label="名字 / 顯示名稱",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "選填"}),
    )
    last_name = forms.CharField(
        label="姓氏",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "選填"}),
    )
    receive_email_notifications = forms.BooleanField(
        label="啟用關注客戶 Email 通知 (總開關)",
        required=False,
    )
    notify_on_new_comment = forms.BooleanField(
        label="當關注客戶有「新留言 / 執行軌跡更新」時寄信通知我",
        required=False,
    )
    notify_on_data_change = forms.BooleanField(
        label="當關注客戶有「基本資料 / 聯絡人 / 維護合約 / 授權 / 設備異動」時寄信通知我",
        required=False,
    )



class RoleForm(forms.ModelForm):
    template_preset = forms.ChoiceField(
        label="套用預設範本快速帶入",
        required=False,
        choices=[("", "-- 請選擇範本以自動載入勾選與說明 (選填) --")] + [
            (code, data["name"]) for code, data in Role.SYSTEM_PRESETS.items()
        ],
        widget=forms.Select(attrs={"class": "form-control", "id": "id_template_preset"}),
        help_text="選取範本會自動填入角色說明與各模組勾選，您可自由微調後存檔為新角色。",
    )

    class Meta:
        model = Role
        fields = [
            "name",
            "code",
            "description",
            "customer_scope",
            "can_view_crm",
            "can_edit_customers",
            "can_edit_licenses",
            "can_edit_products",
            "can_edit_probes",
            "can_manage_system",
            "can_approve_licenses",
            "can_approve_contracts",
            "can_approve_hardware",
            "can_edit_orders",
            "can_approve_orders",
            "can_ship_orders",
            "can_close_orders",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: 業務技術主管"}),
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "例如: sales_tech_lead"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "簡述此角色的職責與適用範圍"}),
            "customer_scope": forms.Select(attrs={"class": "form-control"}),
            "can_view_crm": forms.CheckboxInput(attrs={"id": "id_can_view_crm", "style": "margin-right: 8px;"}),
            "can_edit_customers": forms.CheckboxInput(attrs={"id": "id_can_edit_customers", "style": "margin-right: 8px;"}),
            "can_edit_licenses": forms.CheckboxInput(attrs={"id": "id_can_edit_licenses", "style": "margin-right: 8px;"}),
            "can_edit_products": forms.CheckboxInput(attrs={"id": "id_can_edit_products", "style": "margin-right: 8px;"}),
            "can_edit_probes": forms.CheckboxInput(attrs={"id": "id_can_edit_probes", "style": "margin-right: 8px;"}),
            "can_manage_system": forms.CheckboxInput(attrs={"id": "id_can_manage_system", "style": "margin-right: 8px;"}),
            "can_approve_licenses": forms.CheckboxInput(attrs={"id": "id_can_approve_licenses", "style": "margin-right: 8px;"}),
            "can_approve_contracts": forms.CheckboxInput(attrs={"id": "id_can_approve_contracts", "style": "margin-right: 8px;"}),
            "can_approve_hardware": forms.CheckboxInput(attrs={"id": "id_can_approve_hardware", "style": "margin-right: 8px;"}),
            "can_edit_orders": forms.CheckboxInput(attrs={"id": "id_can_edit_orders", "style": "margin-right: 8px;"}),
            "can_approve_orders": forms.CheckboxInput(attrs={"id": "id_can_approve_orders", "style": "margin-right: 8px;"}),
            "can_ship_orders": forms.CheckboxInput(attrs={"id": "id_can_ship_orders", "style": "margin-right: 8px;"}),
            "can_close_orders": forms.CheckboxInput(attrs={"id": "id_can_close_orders", "style": "margin-right: 8px;"}),
        }

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip().lower()
        if not code:
            raise forms.ValidationError("請輸入角色代碼")
        import re
        if not re.match(r'^[a-z0-9_-]+$', code):
            raise forms.ValidationError("角色代碼僅能包含小寫英文字母、數字、下底線與減號")
        return code


class LocalUserCreationForm(UserCreationForm):
    first_name = forms.CharField(label="名字", required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    last_name = forms.CharField(label="姓氏", required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    email = forms.EmailField(label="電子郵件", required=False, widget=forms.EmailInput(attrs={"class": "form-control"}))
    role_obj = forms.ModelChoiceField(
        label="指派系統角色",
        queryset=Role.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-control", "id": "id_role_obj"}),
        help_text="選取已定義之角色，此帳號將自動繼承該角色的所有模組功能權限與資料視野。",
    )
    is_superuser = forms.BooleanField(
        label="系統超級管理員 (Superuser - 擁有資料庫切換與備份還原最高權限)",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_is_superuser"}),
    )
    can_approve_licenses = forms.BooleanField(
        label="可覆核「軟體授權書」",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_approve_licenses"}),
    )
    can_approve_contracts = forms.BooleanField(
        label="可覆核「維護保固合約」",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_approve_contracts"}),
    )
    can_approve_hardware = forms.BooleanField(
        label="可覆核「硬體設備出庫」",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_approve_hardware"}),
    )
    can_approve_orders = forms.BooleanField(
        label="可審核「客戶訂單」並核准開單 (業務主管)",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_approve_orders"}),
    )
    can_close_orders = forms.BooleanField(
        label="可確認訂單款項入帳與結案 (管理部)",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_close_orders"}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields + ("email", "last_name", "first_name")

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if username and username.endswith("_ad"):
            raise forms.ValidationError("本機帳號名稱不能以 _ad 結尾")
        return username

    def __init__(self, *args, **kwargs):
        Role.ensure_system_roles()
        super().__init__(*args, **kwargs)
        self.fields["role_obj"].queryset = Role.objects.all()
        for f in ["username", "password1", "password2"]:
            if f in self.fields:
                self.fields[f].widget.attrs["class"] = "form-control"

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data.get("email", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        user.first_name = self.cleaned_data.get("first_name", "")
        user.is_superuser = self.cleaned_data.get("is_superuser", False)
        if user.is_superuser:
            user.is_staff = True
        if commit:
            user.save()
            role_obj = self.cleaned_data.get("role_obj")
            if not role_obj:
                role_obj = Role.objects.filter(code="view").first()

            profile, _ = UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "role_obj": role_obj,
                    "role": role_obj.code if role_obj else UserProfile.ROLE_VIEW,
                    "customer_scope": role_obj.customer_scope if role_obj else UserProfile.SCOPE_ALL,
                    "can_view_crm": role_obj.can_view_crm if role_obj else True,
                    "can_edit_customers": role_obj.can_edit_customers if role_obj else False,
                    "can_edit_licenses": role_obj.can_edit_licenses if role_obj else False,
                    "can_edit_products": role_obj.can_edit_products if role_obj else False,
                    "can_edit_crm": (role_obj.can_edit_customers and role_obj.can_edit_licenses) if role_obj else False,
                    "can_edit_probes": role_obj.can_edit_probes if role_obj else False,
                    "can_manage_system": (role_obj.can_manage_system or user.is_superuser) if role_obj else user.is_superuser,
                    "can_approve_licenses": bool(
                        self.cleaned_data.get("can_approve_licenses")
                        or (role_obj and role_obj.can_approve_licenses)
                        or user.is_superuser
                    ),
                    "can_approve_contracts": bool(
                        self.cleaned_data.get("can_approve_contracts")
                        or (role_obj and role_obj.can_approve_contracts)
                        or user.is_superuser
                    ),
                    "can_approve_hardware": bool(
                        self.cleaned_data.get("can_approve_hardware")
                        or (role_obj and role_obj.can_approve_hardware)
                        or user.is_superuser
                    ),
                    "can_edit_orders": bool(
                        (role_obj and role_obj.can_edit_orders) or user.is_superuser
                    ),
                    "can_approve_orders": bool(
                        self.cleaned_data.get("can_approve_orders")
                        or (role_obj and role_obj.can_approve_orders)
                        or user.is_superuser
                    ),
                    "can_ship_orders": bool(
                        (role_obj and role_obj.can_ship_orders) or user.is_superuser
                    ),
                    "can_close_orders": bool(
                        self.cleaned_data.get("can_close_orders")
                        or (role_obj and role_obj.can_close_orders)
                        or user.is_superuser
                    ),
                },
            )
            user.profile = profile
        return user


class LocalUserEditForm(forms.ModelForm):
    first_name = forms.CharField(label="名字", required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    last_name = forms.CharField(label="姓氏", required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    email = forms.EmailField(label="電子郵件", required=False, widget=forms.EmailInput(attrs={"class": "form-control"}))
    role_obj = forms.ModelChoiceField(
        label="指派系統角色",
        queryset=Role.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-control", "id": "id_role_obj"}),
        help_text="選取角色後，該帳號將即時繼承該角色的所有模組功能權限與資料視野。",
    )
    is_active = forms.BooleanField(label="啟用帳號", required=False, widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;"}))
    is_superuser = forms.BooleanField(label="系統超級管理員 (Superuser)", required=False, widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_is_superuser"}))
    can_approve_licenses = forms.BooleanField(
        label="可覆核「軟體授權書」",
        required=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_approve_licenses"}),
    )
    can_approve_contracts = forms.BooleanField(
        label="可覆核「維護保固合約」",
        required=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_approve_contracts"}),
    )
    can_approve_hardware = forms.BooleanField(
        label="可覆核「硬體設備出庫」",
        required=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_approve_hardware"}),
    )
    can_approve_orders = forms.BooleanField(
        label="可審核「客戶訂單」並核准開單 (業務主管)",
        required=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_approve_orders"}),
    )
    can_close_orders = forms.BooleanField(
        label="可確認訂單款項入帳與結案 (管理部)",
        required=False,
        widget=forms.CheckboxInput(attrs={"style": "margin-right: 8px;", "id": "id_can_close_orders"}),
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "is_active", "is_superuser"]

    def __init__(self, *args, **kwargs):
        Role.ensure_system_roles()
        super().__init__(*args, **kwargs)
        self.fields["role_obj"].queryset = Role.objects.all()
        if self.instance and hasattr(self.instance, "profile"):
            p = self.instance.profile
            if p.role_obj:
                self.fields["role_obj"].initial = p.role_obj
            else:
                self.fields["role_obj"].initial = Role.objects.filter(code=p.role).first()
            self.fields["can_approve_licenses"].initial = p.has_license_approve_access
            self.fields["can_approve_contracts"].initial = p.has_contract_approve_access
            self.fields["can_approve_hardware"].initial = p.has_hardware_approve_access
            self.fields["can_approve_orders"].initial = p.has_order_approve_access
            self.fields["can_close_orders"].initial = p.has_order_close_access

    def save(self, commit=True):
        user = super().save(commit=commit)
        role_obj = self.cleaned_data.get("role_obj")
        if commit and role_obj:
            profile = getattr(user, "profile", None)
            if not profile:
                profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role_obj = role_obj
            profile.role = role_obj.code
            profile.customer_scope = role_obj.customer_scope
            profile.can_view_crm = role_obj.can_view_crm
            profile.can_edit_customers = role_obj.can_edit_customers
            profile.can_edit_licenses = role_obj.can_edit_licenses
            profile.can_edit_products = role_obj.can_edit_products
            profile.can_edit_probes = role_obj.can_edit_probes
            profile.can_manage_system = role_obj.can_manage_system or user.is_superuser
            profile.can_edit_crm = profile.can_edit_customers and profile.can_edit_licenses
            profile.can_approve_licenses = bool(self.cleaned_data.get("can_approve_licenses") or user.is_superuser)
            profile.can_approve_contracts = bool(self.cleaned_data.get("can_approve_contracts") or user.is_superuser)
            profile.can_approve_hardware = bool(self.cleaned_data.get("can_approve_hardware") or user.is_superuser)
            profile.can_edit_orders = bool(role_obj.can_edit_orders or user.is_superuser)
            profile.can_approve_orders = bool(self.cleaned_data.get("can_approve_orders") or role_obj.can_approve_orders or user.is_superuser)
            profile.can_ship_orders = bool(role_obj.can_ship_orders or user.is_superuser)
            profile.can_close_orders = bool(self.cleaned_data.get("can_close_orders") or role_obj.can_close_orders or user.is_superuser)
            profile.save()
            user.profile = profile
        return user


class IdleTimeoutSettingForm(forms.ModelForm):
    idle_timeout_minutes = forms.IntegerField(
        min_value=1,
        max_value=1440,
        label="閒置自動登出時間 (分鐘)",
        help_text="有效範圍為 1 至 1440 分鐘 (預設 10 分鐘)。當使用者無任何操作或關閉瀏覽器超過此時間後將自動登出。",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": "1",
                "max": "1440",
                "style": "max-width: 200px;",
            }
        ),
    )

    class Meta:
        model = SystemSetting
        fields = ["idle_timeout_minutes"]


class ApprovalWorkflowSettingForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = [
            "require_license_approval",
            "require_warranty_approval",
            "require_hardware_approval",
        ]
        widgets = {
            "require_license_approval": forms.CheckboxInput(
                attrs={"id": "id_require_license_approval", "style": "width: 18px; height: 18px; cursor: pointer;"}
            ),
            "require_warranty_approval": forms.CheckboxInput(
                attrs={"id": "id_require_warranty_approval", "style": "width: 18px; height: 18px; cursor: pointer;"}
            ),
            "require_hardware_approval": forms.CheckboxInput(
                attrs={"id": "id_require_hardware_approval", "style": "width: 18px; height: 18px; cursor: pointer;"}
            ),
        }

