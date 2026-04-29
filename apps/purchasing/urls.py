from django.urls import path

from apps.purchasing import views

app_name = "purchasing"

urlpatterns = [
    path("suppliers/", views.supplier_list, name="suppliers"),
    path("suppliers/balances/", views.supplier_balances, name="supplier_balances"),
    path("suppliers/create/", views.supplier_create, name="supplier_create"),
    path("suppliers/<int:pk>/", views.supplier_detail, name="supplier_detail"),
    path("suppliers/<int:pk>/edit/", views.supplier_edit, name="supplier_edit"),
    path("suppliers/<int:pk>/delete/", views.supplier_delete, name="supplier_delete"),
    path("suppliers/<int:pk>/payment/", views.supplier_payment_create, name="supplier_payment"),
    path("suppliers/<int:pk>/purchase/", views.purchase_invoice_create, name="purchase_create"),
    path("suppliers/<int:pk>/link-customer/", views.supplier_link_customer, name="supplier_link_customer"),
    path("suppliers/<int:pk>/statement/", views.supplier_statement, name="supplier_statement"),
    path("purchase/new/", views.purchase_invoice_new, name="purchase_new"),
    path("api/purchase-suppliers/search/", views.purchase_suppliers_search, name="purchase_suppliers_search"),
    path("api/purchase-suppliers/quick-create/", views.purchase_supplier_quick_create, name="purchase_supplier_quick_create"),
    path("api/purchase-units/search/", views.purchase_units_search, name="purchase_units_search"),
    path("api/purchase-units/quick-create/", views.purchase_unit_quick_create, name="purchase_unit_quick_create"),
    path("api/purchase-products/search/", views.purchase_products_search, name="purchase_products_search"),
    path("api/purchase-products/quick-create/", views.purchase_product_quick_create, name="purchase_product_quick_create"),
    path("purchase/list/", views.purchase_invoice_list, name="purchase_list"),
    path("purchase/<int:pk>/delete/", views.purchase_invoice_delete, name="purchase_delete"),
    path("purchase/<int:pk>/", views.purchase_invoice_detail, name="purchase_detail"),
    path("purchase/<int:pk>/return/", views.purchase_return_create, name="purchase_return"),
    path("commission-vendors/", views.commission_vendor_report, name="commission_vendors"),
]
