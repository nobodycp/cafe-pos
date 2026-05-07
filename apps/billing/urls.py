from django.urls import path

from apps.billing import views

app_name = "billing"

urlpatterns = [
    path("invoices/", views.sale_invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/delete/", views.sale_invoice_delete, name="sale_invoice_delete"),
    path("invoices/<int:pk>/", views.sale_invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/edit/", views.sale_invoice_edit, name="sale_invoice_edit"),
    path("invoices/customer/<int:customer_id>/", views.customer_invoices, name="customer_invoices"),
    path("invoices/<int:invoice_pk>/return/", views.sale_return_create, name="sale_return"),
    path(
        "invoices/<int:invoice_pk>/returns/<int:return_pk>/delete/",
        views.sale_return_delete,
        name="sale_return_delete",
    ),
]
