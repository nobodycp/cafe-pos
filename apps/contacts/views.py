from datetime import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, IntegerField, Max, Q, Sum, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.ledger_pagination import paginate_amount_ledger
from apps.core.list_filters import get_search_q
from apps.core.pagination import paginate_queryset
from apps.core.panel import PanelFormInvalid, handle_panel_form, panelize_form
from apps.contacts.forms import CustomerForm, CustomerPaymentForm
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.contacts.services import record_customer_payment


def _contacts_ctx(request, **kwargs):
    ctx = {"contacts_ns": "shell"}
    ctx.update(kwargs)
    return ctx


def _contacts_reverse(request, viewname, *args, **kwargs):
    return reverse(f"shell:{viewname}", args=args, kwargs=kwargs)


def _contacts_redirect(request, viewname, *args, **kwargs):
    return redirect(_contacts_reverse(request, viewname, *args, **kwargs))


def _contacts_tpl(request, shell_tpl, classic_tpl):
    return shell_tpl


def _customer_list_hide_zero_balance(request) -> bool:
    """
    إخفاء العملاء ذوي الرصيد (عليه) = 0.
    عند غياب المعامل: مفعّل افتراضياً (نموذج يرسل hidden=0 ثم checkbox=1).
    """
    parts = request.GET.getlist("hide_zero_balance")
    if not parts:
        return True
    last = (parts[-1] or "").strip().lower()
    return last not in ("0", "false", "off")


