from django.urls import path

from apps.accounting import views

app_name = "accounting"

urlpatterns = [
    path("accounts/", views.chart_of_accounts, name="chart"),
    path("accounts/<int:pk>/ledger/", views.account_ledger_view, name="account_ledger"),
    path("trial-balance/", views.trial_balance_view, name="trial_balance"),
    path("pnl/", views.pnl_view, name="pnl"),
    path("journal/", views.journal_list, name="journal_list"),
    path("journal/<int:pk>/", views.journal_detail, name="journal_detail"),
]
