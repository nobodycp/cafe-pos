from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounting.forms import JournalEntryEditForm, make_journal_line_formset
from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.accounting.services import account_ledger, profit_and_loss, trial_balance
from apps.core.models import log_audit
from apps.core.pagination import paginate_queryset
from apps.inventory.models import StockBalance


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
    return render(request, "shell/accounting_chart.html", {"rows": rows})


@login_required
def trial_balance_view(request):
    rows = trial_balance()
    total_d = sum(r["total_debit"] for r in rows)
    total_c = sum(r["total_credit"] for r in rows)
    return render(request, "shell/accounting_trial_balance.html", {
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

    return render(request, "shell/accounting_pnl.html", {
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
    return render(request, "shell/accounting_ledger.html", {
        "account": acc,
        "rows": rows,
        "date_from": date_from or "",
        "date_to": date_to or "",
    })


@login_required
def journal_list(request):
    qs = JournalEntry.objects.select_related("work_session", "user").order_by("-date", "-created_at")
    ctx = {}
    ctx.update(paginate_queryset(request, qs))
    return render(request, "shell/accounting_journal_list.html", ctx)


@login_required
def journal_detail(request, pk):
    entry = get_object_or_404(JournalEntry.objects.select_related("work_session", "user"), pk=pk)
    lines = entry.lines.select_related("account").order_by("-debit", "credit")
    return render(
        request,
        "shell/accounting_journal_detail.html",
        {"entry": entry, "lines": lines, "can_edit_journal": not entry.is_reversed},
    )


@login_required
def journal_edit(request, pk):
    entry = get_object_or_404(JournalEntry.objects.select_related("work_session", "user"), pk=pk)
    if entry.is_reversed:
        messages.error(request, "لا يمكن تعديل قيد معكوس.")
        return redirect("shell:journal_detail", pk=entry.pk)

    LineFormSet = make_journal_line_formset()
    if request.method == "POST":
        entry_form = JournalEntryEditForm(request.POST, instance=entry)
        formset = LineFormSet(
            request.POST,
            queryset=JournalLine.objects.filter(entry=entry).select_related("account"),
        )
        if entry_form.is_valid() and formset.is_valid():
            with transaction.atomic():
                entry_form.save()
                instances = formset.save(commit=False)
                for obj in instances:
                    if obj.account_id and (obj.debit > 0 or obj.credit > 0):
                        obj.entry = entry
                        obj.save()
                for obj in formset.deleted_objects:
                    obj.delete()
            log_audit(
                request.user,
                "accounting.journal_entry.update",
                "accounting.JournalEntry",
                str(entry.pk),
                {"entry_number": entry.entry_number},
            )
            messages.success(request, "تم حفظ تعديلات القيد.")
            return redirect("shell:journal_detail", pk=entry.pk)
        messages.error(request, "راجع البيانات: القيد يجب أن يبقى متوازناً وسطرين على الأقل.")
    else:
        entry_form = JournalEntryEditForm(instance=entry)
        formset = LineFormSet(
            queryset=JournalLine.objects.filter(entry=entry).select_related("account"),
        )
    return render(
        request,
        "shell/accounting_journal_edit.html",
        {
            "entry": entry,
            "entry_form": entry_form,
            "formset": formset,
        },
    )
