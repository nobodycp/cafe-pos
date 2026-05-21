from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounting.forms import (
    JournalEntryEditForm,
    ManualJournalTransferForm,
    make_journal_line_formset,
    make_manual_journal_line_formset,
)
from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.accounting.services import (
    account_ledger,
    create_manual_journal_entry,
    create_manual_transfer_entry,
    profit_and_loss,
    trial_balance,
    trial_balance_grand_totals,
)
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
    total_d, total_c = trial_balance_grand_totals()
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
    from django.db.models import Q

    from apps.accounting.services import paginated_account_ledger_context
    from apps.core.list_filters import parse_iso_date
    from apps.core.pagination import paginate_queryset

    acc = get_object_or_404(Account, pk=pk)
    date_from = parse_iso_date(request.GET.get("from"))
    date_to = parse_iso_date(request.GET.get("to"))
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    line_q = Q(account=acc)
    if date_from:
        line_q &= Q(entry__date__gte=date_from)
    if date_to:
        line_q &= Q(entry__date__lte=date_to)

    lines_qs = (
        JournalLine.objects.filter(line_q)
        .select_related("entry")
        .order_by("entry__date", "entry__created_at", "pk")
    )

    pag = paginate_queryset(request, lines_qs)
    page = pag["page_obj"]
    start_idx = page.start_index() if callable(page.start_index) else page.start_index
    ledger = paginated_account_ledger_context(
        acc,
        lines_qs=lines_qs,
        date_from=date_from,
        date_to=date_to,
        page=page,
        start_idx=start_idx,
    )

    ctx = {
        "account": acc,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        **ledger,
    }
    ctx.update(pag)
    return render(request, "shell/accounting_ledger.html", ctx)


def _journal_entry_detail_queryset():
    return JournalEntry.objects.select_related("work_session", "user")


def _journal_detail_back_url() -> str:
    return reverse("shell:journal_list")


def _redirect_open_journal_entry_to(url: str, pk: int):
    sep = "&" if "?" in url else "?"
    return redirect(f"{url}{sep}view_journal_entry={pk}")


def _redirect_open_journal_entry(request, pk: int):
    from apps.core.nav_back import safe_return_path

    dest = safe_return_path(request.GET.get("return", ""))
    if not dest and request.method == "POST":
        dest = safe_return_path(request.POST.get("next", ""))
    if not dest:
        dest = safe_return_path(request.META.get("HTTP_REFERER", ""))
    if not dest:
        dest = _journal_detail_back_url()
    return _redirect_open_journal_entry_to(dest, pk)


def _journal_entry_detail_context(entry: JournalEntry) -> dict:
    lines = entry.lines.select_related("account").order_by("-debit", "credit")
    totals = lines.aggregate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    return {
        "entry": entry,
        "lines": lines,
        "can_edit_journal": not entry.is_reversed,
        "lines_total_debit": totals["total_debit"] or Decimal("0"),
        "lines_total_credit": totals["total_credit"] or Decimal("0"),
    }


@login_required
def journal_list(request):
    from apps.accounting.journal_list_filters import (
        apply_journal_filters,
        journal_filters_open,
        parse_journal_filters,
    )
    from apps.core.list_filters import iso_date_str

    journal_filters = parse_journal_filters(request)
    qs = JournalEntry.objects.select_related("work_session", "user").order_by("-date", "-created_at", "-pk")
    qs = apply_journal_filters(qs, journal_filters)
    ctx = {
        "journal_filters": journal_filters,
        "filters_open": journal_filters_open(journal_filters),
        "date_from": iso_date_str(journal_filters["date_from"]),
        "date_to": iso_date_str(journal_filters["date_to"]),
    }
    ctx.update(paginate_queryset(request, qs))
    return render(request, "shell/accounting_journal_list.html", ctx)


@login_required
def journal_detail(request, pk):
    """الرابط القديم — يعيد التوجيه لفتح النافذة المنبثقة."""
    get_object_or_404(_journal_entry_detail_queryset(), pk=pk)
    dest = _journal_detail_back_url()
    from apps.core.nav_back import safe_return_path

    return_path = safe_return_path(request.GET.get("return", ""))
    if return_path:
        dest = return_path
    return _redirect_open_journal_entry_to(dest, pk)


