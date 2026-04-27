from django.urls import path

from apps.purchasing import views

app_name = "purchasing"

urlpatterns = [
    path("suppliers/", views.supplier_list, name="suppliers"),
    path("suppliers/balances/", views.supplier_balances, name="supplier_balances"),
    path("suppliers/create/", views.supplier_create, name="supplier_create"),
    path("suppliers/<int:pk>/", views.supplier_detail, name="supplier_detail"),
    path("suppliers/<int:pk>/edit/", views.supplier_edit, name="supplier_edit"),
    path("suppliers/<int:pk>/payment/", views.supplier_payment_create, name="supplier_payment"),
    path("suppliers/<int:pk>/purchase/", views.purchase_invoice_create, name="purchase_create"),
    path("suppliers/<int:pk>/link-customer/", views.supplier_link_customer, name="supplier_link_customer"),
    path("suppliers/<int:pk>/statement/", views.supplier_statement, name="supplier_statement"),
    path("purchase/new/", views.purchase_invoice_new, name="purchase_new"),
    path("purchase/list/", views.purchase_invoice_list, name="purchase_list"),
    path("purchase/<int:pk>/", views.purchase_invoice_detail, name="purchase_detail"),
    path("purchase/<int:pk>/return/", views.purchase_return_create, name="purchase_return"),
    path("commission-vendors/", views.commission_vendor_report, name="commission_vendors"),
]
