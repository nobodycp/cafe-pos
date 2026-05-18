"""مسارات API JSON تحت /app/api/v1/ — قراءة وبحث فقط."""

from django.urls import path

from apps.core.api import views as api_views

app_name = "shell_api"

urlpatterns = [
    path("customers/search/", api_views.api_customers_search, name="customers_search"),
    path("products/search/", api_views.api_products_search, name="products_search"),
    path("suppliers/search/", api_views.api_suppliers_search, name="suppliers_search"),
    path("categories/search/", api_views.api_categories_search, name="categories_search"),
    path("units/search/", api_views.api_units_search, name="units_search"),
    path("accounts/search/", api_views.api_accounts_search, name="accounts_search"),
    path("payment-methods/", api_views.api_payment_methods, name="payment_methods"),
]
