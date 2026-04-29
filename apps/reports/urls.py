from django.urls import path

from apps.reports import views

app_name = "reports"

urlpatterns = [
    path("", views.reports_dashboard, name="dashboard"),
    path("daily-sales/", views.daily_sales_report, name="daily_sales"),
    path("expenses/", views.expense_report, name="expense_report"),
    path("weekly/", views.weekly_report, name="weekly_report"),
    path("product-movement/", views.product_movement_report, name="product_movement"),
    path("cash-flow/", views.cash_flow_report, name="cash_flow"),
    path("payroll/", views.payroll_report, name="payroll_report"),
    path("payment-channels/", views.payment_channels_report, name="payment_channels"),
]