@login_required
def customer_list(request):
    qs = (
        Customer.objects.filter(is_active=True)
        .annotate(
            _customer_name_script_group=Case(
                When(name_ar__regex=r"^\s*[A-Za-z0-9]", then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("_customer_name_script_group", "name_ar", "pk")
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(phone__icontains=q))

    hide_zero_balance = _customer_list_hide_zero_balance(request)
    if hide_zero_balance:
        qs = qs.exclude(balance=Decimal("0"))

    totals_agg = qs.aggregate(sum_balance=Sum("balance"))
    totals = {
        "sum_balance": (totals_agg["sum_balance"] or Decimal("0")).quantize(Decimal("0.01")),
    }

    tpl = _contacts_tpl(request, "shell/customers_list.html", "contacts/customers.html")
    ctx = _contacts_ctx(
        request,
        q=q,
        customer_filter_hide_zero_balance=hide_zero_balance,
        customer_totals=totals,
    )
    ctx.update(paginate_queryset(request, qs))
    return render(request, tpl, ctx)


@login_required
def customer_detail(request, pk):
    c = get_object_or_404(Customer.objects.select_related("linked_supplier"), pk=pk)
    led = c.ledger_entries.order_by("-created_at")[:200]
    tpl = _contacts_tpl(request, "shell/customers_detail.html", "contacts/customer_detail.html")
    return render(request, tpl, _contacts_ctx(request, customer=c, ledger=led))


@login_required
def customer_create(request):
    from apps.contacts.services import replace_customer_opening_ledger
    from apps.core.decimalutil import as_decimal

    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save()
            opening_dec = as_decimal(form.cleaned_data.get("opening_balance") or 0).quantize(Decimal("0.01"))
            replace_customer_opening_ledger(customer=customer, opening=opening_dec)
            messages.success(request, f"تم إضافة العميل «{customer.name_ar}» بنجاح")
            return _contacts_redirect(request, "customer_detail", pk=customer.pk)
    else:
        form = CustomerForm()
    tpl = _contacts_tpl(request, "shell/customers_form.html", "contacts/customer_form.html")
    return render(
        request,
        tpl,
        _contacts_ctx(request, form=form, title="إضافة عميل", is_new=True),
    )


@login_required
def customer_edit(request, pk):
    from apps.contacts.services import replace_customer_opening_ledger
    from apps.core.decimalutil import as_decimal

    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            cust = form.save()
            opening_dec = as_decimal(form.cleaned_data.get("opening_balance") or 0).quantize(Decimal("0.01"))
            replace_customer_opening_ledger(customer=cust, opening=opening_dec)
            messages.success(request, "تم تعديل بيانات العميل بنجاح")
            return _contacts_redirect(request, "customer_detail", pk=cust.pk)
    else:
        form = CustomerForm(instance=customer)
    tpl = _contacts_tpl(request, "shell/customers_form.html", "contacts/customer_form.html")
    return render(
        request,
        tpl,
        _contacts_ctx(request, form=form, title="تعديل عميل", customer=customer),
    )


@login_required
@require_POST
@transaction.atomic
def customer_delete(request, pk):
    from apps.purchasing.models import Supplier

    customer = get_object_or_404(Customer, pk=pk)
    Supplier.objects.filter(linked_customer=customer).update(linked_customer=None)
    CustomerLedgerEntry.objects.filter(customer=customer).delete()
    name = customer.name_ar
    customer.delete()
    messages.success(request, f"تم حذف العميل «{name}» نهائياً.")
    return _contacts_redirect(request, "customers")


@login_required
@require_POST
@transaction.atomic
def customer_ledger_delete(request, pk, entry_pk):
    customer = get_object_or_404(Customer, pk=pk)
    entry = get_object_or_404(CustomerLedgerEntry, pk=entry_pk, customer=customer)
    entry.delete()
    customer.balance = customer.computed_balance
    customer.save(update_fields=["balance"])
    messages.success(request, "تم حذف القيد وتحديث رصيد العميل.")
    return _contacts_redirect(request, "customer_detail", pk=customer.pk)


@login_required
def customer_payment(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        form = CustomerPaymentForm(request.POST)
        if form.is_valid():
            try:
                record_customer_payment(
                    customer=customer,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["method"],
                    note=form.cleaned_data["note"],
                    user=request.user,
                )
                messages.success(request, "تم تسجيل السداد بنجاح")
                return _contacts_redirect(request, "customer_detail", pk=customer.pk)
            except Exception as e:
                messages.error(request, f"حدث خطأ: {e}")
    else:
        form = CustomerPaymentForm()
    tpl = _contacts_tpl(
        request,
        "shell/customers_payment.html",
        "contacts/customer_payment_form.html",
    )
    return render(request, tpl, _contacts_ctx(request, form=form, customer=customer))


@login_required
def customer_statement(request, pk):
    customer = get_object_or_404(Customer, pk=pk)

    date_from = request.GET.get("from")
    date_to = request.GET.get("to")

    try:
        date_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
    except ValueError:
        date_from = None
    try:
        date_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    except ValueError:
        date_to = None

    all_entries = customer.ledger_entries.order_by("created_at")

    if date_from:
        opening_agg = all_entries.filter(created_at__date__lt=date_from).aggregate(s=Sum("amount"))
        opening_balance = (opening_agg["s"] or Decimal("0")).quantize(Decimal("0.01"))
        entries = all_entries.filter(created_at__date__gte=date_from)
    else:
        opening_balance = Decimal("0.00")
        entries = all_entries

    if date_to:
        entries = entries.filter(created_at__date__lte=date_to)

    def _statement_row(e, running):
        return {
            "date": e.created_at,
            "type": e.get_entry_type_display(),
            "entry_type": e.entry_type,
            "amount": e.amount,
            "running": running,
            "reference": e.note or e.reference_model,
            "reference_model": e.reference_model,
            "reference_pk": e.reference_pk,
        }

    stmt_pag = paginate_amount_ledger(
        request,
        entries,
        opening_balance=opening_balance,
        build_row=_statement_row,
    )

    tpl = _contacts_tpl(
        request,
        "shell/customers_statement.html",
        "contacts/customer_statement.html",
    )
    ctx = _contacts_ctx(
        request,
        customer=customer,
        rows=stmt_pag["rows"],
        opening_balance=opening_balance,
        closing_balance=stmt_pag["closing_balance"],
        page_opening_balance=stmt_pag["page_opening_balance"],
        date_from=date_from,
        date_to=date_to,
    )
    ctx.update(stmt_pag)
    return render(request, tpl, ctx)


@login_required
def customer_balances(request):
    qs = (
        Customer.objects.filter(is_active=True)
        .exclude(balance=Decimal("0"))
        .annotate(last_txn=Max("ledger_entries__created_at"))
        .order_by("name_ar")
    )
    q = get_search_q(request)
    if q:
        qs = qs.filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(phone__icontains=q))

    grand_agg = qs.aggregate(s=Sum("balance"))
    grand_total = (grand_agg["s"] or Decimal("0")).quantize(Decimal("0.01"))

    tpl = _contacts_tpl(
        request,
        "shell/customers_balances.html",
        "contacts/customer_balances.html",
    )
    ctx = _contacts_ctx(request, q=q, grand_total=grand_total)
    pag = paginate_queryset(request, qs)
    ctx["results"] = [
        {"customer": c, "balance": c.balance, "last_txn": c.last_txn}
        for c in pag["page_obj"]
    ]
    ctx.update(pag)
    return render(request, tpl, ctx)


@login_required
def customer_create_panel(request):
    from apps.contacts.services import replace_customer_opening_ledger
    from apps.core.decimalutil import as_decimal

    tpl = "shell/panels/customer_create_panel.html"

    def build_context():
        form = CustomerForm(request.POST or None)
        panelize_form(form)
        return {
            "form": form,
            "form_action": reverse("shell:customer_create_panel"),
            "panel_title": "إضافة عميل",
            "is_new": True,
        }

    def on_valid():
        form = CustomerForm(request.POST)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        customer = form.save()
        opening_dec = as_decimal(form.cleaned_data.get("opening_balance") or 0).quantize(Decimal("0.01"))
        replace_customer_opening_ledger(customer=customer, opening=opening_dec)

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)


@login_required
def customer_edit_panel(request, pk):
    from apps.contacts.services import replace_customer_opening_ledger
    from apps.core.decimalutil import as_decimal

    customer = get_object_or_404(Customer, pk=pk)
    tpl = "shell/panels/customer_edit_panel.html"

    def build_context():
        form = CustomerForm(request.POST or None, instance=customer)
        panelize_form(form)
        return {
            "form": form,
            "customer": customer,
            "form_action": reverse("shell:customer_edit_panel", args=[pk]),
            "panel_title": "تعديل عميل",
        }

    def on_valid():
        form = CustomerForm(request.POST, instance=customer)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        cust = form.save()
        opening_dec = as_decimal(form.cleaned_data.get("opening_balance") or 0).quantize(Decimal("0.01"))
        replace_customer_opening_ledger(customer=cust, opening=opening_dec)

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)
