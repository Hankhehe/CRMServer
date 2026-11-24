from django.urls import path
from . import views

urlpatterns = [
    path("", views.customer_list, name="customer-list"),
    path("custom-fields/", views.customer_custom_fields_setting, name="customer-custom-fields"),
    path("export/", views.customer_export, name="customer-export"),
    path("import/", views.customer_import, name="customer-import"),
    path("import/template/", views.customer_import_template, name="customer-import-template"),
    path("create/", views.customer_create, name="customer-create"),
    path("<int:pk>/", views.customer_detail, name="customer-detail"),
    path("<int:pk>/edit/", views.customer_update, name="customer-update"),
    path("<int:pk>/delete/", views.customer_delete, name="customer-delete"),
    # Contacts
    path("<int:customer_pk>/contacts/create/", views.contact_create, name="contact-create"),
    path("contacts/<int:pk>/edit/", views.contact_update, name="contact-update"),
    path("contacts/<int:pk>/delete/", views.contact_delete, name="contact-delete"),
    # Contracts
    path("contracts/", views.contract_list, name="contract-list"),
    path("contracts/create/", views.contract_create, name="contract-create"),
    path("contracts/batch-approve/", views.contract_batch_approve, name="contract-batch-approve"),
    path("<int:customer_pk>/contracts/create/", views.contract_create, name="customer-contract-create"),
    path("contracts/<int:pk>/edit/", views.contract_update, name="contract-update"),
    path("contracts/<int:pk>/approve/", views.contract_approve, name="contract-approve"),
    path("contracts/<int:pk>/reject/", views.contract_reject, name="contract-reject"),
    path("contracts/<int:pk>/swap-probe/", views.contract_swap_probe, name="contract-swap-probe"),
    path("contracts/<int:contract_pk>/attachments/upload/", views.contract_attachment_upload, name="contract-attachment-upload"),
    path("contracts/attachments/<int:pk>/delete/", views.contract_attachment_delete, name="contract-attachment-delete"),
    path("contracts/<int:pk>/delete/", views.contract_delete, name="contract-delete"),
    path("api/customers/<int:customer_id>/contract-candidates/", views.api_customer_contract_candidates, name="api-customer-contract-candidates"),
    # Customer Activity Logs
    path("<int:customer_pk>/activities/list/", views.api_customer_activity_logs, name="api-customer-activity-logs"),
    path("<int:customer_pk>/activities/create/", views.activity_log_create, name="activity-create"),
    path("activities/<int:pk>/edit/", views.activity_log_update, name="activity-update"),
    path("activities/<int:pk>/delete/", views.activity_log_delete, name="activity-delete"),
    path("attachments/<int:pk>/delete/", views.activity_attachment_delete, name="activity-attachment-delete"),
    # Customer Custom Info / Environment Sections
    path("<int:customer_pk>/info-sections/create/", views.info_section_create, name="info-section-create"),
    path("info-sections/<int:pk>/edit/", views.info_section_update, name="info-section-update"),
    path("info-sections/<int:pk>/delete/", views.info_section_delete, name="info-section-delete"),
    # Customer Follow
    path("<int:pk>/follow/", views.customer_follow_toggle, name="customer-follow-toggle"),
    path("<int:pk>/followers/manage/", views.customer_follower_manage, name="customer-follower-manage"),
    # Customer Hierarchy Quick Actions
    path("<int:pk>/children/bind/", views.customer_bind_child, name="customer-bind-child"),
    path("<int:pk>/children/<int:child_pk>/unbind/", views.customer_unbind_child, name="customer-unbind-child"),
]
