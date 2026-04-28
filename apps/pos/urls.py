from django.urls import path

from apps.pos import views

app_name = "pos"

urlpatterns = [
    path("", views.pos_main, name="main"),
    path("tables/", views.tables_floor, name="tables_floor"),
    path("tables/open/", views.table_open, name="table_open"),
    path("tables/quick-create/", views.table_quick_create, name="table_quick_create"),
    path("customers/search/", views.customers_search, name="customers_search"),
    path("products/search/", views.products_search, name="products_search"),
    path("customers/quick-create/", views.customer_quick_create, name="customer_quick_create"),
    path("order/new/", views.order_new, name="order_new"),
    path("order/<int:order_id>/resume/", views.order_resume, name="order_resume"),
    path("order/<int:order_id>/add/", views.order_add_product, name="order_add"),
    path("order/<int:order_id>/custom/<int:product_id>/", views.customize_product, name="customize_product"),
    path("order/<int:order_id>/line/<int:line_id>/adjust/", views.order_adjust_line, name="order_adjust_line"),
    path("order/<int:order_id>/remove/<int:line_id>/", views.order_remove_line, name="order_remove"),
    path("order/<int:order_id>/line/<int:line_id>/note/", views.order_line_note, name="order_line_note"),
    path("order/<int:order_id>/line/<int:line_id>/unit-price/", views.order_line_unit_price, name="order_line_unit_price"),
    path("order/<int:order_id>/customer/", views.order_set_customer, name="order_customer"),
    path("order/<int:order_id>/note/", views.order_note, name="order_note"),
    path("order/<int:order_id>/discount/", views.order_discount, name="order_discount"),
    path("order/<int:order_id>/split/", views.order_split, name="order_split"),
    path("order/<int:order_id>/cancel/", views.order_cancel, name="order_cancel"),
    path("order/<int:order_id>/hold/", views.order_hold, name="order_hold"),
    path("order/<int:order_id>/checkout/", views.order_checkout, name="order_checkout"),
    path("kitchen/<int:order_id>/batch/<int:batch_no>/", views.kitchen_ticket, name="kitchen_ticket"),
    path("cart-fragment/", views.cart_fragment, name="cart_fragment"),
    path("receipt/<int:invoice_id>/", views.receipt_print, name="receipt"),
    path("receipt/<int:invoice_id>/raw/", views.receipt_raw, name="receipt_raw"),
]
