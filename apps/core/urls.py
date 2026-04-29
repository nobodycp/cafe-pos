from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("treasury/", views.treasury, name="treasury"),
    path("api/treasury/party-search/", views.treasury_party_search, name="treasury_party_search"),
    path("session/open/", views.open_session_view, name="session_open"),
    path("session/close/", views.close_session_view, name="session_close"),
    path("session/summary/", views.session_summary, name="session_summary"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/payment-methods/", views.payment_method_list, name="payment_method_list"),
    path("settings/payment-methods/new/", views.payment_method_create, name="payment_method_create"),
    path("settings/payment-methods/<int:pk>/edit/", views.payment_method_update, name="payment_method_update"),
    path("settings/payment-methods/<int:pk>/delete/", views.payment_method_delete, name="payment_method_delete"),
    path("settings/tables/", views.tables_list, name="tables_list"),
    path("settings/tables/create/", views.table_create, name="table_create"),
    path("settings/tables/<int:pk>/edit/", views.table_edit, name="table_edit"),
    path("settings/tables/<int:pk>/toggle/", views.table_toggle, name="table_toggle"),
]
