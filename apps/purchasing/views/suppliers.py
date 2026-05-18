import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import Resolver404, resolve, reverse
from django.views.decorators.http import require_GET, require_POST

from apps.catalog.models import Product, Unit
from apps.core.models import log_audit
from apps.core.ledger_pagination import paginate_amount_ledger
from apps.core.list_filters import get_search_q, parse_date_range
from apps.core.pagination import paginate_queryset
from apps.core.panel import PanelFormInvalid, handle_panel_form, panelize_form
from apps.core.payment_methods import credit_method_codes, load_payment_method_rows
from apps.purchasing.forms import SupplierForm, SupplierPaymentForm
from apps.purchasing.supplier_list_filters import (
    COMMISSION_FILTER_CHOICES,
    LINKED_FILTER_CHOICES,
    NET_SIDE_CHOICES,
    SUPPLIER_SORT_CHOICES,
    apply_supplier_filters,
    parse_supplier_filters,
    supplier_filters_open,
    supplier_list_base_queryset,
)
from apps.purchasing.models import (
    PurchaseInvoice,
    PurchaseLine,
    PurchaseReturn,
    PurchaseReturnLine,
    Supplier,
    SupplierCafePurchase,
    SupplierLedgerEntry,
    SupplierPayment,
)
from apps.purchasing.purge_service import purge_purchase_invoice
from apps.purchasing.request_parsers import (
    payment_rows as _payment_rows,
    purchase_form_state as _purchase_form_state,
    purchase_lines_from_request as _purchase_lines_from_request,
    purchase_payments_from_request as _purchase_payments_from_request,
)
from apps.purchasing.services import post_purchase_invoice, record_supplier_payment
from apps.billing.models import SaleInvoiceLine

from apps.purchasing.views._helpers import (
    _apply_supplier_opening_balance,
    _purchasing_ctx,
    _purchasing_redirect,
    _purchasing_tpl,
    _supplier_opening_balance_from_ledger,
)


OPENING_BALANCE_LEDGER_NOTE = "رصيد افتتاحي"

def supplier_list(request):
    supplier_filters = parse_supplier_filters(request.GET)
    qs = apply_supplier_filters(supplier_list_base_queryset(), supplier_filters)

    totals_agg = qs.aggregate(
        sum_balance=Sum("balance"),
        sum_customer=Sum("cust_balance_ann"),
        sum_net=Sum("net_balance_ann"),
    )
    totals = {
        "sum_balance": (totals_agg["sum_balance"] or Decimal("0")).quantize(Decimal("0.01")),
        "sum_customer": (totals_agg["sum_customer"] or Decimal("0")).quantize(Decimal("0.01")),
        "sum_net": (totals_agg["sum_net"] or Decimal("0")).quantize(Decimal("0.01")),
    }

    pag = paginate_queryset(request, qs)
    enriched = []
    for s in pag["page_obj"]:
        cust_bal = (getattr(s, "cust_balance_ann", None) or Decimal("0")).quantize(Decimal("0.01"))
        net = (getattr(s, "net_balance_ann", None) or (s.balance - cust_bal)).quantize(Decimal("0.01"))
        enriched.append({
            "supplier": s,
            "customer_balance": cust_bal,
            "net_balance": net,
            "is_commission_vendor": bool(getattr(s, "is_commission_vendor", False)),
        })
    tpl = _purchasing_tpl(request, "shell/suppliers_list.html", "purchasing/suppliers.html")
    ctx = _purchasing_ctx(
        request,
        rows=enriched,
        supplier_filters=supplier_filters,
        filters_open=supplier_filters_open(supplier_filters),
        supplier_sort_choices=SUPPLIER_SORT_CHOICES,
        linked_filter_choices=LINKED_FILTER_CHOICES,
        commission_filter_choices=COMMISSION_FILTER_CHOICES,
        net_side_choices=NET_SIDE_CHOICES,
        supplier_totals=totals,
    )
    ctx.update(pag)
    return render(request, tpl, ctx)
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier.objects.select_related("linked_customer"), pk=pk)
    inv = supplier.purchase_invoices.order_by("-created_at")[:50]
    pay = supplier.payments.order_by("-created_at")[:50]
    led = supplier.ledger_entries.order_by("-created_at")[:100]
    cafe_purchases = supplier.cafe_purchases.select_related("sale_invoice").order_by("-created_at")[:50]
    cafe_agg = SupplierCafePurchase.objects.filter(supplier_id=supplier.pk).aggregate(s=Sum("amount"))
    cafe_purchases_total = (cafe_agg["s"] or Decimal("0")).quantize(Decimal("0.01"))
    net_balance = supplier.balance
    if supplier.linked_customer:
        net_balance = (supplier.balance - supplier.linked_customer.balance).quantize(Decimal("0.01"))
    is_commission_vendor = bool(supplier.is_commission_vendor)
    tpl = _purchasing_tpl(request, "shell/suppliers_detail.html", "purchasing/supplier_detail.html")
    return render(
        request,
        tpl,
        _purchasing_ctx(
            request,
            supplier=supplier,
            invoices=inv,
            payments=pay,
            ledger=led,
            cafe_purchases=cafe_purchases,
            cafe_purchases_total=cafe_purchases_total,
            net_balance=net_balance,
            is_commission_vendor=is_commission_vendor,
        ),
    )
