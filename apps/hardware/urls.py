from django.urls import path
from . import views

urlpatterns = [
    path("dashboard/", views.hardware_dashboard, name="hardware-dashboard"),
    path("", views.device_list, name="hardware-list"),
    path("export/", views.device_export, name="hardware-export"),
    path("import/", views.device_import, name="hardware-import"),
    path("import/template/", views.device_import_template, name="hardware-import-template"),
    path("create/", views.device_create, name="hardware-create"),
    path("batch-approve/", views.hardware_batch_approve, name="hardware-batch-approve"),
    path("<int:pk>/", views.device_detail, name="hardware-detail"),
    path("<int:pk>/edit/", views.device_update, name="hardware-update"),
    path("<int:pk>/delete/", views.device_delete, name="hardware-delete"),
    path("<int:pk>/return/", views.device_return, name="hardware-device-return"),
    path("<int:pk>/approve/", views.hardware_approve, name="hardware-approve"),
    path("<int:pk>/reject/", views.hardware_reject, name="hardware-reject"),
    # Models
    path("models/", views.model_list, name="hardware-models"),
    path("models/create/", views.model_create, name="hardware-model-create"),
    path("models/<int:pk>/edit/", views.model_update, name="hardware-model-update"),
    path("models/<int:pk>/delete/", views.model_delete, name="hardware-model-delete"),
    # Suppliers
    path("suppliers/", views.supplier_list, name="hardware-suppliers"),
    path("suppliers/create/", views.supplier_create, name="hardware-supplier-create"),
    path("suppliers/<int:pk>/edit/", views.supplier_update, name="hardware-supplier-update"),
    path("suppliers/<int:pk>/delete/", views.supplier_delete, name="hardware-supplier-delete"),
    # Transactions
    path("transactions/", views.transaction_list, name="hardware-transactions"),
    path("transactions/create/", views.transaction_create, name="hardware-transaction-create"),
    # API helpers
    path("api/customers/<int:customer_id>/devices/", views.api_customer_devices, name="api-customer-devices"),
    path("api/models/<int:pk>/meta/", views.api_hardware_model_meta, name="api-hardware-model-meta"),
    # Legacy Probe aliases for seamless template compatibility
    path("dashboard/probe-alias/", views.hardware_dashboard, name="probe-dashboard"),
    path("probe-alias/", views.device_list, name="probe-list"),
    path("create/probe-alias/", views.device_create, name="probe-create"),
    path("<int:pk>/probe-alias/", views.device_detail, name="probe-detail"),
    path("<int:pk>/edit/probe-alias/", views.device_update, name="probe-update"),
    path("<int:pk>/delete/probe-alias/", views.device_delete, name="probe-delete"),
    path("<int:pk>/return/probe-alias/", views.device_return, name="probe-return"),
    path("models/probe-alias/", views.model_list, name="probe-models"),
    path("models/create/probe-alias/", views.model_create, name="probe-model-create"),
    path("models/<int:pk>/edit/probe-alias/", views.model_update, name="probe-model-update"),
    path("models/<int:pk>/delete/probe-alias/", views.model_delete, name="probe-model-delete"),
    path("suppliers/probe-alias/", views.supplier_list, name="probe-suppliers"),
    path("suppliers/create/probe-alias/", views.supplier_create, name="probe-supplier-create"),
    path("suppliers/<int:pk>/edit/probe-alias/", views.supplier_update, name="probe-supplier-update"),
    path("suppliers/<int:pk>/delete/probe-alias/", views.supplier_delete, name="probe-supplier-delete"),
    path("transactions/probe-alias/", views.transaction_list, name="probe-transactions"),
    path("transactions/create/probe-alias/", views.transaction_create, name="transaction-create"),
    path("api/customers/<int:customer_id>/probes/", views.api_customer_devices, name="api-customer-probes"),
]
