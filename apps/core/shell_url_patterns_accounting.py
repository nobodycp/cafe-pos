"""مسارات المحاسبة والخزينة تحت غلاف /app/ — تُدمج في apps.core.shell_urls."""

from django.urls import path

from apps.accounting import views as accounting_views
from apps.core import views as core_views

urlpatterns_accounting = [
    path("accounting/accounts/", accounting_views.chart_of_accounts, name="accounting_chart"),
    path("accounting/accounts/<int:pk>/ledger/", accounting_views.account_ledger_view, name="account_ledger"),
    path("accounting/trial-balance/", accounting_views.trial_balance_view, name="trial_balance"),
    path("accounting/pnl/", accounting_views.pnl_view, name="pnl"),
    path("accounting/journal/", accounting_views.journal_list, name="journal_list"),
    path("accounting/journal/<int:pk>/", accounting_views.journal_detail, name="journal_detail"),
    path("accounting/journal/<int:pk>/edit/", accounting_views.journal_edit, name="journal_edit"),
    path("accounting/treasury/", core_views.treasury, name="accounting_treasury"),
    path("accounting/treasury/party-search/", core_views.treasury_party_search, name="treasury_party_search"),
    path(
        "accounting/treasury/customers/quick-create/",
        core_views.treasury_customer_quick_create,
        name="treasury_customer_quick_create",
    ),
]
