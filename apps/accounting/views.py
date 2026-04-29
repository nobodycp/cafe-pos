from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import Sum
from django.shortcuts import get_object_or_404, render

from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.accounting.services import account_ledger, profit_and_loss, trial_balance
from apps.inventory.models import StockBalance


def _accounting_tpl(request, shell_tpl, classic_tpl):
    ns = (getattr(request.resolver_match, "namespace", "") or "").strip()
    return shell_tpl if ns == "shell" else classic_tpl


@login_required
def chart_of_accounts(request):
    accounts = (
        Account.objects.filter(is_active=True)
        .annotate(total_debit=Sum("journal_lines__debit"), total_credit=Sum("journal_lines__credit"))
        .order_by("code")
    )
    rows = []
    for acc in accounts:
        td = acc.total_debit or Decimal("0")
        tc = acc.total_credit or Decimal("0")
        if acc.account_type in (Account.AccountType.ASSET, Account.AccountType.EXPENSE):
            balance = td - tc
        else:
            balance = tc - td
        rows.append({"account": acc, "total_debit": td, "total_credit": tc, "balance": balance})
    return render(request, _accounting_tpl(request, "shell/accounting_chart.html", "accounting/chart_of_accounts.html"), {"rows": rows})


@login_required
def trial_balance_view(request):
    rows = trial_balance()
    total_d = sum(r["total_debit"] for r in rows)
    total_c = sum(r["total_credit"] for r in rows)
    return render(request, _accounting_tpl(request, "shell/accounting_trial_balance.html", "accounting/trial_balance.html"), {
        "rows": rows,
        "total_debit": total_d,
        "total_credit": total_c,
    })


@login_required
def pnl_view(request):
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    pnl = profit_and_loss(date_from=date_from, date_to=date_to)

    rev_qs = Account.objects.filter(account_type=Account.AccountType.REVENUE, is_active=True).annotate(
        total_debit=Sum("journal_lines__debit"), total_credit=Sum("journal_lines__credit")
    )
    rev_detail = []
    for acc in rev_qs:
        td = acc.total_debit or Decimal("0")
        tc = acc.total_credit or Decimal("0")
        b = tc - td  # revenue: credit - debit
        if b != 0:
            rev_detail.append({"account": acc, "balance": b})

    exp_qs = Account.objects.filter(account_type=Account.AccountType.EXPENSE, is_active=True).annotate(
        total_debit=Sum("journal_lines__debit"), total_credit=Sum("journal_lines__credit")
    )
    exp_detail = []
    for acc in exp_qs:
        td = acc.total_debit or Decimal("0")
        tc = acc.total_credit or Decimal("0")
        b = td - tc  # expense: debit - credit
        if b != 0:
            exp_detail.append({"account": acc, "balance": b})

    inv_val = StockBalance.objects.aggregate(
        val=Sum(models.F("quantity_on_hand") * models.F("average_cost"))
    )["val"] or Decimal("0")

    return render(request, _accounting_tpl(request, "shell/accounting_pnl.html", "accounting/pnl.html"), {
        "pnl": pnl,
        "revenue_detail": rev_detail,
        "expense_detail": exp_detail,
        "inventory_valuation": inv_val.quantize(Decimal("0.01")),
        "date_from": date_from or "",
        "date_to": date_to or "",
    })


@login_required
def account_ledger_view(request, pk):
    acc = get_object_or_404(Account, pk=pk)
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    rows = account_ledger(acc, date_from=date_from, date_to=date_to)
    return render(request, _accounting_tpl(request, "shell/accounting_ledger.html", "accounting/account_ledger.html"), {
        "account": acc,
        "rows": rows,
        "date_from": date_from or "",
        "date_to": date_to or "",
    })


@login_required
def journal_list(request):
    entries = JournalEntry.objects.select_related("work_session", "user").order_by("-date", "-created_at")[:200]
    return render(request, _accounting_tpl(request, "shell/accounting_journal_list.html", "accounting/journal_list.html"), {"entries": entries})


@login_required
def journal_detail(request, pk):
    entry = get_object_or_404(JournalEntry.objects.select_related("work_session", "user"), pk=pk)
    lines = entry.lines.select_related("account").order_by("-debit", "credit")
    return render(request, _accounting_tpl(request, "shell/accounting_journal_detail.html", "accounting/journal_detail.html"), {"entry": entry, "lines": lines})
