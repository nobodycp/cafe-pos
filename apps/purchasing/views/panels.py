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
    _apply_general_discount,
    _apply_supplier_opening_balance,
    _purchase_invoice_detail_context,
    _purchase_invoice_detail_queryset,
    _purchasing_ctx,
    _supplier_opening_balance_from_ledger,
)


OPENING_BALANCE_LEDGER_NOTE = "رصيد افتتاحي"

def purchase_invoice_create_panel(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    errors: list[str] = []
    tpl = "shell/panels/purchase_invoice_create_panel.html"
    panel_action = reverse("shell:purchase_invoice_create_panel", args=[pk])

    def build_context():
        return _purchasing_ctx(
            request,
            supplier=supplier,
            errors=errors,
            range10=range(10),
            range20=range(20),
            payment_method_rows=_payment_rows(),
            purchase_form_state=_purchase_form_state(request) if request.method == "POST" else {},
            purchase_form_action=panel_action,
            form_action=panel_action,
            purchase_form_compact=True,
            panel_shell=True,
            panel_title="فاتورة شراء",
        )

    def on_valid():
        nonlocal errors
        errors = []
        lines = _purchase_lines_from_request(request, errors)
        if not errors:
            lines = _apply_general_discount(request, lines, errors)
        if not errors:
            total = sum((q * c for _, q, c in lines), Decimal("0")).quantize(Decimal("0.01"))
            payments = _purchase_payments_from_request(request, total, errors)
            if not errors:
                try:
                    post_purchase_invoice(
                        supplier=supplier,
                        lines=lines,
                        user=request.user,
                        payments=payments,
                    )
                except Exception as e:
                    errors.append(f"حدث خطأ: {e}")
        if errors:
            raise PanelFormInvalid(errors[0])

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid, wide=True)
def supplier_create_panel(request):
    from apps.contacts.models import Customer

    tpl = "shell/panels/supplier_create_panel.html"

    def build_context():
        form = SupplierForm(request.POST or None)
        panelize_form(form)
        return {
            "form": form,
            "form_action": reverse("shell:supplier_create_panel"),
            "panel_title": "إضافة مورد",
        }

    @transaction.atomic
    def on_valid():
        form = SupplierForm(request.POST)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
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

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)
def supplier_edit_panel(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    tpl = "shell/panels/supplier_edit_panel.html"

    def build_context():
        if request.method == "POST":
            form = SupplierForm(request.POST, instance=supplier)
        else:
            form = SupplierForm(
                instance=supplier,
                initial={"opening_balance": _supplier_opening_balance_from_ledger(supplier)},
            )
        panelize_form(form)
        return {
            "form": form,
            "supplier": supplier,
            "form_action": reverse("shell:supplier_edit_panel", args=[pk]),
            "panel_title": "تعديل مورد",
        }

    @transaction.atomic
    def on_valid():
        form = SupplierForm(request.POST, instance=supplier)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()
        new_ob = (form.cleaned_data.get("opening_balance") or Decimal("0")).quantize(Decimal("0.01"))
        if new_ob < 0:
            new_ob = Decimal("0")
        _apply_supplier_opening_balance(supplier=supplier, amount=new_ob)

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)
def purchase_invoice_detail_panel(request, pk):
    """HTML جزئي لعرض فاتورة الشراء داخل النافذة المنبثقة."""
    invoice = get_object_or_404(_purchase_invoice_detail_queryset(), pk=pk)
    ctx = _purchase_invoice_detail_context(request, invoice)
    return render(request, "shell/_purchase_invoice_detail_modal_fragment.html", ctx)
