from django.urls import path
from . import views

urlpatterns = [
    # Customer Licenses
    path("", views.license_list, name="license-list"),
    path("export/", views.license_export, name="license-export"),
    path("create/", views.license_create, name="license-create"),
    path("batch-approve/", views.license_batch_approve, name="license-batch-approve"),
    path("<int:pk>/", views.license_detail, name="license-detail"),
    path("<int:pk>/edit/", views.license_update, name="license-update"),
    path("<int:pk>/delete/", views.license_delete, name="license-delete"),
    path("<int:pk>/approve/", views.license_approve, name="license-approve"),
    path("<int:pk>/reject/", views.license_reject, name="license-reject"),

    # Products
    path("products/", views.product_list, name="product-list"),
    path("products/create/", views.product_create, name="product-create"),
    path("products/<int:pk>/edit/", views.product_update, name="product-update"),
    path("products/<int:pk>/delete/", views.product_delete, name="product-delete"),

    # Dynamic API
    path("api/products/<int:pk>/meta/", views.product_meta_api, name="product-meta-api"),
]
