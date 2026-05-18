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
    _redirect_open_purchase_invoice,
)


OPENING_BALANCE_LEDGER_NOTE = "رصيد افتتاحي"

def purchase_return_create(request, pk):
    from decimal import InvalidOperation
    from django.db import transaction
    from apps.core.sequences import next_int
    from apps.core.models import log_audit
    from apps.core.services import SessionService
    from apps.inventory.services import adjust_stock
    from apps.inventory.models import StockMovement

    invoice = get_object_or_404(PurchaseInvoice.objects.select_related("supplier"), pk=pk)
    inv_lines = invoice.lines.select_related("product").order_by("pk")
    errors = []

    if request.method == "POST":
        reason = request.POST.get("reason", "")
        return_lines = []

        for line in inv_lines:
            qty_str = request.POST.get(f"qty_{line.pk}", "").strip()
            if not qty_str:
                continue
            try:
                qty = Decimal(qty_str)
                if qty <= 0:
                    continue
                if qty > line.quantity:
                    errors.append(f"الكمية المرتجعة لـ {line.product.name_ar} أكبر من المشتراة")
                    continue
                return_lines.append((line, qty))
            except (InvalidOperation, ValueError):
                errors.append(f"كمية غير صالحة لـ {line.product.name_ar}")

        if not return_lines and not errors:
            errors.append("يرجى تحديد كمية مرتجعة واحدة على الأقل")

        if not errors:
            with transaction.atomic():
                total = Decimal("0")
                ret = PurchaseReturn.objects.create(
                    purchase_invoice=invoice,
                    return_number=f"PRET-{next_int('purchase_return'):06d}",
                    reason=reason,
                )

                session = SessionService.get_open_session()
                for inv_line, qty in return_lines:
                    line_total = (qty * inv_line.unit_cost).quantize(Decimal("0.01"))
                    PurchaseReturnLine.objects.create(
                        purchase_return=ret,
                        product=inv_line.product,
                        quantity=qty,
                        unit_cost=inv_line.unit_cost,
                        line_total=line_total,
                    )
                    total += line_total

                    if inv_line.product.is_stock_tracked:
                        adjust_stock(
                            product=inv_line.product,
                            quantity_delta=-qty,
                            movement_type=StockMovement.MovementType.ADJUSTMENT,
                            session=session,
                            reference_model="purchasing.PurchaseReturn",
                            reference_pk=str(ret.pk),
                            note=f"مرتجع مشتريات {ret.return_number}",
                        )

                ret.total = total
                ret.save(update_fields=["total", "updated_at"])

                supplier = invoice.supplier
                supplier.balance = (supplier.balance - total).quantize(Decimal("0.01"))
                supplier.save(update_fields=["balance", "updated_at"])
                SupplierLedgerEntry.objects.create(
                    supplier=supplier,
                    entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
                    amount=-total,
                    note=f"مرتجع مشتريات {ret.return_number}",
                    reference_model="purchasing.PurchaseReturn",
                    reference_pk=str(ret.pk),
                )

                log_audit(request.user, "purchase.return.created", "purchasing.PurchaseReturn", ret.pk, {"total": str(total)})
                messages.success(request, f"تم تسجيل مرتجع {ret.return_number} بمبلغ {total}")
                return _redirect_open_purchase_invoice(request, invoice.pk)

    return render(request, "purchasing/purchase_return_form.html", {
        "invoice": invoice,
        "inv_lines": inv_lines,
        "errors": errors,
    })
