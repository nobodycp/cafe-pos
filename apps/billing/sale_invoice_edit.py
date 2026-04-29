"""تعديل بنود فاتورة بيع صادرة (مع مخزون) — يُفعّل من إعدادات النظام فقط."""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Tuple

from django.db import transaction
from apps.billing.models import InvoicePayment, SaleInvoice
from apps.catalog.models import Product
from apps.core.decimalutil import as_decimal
from apps.core.models import get_pos_settings, log_audit
from apps.core.payment_methods import credit_method_codes
from apps.inventory.services import (
    check_stock_available,
    consume_for_sale,
    get_unit_cost,
    return_sale_consumption,
)


def _line_gross(qty: Decimal, unit_price: Decimal) -> Decimal:
    return (as_decimal(qty) * as_decimal(unit_price)).quantize(Decimal("0.01"))


def can_edit_sale_invoice(invoice: SaleInvoice) -> Tuple[bool, str]:
    if not get_pos_settings().allow_sale_invoice_edit:
        return False, "تعديل فواتير البيع غير مفعّل في الإعدادات (تبويب الإيصال)."
    if invoice.is_cancelled:
        return False, "لا يمكن تعديل فاتورة ملغاة."
    if invoice.returns.exists():
        return False, "لا يمكن تعديل فاتورة عليها مرتجع."
    return True, ""


@transaction.atomic
def apply_sale_invoice_line_edits(
    *,
    invoice: SaleInvoice,
    user,
    rows: List[Tuple[int, Decimal, Decimal]],
) -> None:
    """
    rows: قائمة (line_pk, quantity, unit_price)
    يحدّث المخزون وفق فرق الكمية، ويعيد توزيع خصم الفاتورة على الأسطر، ويحدّث الإجمالي.
    دفعات متعددة: يجب أن يطابق مجموع الدفعات الإجمالي الجديد (بدون تغيير تلقائي).
    دفعة واحدة: يُحدَّث مبلغها ليطابق الإجمالي الجديد.
    """
    ok, msg = can_edit_sale_invoice(invoice)
    if not ok:
        raise ValueError(msg)

    inv = (
        SaleInvoice.objects.select_for_update()
        .select_related("work_session", "order")
        .get(pk=invoice.pk)
    )
    line_map = {ln.pk: ln for ln in inv.lines.select_related("product").all()}
    discount_total = as_decimal(inv.discount_total)
    svc = as_decimal(inv.service_charge_total)
    tax = as_decimal(inv.tax_total)

    parsed: Dict[int, Tuple[Decimal, Decimal]] = {}
    for line_pk, qty, price in rows:
        ln = line_map.get(int(line_pk))
        if not ln:
            raise ValueError("INVALID_LINE")
        q = as_decimal(qty)
        p = as_decimal(price)
        if q < 0 or p < 0:
            raise ValueError("NEGATIVE_NOT_ALLOWED")
        parsed[ln.pk] = (q, p)

    if len(parsed) != len(line_map):
        raise ValueError("ALL_LINES_REQUIRED")

    session = inv.work_session
    if not session:
        raise ValueError("NO_SESSION")

    pays = list(InvoicePayment.objects.select_for_update().filter(invoice=inv))
    if not pays:
        raise ValueError("NO_PAYMENTS_ON_INVOICE")
    if any(p.method in credit_method_codes() for p in pays):
        raise ValueError("CREDIT_PAYMENTS_NO_EDIT")

    # فرق كمية → مخزون
    for ln in line_map.values():
        old_q = as_decimal(ln.quantity)
        new_q = parsed[ln.pk][0]
        delta = new_q - old_q
        if delta == 0:
            continue
        prod = ln.product
        if delta > 0:
            check_stock_available(prod, delta)
            consume_for_sale(product=prod, quantity=delta, session=session, invoice_pk=inv.pk)
        else:
            return_sale_consumption(product=prod, quantity=-delta, session=session, invoice_pk=inv.pk)

    gross_by_line: Dict[int, Decimal] = {}
    for ln in line_map.values():
        q, p = parsed[ln.pk]
        gross_by_line[ln.pk] = _line_gross(q, p)

    gross_sum = sum(gross_by_line.values(), Decimal("0")).quantize(Decimal("0.01"))
    if gross_sum <= 0:
        raise ValueError("INVALID_TOTALS")

    total_cost = Decimal("0")
    total_profit = Decimal("0")
    for ln in line_map.values():
        q, p = parsed[ln.pk]
        lg = gross_by_line[ln.pk]
        share = (lg / gross_sum) if gross_sum else Decimal("0")
        line_discount = (discount_total * share).quantize(Decimal("0.01"))
        adjusted_line_sub = (lg - line_discount).quantize(Decimal("0.01"))
        if adjusted_line_sub < 0:
            adjusted_line_sub = Decimal("0")

        prod = ln.product
        uc = get_unit_cost(prod)
        line_cost = (as_decimal(q) * uc).quantize(Decimal("0.01"))

        if prod.product_type == Product.ProductType.COMMISSION:
            pct = as_decimal(prod.commission_percentage or 0)
            recognized = (adjusted_line_sub * pct / Decimal("100")).quantize(Decimal("0.01"))
            line_cost = Decimal("0")
            line_profit = recognized
        else:
            recognized = adjusted_line_sub
            line_profit = (recognized - line_cost).quantize(Decimal("0.01"))

        ln.quantity = q
        ln.unit_price = p
        ln.line_subtotal = adjusted_line_sub
        ln.unit_cost_snapshot = uc
        ln.line_cost_total = line_cost
        ln.recognized_revenue = recognized
        ln.line_profit = line_profit
        ln.save(
            update_fields=[
                "quantity",
                "unit_price",
                "line_subtotal",
                "unit_cost_snapshot",
                "line_cost_total",
                "recognized_revenue",
                "line_profit",
                "updated_at",
            ]
        )
        total_cost += line_cost
        total_profit += line_profit

    new_subtotal = gross_sum
    new_total = (new_subtotal - discount_total + svc + tax).quantize(Decimal("0.01"))

    pay_sum = sum((as_decimal(x.amount) for x in pays), Decimal("0")).quantize(Decimal("0.01"))
    diff = (new_total - pay_sum).quantize(Decimal("0.01"))

    if abs(diff) > Decimal("0.02"):
        if len(pays) == 1:
            p0 = pays[0]
            p0.amount = new_total
            p0.save(update_fields=["amount", "updated_at"])
        else:
            raise ValueError(
                f"PAYMENT_MISMATCH:{pay_sum}:{new_total}"
            )

    inv.subtotal = new_subtotal
    inv.total = new_total
    inv.total_cost = total_cost.quantize(Decimal("0.01"))
    inv.total_profit = total_profit.quantize(Decimal("0.01"))
    inv.save(update_fields=["subtotal", "total", "total_cost", "total_profit", "updated_at"])

    log_audit(
        user,
        "sale.invoice.lines_edited",
        "billing.SaleInvoice",
        str(inv.pk),
        {"invoice_number": inv.invoice_number, "new_total": str(new_total)},
    )
