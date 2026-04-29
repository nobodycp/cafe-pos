from django.urls import path

from apps.contacts import legacy_redirects
from apps.contacts import views

app_name = "contacts"

urlpatterns = [
    path("", legacy_redirects.redirect_legacy_customers_root, name="customers"),
    path("balances/", legacy_redirects.redirect_legacy_customers_balances, name="customer_balances"),
    path("create/", legacy_redirects.legacy_customer_create, name="customer_create"),
    path("<int:pk>/", legacy_redirects.redirect_legacy_customer_detail, name="customer_detail"),
    path("<int:pk>/edit/", legacy_redirects.legacy_customer_edit, name="customer_edit"),
    path("<int:pk>/delete/", legacy_redirects.legacy_customer_delete, name="customer_delete"),
    path("<int:pk>/ledger/<int:entry_pk>/delete/", views.customer_ledger_delete, name="customer_ledger_delete"),
    path("<int:pk>/payment/", legacy_redirects.legacy_customer_payment, name="customer_payment"),
    path("<int:pk>/statement/", legacy_redirects.legacy_customer_statement, name="customer_statement"),
]
