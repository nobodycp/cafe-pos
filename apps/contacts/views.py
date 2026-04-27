from datetime import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max, Sum
from django.shortcuts import get_object_or_404, redirect, render

from apps.contacts.forms import CustomerForm, CustomerPaymentForm
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.contacts.services import record_customer_payment


@login_required
def customer_list(request):
    qs = Customer.objects.filter(is_active=True).order_by("name_ar")
    return render(request, "contacts/customers.html", {"customers": qs})


@login_required
def customer_detail(request, pk):
    c = get_object_or_404(Customer.objects.select_related("linked_supplier"), pk=pk)
    led = c.ledger_entries.order_by("-created_at")[:200]
    return render(request, "contacts/customer_detail.html", {"customer": c, "ledger": led})


@login_required
def customer_create(request):
    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save()
            opening = form.cleaned_data.get("opening_balance") or Decimal("0")
            if opening > 0:
                customer.balance = opening
                customer.save(update_fields=["balance"])
                CustomerLedgerEntry.objects.create(
                    customer=customer,
                    entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
                    amount=opening,
                    note="رصيد افتتاحي",
                    reference_model="contacts.Customer",
                    reference_pk=str(customer.pk),
                )
            messages.success(request, f"تم إضافة العميل «{customer.name_ar}» بنجاح")
            return redirect("contacts:customer_detail", pk=customer.pk)
    else:
        form = CustomerForm()
    return render(request, "contacts/customer_form.html", {"form": form, "title": "إضافة عميل", "is_new": True})


@login_required
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل بيانات العميل بنجاح")
            return redirect("contacts:customer_detail", pk=customer.pk)
    else:
        form = CustomerForm(instance=customer)
    return render(request, "contacts/customer_form.html", {"form": form, "title": "تعديل عميل", "customer": customer})


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
                return redirect("contacts:customer_detail", pk=customer.pk)
            except Exception as e:
                messages.error(request, f"حدث خطأ: {e}")
    else:
        form = CustomerPaymentForm()
    return render(request, "contacts/customer_payment_form.html", {"form": form, "customer": customer})


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

    rows = []
    running = opening_balance
    for e in entries:
        running += e.amount
        rows.append({
            "date": e.created_at,
            "type": e.get_entry_type_display(),
            "entry_type": e.entry_type,
            "amount": e.amount,
            "running": running.quantize(Decimal("0.01")),
            "reference": e.note or e.reference_model,
        })

    closing_balance = running.quantize(Decimal("0.01"))

    return render(request, "contacts/customer_statement.html", {
        "customer": customer,
        "rows": rows,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "date_from": date_from,
        "date_to": date_to,
    })


@login_required
def customer_balances(request):
    customers = Customer.objects.filter(is_active=True).annotate(
        last_txn=Max("ledger_entries__created_at"),
    ).order_by("name_ar")

    results = []
    grand_total = Decimal("0.00")
    for c in customers:
        bal = c.computed_balance
        if bal != Decimal("0"):
            results.append({
                "customer": c,
                "balance": bal,
                "last_txn": c.last_txn,
            })
            grand_total += bal

    grand_total = grand_total.quantize(Decimal("0.01"))

    return render(request, "contacts/customer_balances.html", {
        "results": results,
        "grand_total": grand_total,
    })
