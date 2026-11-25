from django.urls import path
from . import views

urlpatterns = [
    path("", views.order_list, name="order-list"),
    path("export/", views.order_export, name="order-export"),
    path("create/", views.order_create, name="order-create"),
    path("settings/", views.quotation_settings_view, name="quotation-settings"),
    path("settings/asset/<str:asset_type>/", views.quotation_asset_serve, name="quotation-asset-serve"),
    path("api/customer-contacts/<int:customer_id>/", views.customer_contacts_api, name="order-customer-contacts-api"),
    path("<int:pk>/", views.order_detail, name="order-detail"),
    path("<int:pk>/edit/", views.order_update, name="order-update"),
    path("<int:pk>/new-version/", views.order_new_version, name="order-new-version"),
    path("<int:pk>/submit/", views.order_submit, name="order-submit"),
    path("<int:pk>/approve/", views.order_approve, name="order-approve"),
    path("<int:pk>/reject/", views.order_reject, name="order-reject"),
    path("<int:pk>/ship/", views.order_ship, name="order-ship"),
    path("<int:pk>/payments/report/", views.order_payment_report, name="order-payment-report"),
    path("payments/<int:payment_id>/confirm/", views.order_payment_confirm, name="order-payment-confirm"),
    path("payments/<int:payment_id>/reject/", views.order_payment_reject, name="order-payment-reject"),
    path("payments/<int:payment_id>/proof/", views.order_payment_proof_download, name="order-payment-proof"),
    path("<int:pk>/close/", views.order_close, name="order-close"),
    path("<int:pk>/cancel/", views.order_cancel, name="order-cancel"),
    path("<int:pk>/attachments/upload/", views.order_attachment_upload, name="order-attachment-upload"),
    path("attachments/<int:att_id>/download/", views.order_attachment_download, name="order-attachment-download"),
    path("attachments/<int:att_id>/delete/", views.order_attachment_delete, name="order-attachment-delete"),
    path("<int:pk>/quote/", views.order_quotation_print, name="order-quotation-print"),
    path("<int:pk>/quote/v/<int:version_no>/", views.order_quotation_print, name="order-quotation-version-print"),
]
