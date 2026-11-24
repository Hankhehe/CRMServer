from django.contrib import admin
from .models import Order, OrderItem, OrderVersion, OrderAttachment, OrderPayment


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_no", "title", "customer", "status", "current_version", "total_amount", "created_at")
    list_filter = ("status", "tax_mode")
    search_fields = ("order_no", "title", "customer__name", "customer__code")


admin.site.register(OrderItem)
admin.site.register(OrderVersion)
admin.site.register(OrderAttachment)
admin.site.register(OrderPayment)