@login_required
@require_GET
def journal_detail_panel(request, pk):
    """HTML جزئي لعرض القيد داخل النافذة المنبثقة."""
    entry = get_object_or_404(_journal_entry_detail_queryset(), pk=pk)
    return render(
        request,
        "shell/_journal_entry_detail_modal_fragment.html",
        _journal_entry_detail_context(entry),
    )


@login_required
def journal_create(request):
    """إنشاء قيد يدوي جديد بأسطر متعددة."""
    LineFormSet = make_manual_journal_line_formset()
    if request.method == "POST":
        entry_form = JournalEntryEditForm(request.POST)
        formset = LineFormSet(request.POST, queryset=JournalLine.objects.none())
        if entry_form.is_valid() and formset.is_valid():
            lines = []
            for form in formset.forms:
                if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                    continue
                if form.cleaned_data.get("DELETE"):
                    continue
                account = form.cleaned_data.get("account")
                debit = Decimal(form.cleaned_data.get("debit") or 0).quantize(Decimal("0.01"))
                credit = Decimal(form.cleaned_data.get("credit") or 0).quantize(Decimal("0.01"))
                if account is None and debit == 0 and credit == 0:
                    continue
                lines.append((account, debit, credit, form.cleaned_data.get("description") or ""))
            with transaction.atomic():
                entry = create_manual_journal_entry(
                    date=entry_form.cleaned_data["date"],
                    description=entry_form.cleaned_data.get("description") or "",
                    lines=lines,
                    user=request.user,
                )
            log_audit(
                request.user,
                "accounting.journal_entry.create",
                "accounting.JournalEntry",
                str(entry.pk),
                {"entry_number": entry.entry_number, "mode": "manual"},
            )
            messages.success(request, f"تم إنشاء القيد {entry.entry_number}.")
            return _redirect_open_journal_entry(request, entry.pk)
        messages.error(request, "راجع البيانات: القيد يجب أن يكون متوازناً وسطرين على الأقل.")
    else:
        entry_form = JournalEntryEditForm(initial={"date": timezone.now().date()})
        formset = LineFormSet(queryset=JournalLine.objects.none())
    return render(
        request,
        "shell/accounting_journal_create.html",
        {
            "entry_form": entry_form,
            "formset": formset,
            "accounts_search_url": reverse("shell:shell_api:accounts_search"),
        },
    )


@login_required
def journal_transfer(request):
    """نقل مبلغ بين حسابين — قيد يدوي بسطرين."""
    if request.method == "POST":
        form = ManualJournalTransferForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                entry = create_manual_transfer_entry(
                    date=form.cleaned_data["date"],
                    description=form.cleaned_data["description"],
                    from_account=form.cleaned_data["from_account"],
                    to_account=form.cleaned_data["to_account"],
                    amount=form.cleaned_data["amount"],
                    user=request.user,
                )
            log_audit(
                request.user,
                "accounting.journal_entry.create",
                "accounting.JournalEntry",
                str(entry.pk),
                {"entry_number": entry.entry_number, "mode": "transfer"},
            )
            messages.success(request, f"تم تسجيل النقل في القيد {entry.entry_number}.")
            return _redirect_open_journal_entry(request, entry.pk)
        messages.error(request, "راجع بيانات النقل بين الحسابين.")
    else:
        form = ManualJournalTransferForm(initial={"date": timezone.now().date()})
    return render(
        request,
        "shell/accounting_journal_transfer.html",
        {
            "form": form,
            "accounts_search_url": reverse("shell:shell_api:accounts_search"),
        },
    )


@login_required
def journal_edit(request, pk):
    entry = get_object_or_404(JournalEntry.objects.select_related("work_session", "user"), pk=pk)
    if entry.is_reversed:
        messages.error(request, "لا يمكن تعديل قيد معكوس.")
        return _redirect_open_journal_entry(request, entry.pk)

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
            return _redirect_open_journal_entry(request, entry.pk)
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
