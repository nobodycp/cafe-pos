from django.urls import path

from apps.contacts import views

app_name = "contacts"

urlpatterns = [
    path("", views.customer_list, name="customers"),
    path("balances/", views.customer_balances, name="customer_balances"),
    path("create/", views.customer_create, name="customer_create"),
    path("<int:pk>/", views.customer_detail, name="customer_detail"),
    path("<int:pk>/edit/", views.customer_edit, name="customer_edit"),
    path("<int:pk>/payment/", views.customer_payment, name="customer_payment"),
    path("<int:pk>/statement/", views.customer_statement, name="customer_statement"),
]