def supplier_create(request):
    from apps.contacts.models import Customer

    if request.method == "POST":
        form = SupplierForm(request.POST)
        if form.is_valid():
            supplier = form.save()
            ob = (form.cleaned_data.get("opening_balance") or Decimal("0")).quantize(Decimal("0.01"))
            if ob < 0:
                ob = Decimal("0")
            _apply_supplier_opening_balance(supplier=supplier, amount=ob)
            if form.cleaned_data.get("also_customer"):
                cust = Customer.objects.create(
                    name_ar=supplier.name_ar,
                    name_en=supplier.name_en,
                    phone=supplier.phone,
                )
                supplier.linked_customer = cust
                supplier.save(update_fields=["linked_customer", "updated_at"])
            messages.success(request, f"تم إضافة المورد «{supplier.name_ar}» بنجاح")
            return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
    else:
        form = SupplierForm()
    tpl = _purchasing_tpl(request, "shell/suppliers_form.html", "purchasing/supplier_form.html")
    return render(request, tpl, _purchasing_ctx(request, form=form, title="إضافة مورد"))
def supplier_edit(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            new_ob = (form.cleaned_data.get("opening_balance") or Decimal("0")).quantize(Decimal("0.01"))
            if new_ob < 0:
                new_ob = Decimal("0")
            _apply_supplier_opening_balance(supplier=supplier, amount=new_ob)
            messages.success(request, "تم حفظ بيانات المورد.")
            return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
    else:
        form = SupplierForm(
            instance=supplier,
            initial={"opening_balance": _supplier_opening_balance_from_ledger(supplier)},
        )
    tpl = _purchasing_tpl(request, "shell/suppliers_form.html", "purchasing/supplier_form.html")
    return render(request, tpl, _purchasing_ctx(request, form=form, title="تعديل مورد", supplier=supplier))
def supplier_delete(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if PurchaseInvoice.objects.filter(supplier=supplier).exists():
        messages.error(
            request,
            "لا يمكن حذف المورد: توجد فواتير شراء مرتبطة. احذف أو ألغِ فواتير الشراء أولاً.",
        )
        return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
    Product.objects.filter(commission_vendor=supplier).update(commission_vendor=None)
    SupplierPayment.objects.filter(supplier=supplier).delete()
    SupplierLedgerEntry.objects.filter(supplier=supplier).delete()
    name = supplier.name_ar
    supplier.delete()
    messages.success(request, f"تم حذف المورد «{name}» نهائياً.")
    return _purchasing_redirect(request, "suppliers")
def supplier_payment_create(request, pk):
    supplier = get_object_or_404(Supplier.objects.select_related("linked_customer"), pk=pk)
    if request.method == "POST":
        form = SupplierPaymentForm(request.POST)
        if form.is_valid():
            try:
                record_supplier_payment(
                    supplier=supplier,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["method"],
                    note=form.cleaned_data["note"],
                    user=request.user,
                )
                messages.success(request, "تم تسجيل السداد بنجاح")
                return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
            except Exception as e:
                messages.error(request, f"حدث خطأ: {e}")
    else:
        form = SupplierPaymentForm()
    net_balance = supplier.balance
    if supplier.linked_customer:
        net_balance = (supplier.balance - supplier.linked_customer.balance).quantize(Decimal("0.01"))
    tpl = _purchasing_tpl(request, "shell/suppliers_payment.html", "purchasing/supplier_payment_form.html")
    return render(request, tpl, _purchasing_ctx(request, form=form, supplier=supplier, net_balance=net_balance))
def supplier_link_customer(request, pk):
    """Create a Customer record linked to this supplier, or link to existing."""
    from apps.contacts.models import Customer

    supplier = get_object_or_404(Supplier, pk=pk)
    if supplier.linked_customer_id:
        messages.info(request, f"المورد مرتبط بالفعل بحساب عميل: {supplier.linked_customer.name_ar}")
        return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)

    existing = Customer.objects.filter(
        name_ar=supplier.name_ar, phone=supplier.phone
    ).first()
    if existing:
        supplier.linked_customer = existing
        supplier.save(update_fields=["linked_customer", "updated_at"])
        messages.success(request, f"تم ربط المورد بحساب العميل الموجود «{existing.name_ar}»")
    else:
        cust = Customer.objects.create(
            name_ar=supplier.name_ar,
            name_en=supplier.name_en,
            phone=supplier.phone,
        )
        supplier.linked_customer = cust
        supplier.save(update_fields=["linked_customer", "updated_at"])
        messages.success(request, f"تم إنشاء حساب عميل للمورد «{supplier.name_ar}»")
    return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
