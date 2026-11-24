from django.contrib import admin
from .models import Product, CustomerLicense


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "category", "default_unit", "is_active", "updated_at"]
    search_fields = ["code", "name", "category"]
    list_filter = ["category", "is_active"]


@admin.register(CustomerLicense)
class CustomerLicenseAdmin(admin.ModelAdmin):
    list_display = [
        "license_no",
        "customer",
        "product",
        "license_type",
        "quantity",
        "unit",
        "status",
        "is_perpetual",
        "end_date",
    ]
    search_fields = ["license_no", "customer__name", "customer__code", "product__name"]
    list_filter = ["license_type", "status", "is_perpetual", "product"]

