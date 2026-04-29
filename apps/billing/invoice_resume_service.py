"""إعادة فتح طلب مرتبط بفاتورة للتعديل من الكاشير، وتحديث الفاتورة نفسها عند إعادة التسوية."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db import transaction

from apps.billing.models import InvoicePayment, OrderPayment, SaleInvoice, SaleInvoiceLine
from apps.billing.sale_invoice_edit import can_edit_sale_invoice
from apps.catalog.models import Product
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.decimalutil import as_decimal
from apps.core.models import get_pos_settings, log_audit
from apps.core.payment_methods import credit_method_codes
from apps.core.services import SessionService
from apps.inventory.services import check_stock_available, consume_for_sale, get_unit_cost, return_sale_consumption
from apps.pos.models import Order, TableSession
from apps.pos.services import hold_order


def _line_gross_order_line(line) -> Decimal:
    return (as_decimal(line.quantity) * (as_decimal(line.unit_price) + as_decimal(line.extra_unit_price))).quantize(
        Decimal("0.01")
    )


def _reverse_commission_vendor_payables(*, invoice: SaleInvoice) -> None:
    from apps.purchasing.models import Supplier, SupplierLedgerEntry

    vendor_totals: Dict[int, Decimal] = {}
    for sil in invoice.lines.select_related("product"):
        p = sil.product
        if p.product_type != p.ProductType.COMMISSION or not p.commission_vendor_id:
            continue
        pct = as_decimal(p.commission_percentage or 0)
        vendor_payable = (sil.line_subtotal - sil.line_subtotal * pct / Decimal("100")).quantize(Decimal("0.01"))
        vendor_totals[p.commission_vendor_id] = vendor_totals.get(p.commission_vendor_id, Decimal("0")) + vendor_payable

    for vendor_id, total in vendor_totals.items():
        if total <= 0:
            continue
        supplier = Supplier.objects.select_for_update().get(pk=vendor_id)
        supplier.balance = (as_decimal(supplier.balance) - total).quantize(Decimal("0.01"))
        if supplier.balance < 0 and supplier.balance > Decimal("-0.01"):
            supplier.balance = Decimal("0")
        supplier.save(update_fields=["balance", "updated_at"])
        SupplierLedgerEntry.objects.create(
            supplier=supplier,
            entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
            amount=-total,
            note=f"عكس مستحقات بائع نسبة — تعديل فاتورة {invoice.invoice_number}",
            reference_model="billing.SaleInvoice",
            reference_pk=str(invoice.pk),
        )


def _reverse_customer_credit_for_invoice(*, invoice: SaleInvoice) -> None:
    credit_total = sum(
        as_decimal(p.amount) for p in invoice.payments.filter(method__in=credit_method_codes())
    )
    if credit_total <= 0 or not invoice.customer:
        return
    cust = invoice.customer
    cust.balance = (as_decimal(cust.balance) - credit_total).quantize(Decimal("0.01"))
    if cust.balance < 0 and cust.balance > Decimal("-0.01"):
        cust.balance = Decimal("0")
    cust.save(update_fields=["balance", "updated_at"])
    CustomerLedgerEntry.objects.create(
        customer=cust,
        entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
        amount=-credit_total,
        note=f"عكس آجل — تعديل فاتورة {invoice.invoice_number}",
        reference_model="billing.SaleInvoice",
        reference_pk=str(invoice.pk),
    )
    linked = getattr(cust, "linked_supplier", None)
    if linked:
        from apps.purchasing.models import SupplierLedgerEntry

        linked.balance = (as_decimal(linked.balance) + credit_total).quantize(Decimal("0.01"))
        linked.save(update_fields=["balance", "updated_at"])
        SupplierLedgerEntry.objects.create(
            supplier=linked,
            entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
            amount=credit_total,
            note=f"عكس مشتريات العميل — تعديل فاتورة {invoice.invoice_number}",
            reference_model="billing.SaleInvoice",
            reference_pk=str(invoice.pk),
        )


def _reverse_sale_invoice_journals(*, invoice: SaleInvoice, user, reason: str) -> None:
    from apps.accounting.models import JournalEntry
    from apps.accounting.services import reverse_journal_entry

    while True:
        je = (
            JournalEntry.objects.filter(
                reference_type="billing.SaleInvoice",
                reference_pk=str(invoice.pk),
                is_reversed=False,
            )
            .exclude(description__startswith="عكس قيد")
            .first()
        )
        if not je:
            break
        reverse_journal_entry(original=je, reason=reason, user=user)


@transaction.atomic
def abort_resume_invoice_order(*, order: Order, invoice: SaleInvoice, user) -> None:
    """إلغاء وضع التعديل: حذف دفعات التاب المؤقتة وإعادة ربط الدفعات من الفاتورة وإغلاق الطلب."""
    if order.status != Order.Status.OPEN:
        raise ValueError("ORDER_NOT_OPEN")
    if invoice.order_id != order.id:
        raise ValueError("ORDER_INVOICE_MISMATCH")

    OrderPayment.objects.filter(order=order, sale_invoice__isnull=True).delete()
    for ip in invoice.payments.order_by("pk"):
        OrderPayment.objects.create(
            order=order,
            method=ip.method,
            amount=as_decimal(ip.amount),
            payer_name=(ip.payer_name or "")[:120],
            payer_phone=(ip.payer_phone or "")[:40],
            payment_source=(ip.payment_source or "")[:24],
            sale_invoice=invoice,
        )
    order.status = Order.Status.CHECKED_OUT
    order.is_held = False
    order.save(update_fields=["status", "is_held", "updated_at"])
    from apps.billing.tab_service import _close_table_session_if_no_open_orders

    _close_table_session_if_no_open_orders(table_session_id=order.table_session_id)
    log_audit(user, "sale.invoice.resume_aborted", "billing.SaleInvoice", invoice.pk, {"order": order.pk})


@transaction.atomic
def resume_last_sale_invoice_into_cart(*, user) -> Order:
    """يعيد فتح طلب آخر فاتورة بيع غير ملغاة في هذه الوردية للتعديل في الكاشير."""
    if not get_pos_settings().allow_sale_invoice_edit:
        raise ValueError("تعديل فواتير البيع غير مفعّل في الإعدادات (تبويب الإيصال).")

    session = SessionService.require_open_session()

    inv = (
        SaleInvoice.objects.filter(is_cancelled=False, work_session=session)
        .select_related("order", "order__table_session")
        .order_by("-created_at", "-pk")
        .first()
    )
    if inv is None:
        raise ValueError("لا توجد فاتورة بيع في هذه الوردية.")

    ok, msg = can_edit_sale_invoice(inv)
    if not ok:
        raise ValueError(msg or "لا يمكن استئناف هذه الفاتورة.")

    order = Order.objects.select_for_update().select_related("table_session").get(pk=inv.order_id)
    if order.work_session_id != session.id:
        raise ValueError("ORDER_SESSION_MISMATCH")

    if order.status == Order.Status.OPEN:
        if not OrderPayment.objects.filter(sale_invoice=inv).exists():
            return order

    if order.status != Order.Status.CHECKED_OUT:
        raise ValueError("حالة الطلب غير متوقعة — لا يمكن الاستئناف.")

    if order.table_session_id:
        ts = TableSession.objects.select_for_update().get(pk=order.table_session_id)
        if ts.status == TableSession.Status.CLOSED:
            ts.status = TableSession.Status.OPEN
            ts.closed_at = None
            ts.save(update_fields=["status", "closed_at", "updated_at"])

    OrderPayment.objects.filter(sale_invoice=inv).update(sale_invoice=None)
    order.status = Order.Status.OPEN
    order.is_held = False
    order.save(update_fields=["status", "is_held", "updated_at"])

    log_audit(user, "sale.invoice.resume_cart", "billing.SaleInvoice", inv.pk, {"order": order.pk})
    return order


def hold_current_pos_order_if_needed(*, user, session, current_order_id: Optional[int], target_order_id: int) -> None:
    if not current_order_id or int(current_order_id) == int(target_order_id):
        return
    cur = (
        Order.objects.filter(
            pk=current_order_id,
            work_session=session,
            status=Order.Status.OPEN,
            is_held=False,
        ).first()
    )
    if not cur:
        return
    has_stuff = cur.lines.exists() or cur.tab_payments.filter(sale_invoice__isnull=True).exists()
    if not has_stuff:
        return
    hold_order(order=cur, user=user)


@transaction.atomic
def update_sale_invoice_from_order(
    *,
    order: Order,
    user,
    customer: Optional[Customer],
    pay_by_method: Dict[str, Decimal],
    payment_rows: List[Dict[str, Any]],
) -> SaleInvoice:
    """يحدّث فاتورة موجودة من الطلب المفتوح (بعد استئناف التعديل)."""
    from apps.billing import tab_service as tabs

    session = SessionService.require_open_session()
    if order.work_session_id != session.id:
        raise ValueError("ORDER_SESSION_MISMATCH")
    if order.status != Order.Status.OPEN:
        raise ValueError("ORDER_NOT_OPEN")

    inv = SaleInvoice.objects.select_for_update().filter(order=order).first()
    if not inv:
        raise ValueError("NO_INVOICE_FOR_ORDER")

    ok, msg = can_edit_sale_invoice(inv)
    if not ok:
        raise ValueError(msg or "لا يمكن تعديل هذه الفاتورة.")

    totals = tabs.compute_order_totals(order)
    if not order.lines.exists() and totals["grand"] > Decimal("0.005"):
        raise ValueError("ORDER_EMPTY")

    gross = totals["gross"]
    discount_total = totals["discount"]
    grand = totals["grand"]
    svc = totals["service"]
    tax = totals["tax"]

    for line in order.lines.select_related("product"):
        check_stock_available(line.product, as_decimal(line.quantity))

    _reverse_commission_vendor_payables(invoice=inv)
    _reverse_customer_credit_for_invoice(invoice=inv)
    _reverse_sale_invoice_journals(invoice=inv, user=user, reason=f"تعديل فاتورة {inv.invoice_number}")

    for sil in inv.lines.select_related("product"):
        return_sale_consumption(
            product=sil.product,
            quantity=as_decimal(sil.quantity),
            session=inv.work_session,
            invoice_pk=inv.pk,
        )

    inv.payments.all().delete()
    inv.lines.all().delete()

    line_grosses = [(ln, _line_gross_order_line(ln)) for ln in order.lines.select_related("product")]
    gross_sum = sum((x[1] for x in line_grosses), Decimal("0")) or Decimal("1")

    inv.customer = customer or order.customer
    inv.subtotal = gross
    inv.discount_total = discount_total
    inv.total = grand
    inv.service_charge_total = svc
    inv.tax_total = tax
    inv.total_cost = Decimal("0")
    inv.total_profit = Decimal("0")
    inv.save(
        update_fields=[
            "customer",
            "subtotal",
            "discount_total",
            "total",
            "service_charge_total",
            "tax_total",
            "total_cost",
            "total_profit",
            "updated_at",
        ]
    )

    total_cost = Decimal("0")
    total_profit = Decimal("0")
    for line, lg in line_grosses:
        share = (lg / gross_sum) if gross_sum else Decimal("0")
        line_discount = (discount_total * share).quantize(Decimal("0.01"))
        adjusted_line_sub = (lg - line_discount).quantize(Decimal("0.01"))
        if adjusted_line_sub < 0:
            adjusted_line_sub = Decimal("0")
        qty = as_decimal(line.quantity)
        uc = get_unit_cost(line.product)
        line_cost = (qty * uc).quantize(Decimal("0.01"))
        p = line.product
        if p.product_type == p.ProductType.COMMISSION:
            pct = as_decimal(p.commission_percentage or 0)
            recognized = (adjusted_line_sub * pct / Decimal("100")).quantize(Decimal("0.01"))
            line_cost = Decimal("0")
            line_profit = recognized
        else:
            recognized = adjusted_line_sub
            line_profit = (recognized - line_cost).quantize(Decimal("0.01"))
        SaleInvoiceLine.objects.create(
            invoice=inv,
            product=line.product,
            quantity=qty,
            unit_price=as_decimal(line.unit_price) + as_decimal(line.extra_unit_price),
            line_subtotal=adjusted_line_sub,
            unit_cost_snapshot=uc,
            line_cost_total=line_cost,
            recognized_revenue=recognized,
            line_profit=line_profit,
        )
        total_cost += line_cost
        total_profit += line_profit

    inv.total_cost = total_cost
    inv.total_profit = total_profit

    commission_vendors = set()
    for line in order.lines.select_related("product"):
        p = line.product
        if p.product_type == p.ProductType.COMMISSION and p.commission_vendor_id:
            commission_vendors.add(p.commission_vendor_id)
    if len(commission_vendors) == 1:
        inv.supplier_buyer_id = commission_vendors.pop()
    else:
        inv.supplier_buyer_id = None

    inv.save(update_fields=["total_cost", "total_profit", "supplier_buyer", "updated_at"])

    pay_sum = sum(pay_by_method.values(), Decimal("0")).quantize(Decimal("0.01"))
    if pay_sum != grand:
        raise ValueError("PAYMENT_SUM_MISMATCH")

    credit_total = Decimal("0")
    for pr in payment_rows:
        method = str(pr.get("method") or "")
        amount = as_decimal(pr.get("amount"))
        if amount <= 0:
            continue
        InvoicePayment.objects.create(
            invoice=inv,
            method=method,
            amount=amount,
            payer_name=str(pr.get("payer_name") or "")[:120],
            payer_phone=str(pr.get("payer_phone") or "")[:40],
            payment_source=str(pr.get("source") or "")[:24],
        )
        if method in credit_method_codes():
            credit_total += amount

    cust = customer or order.customer
    if credit_total > 0:
        if not cust:
            raise ValueError("CREDIT_REQUIRES_CUSTOMER")
        cust.balance = (as_decimal(cust.balance) + credit_total).quantize(Decimal("0.01"))
        cust.save(update_fields=["balance", "updated_at"])
        CustomerLedgerEntry.objects.create(
            customer=cust,
            entry_type=CustomerLedgerEntry.EntryType.INVOICE,
            amount=credit_total,
            note=f"فاتورة {inv.invoice_number}",
            reference_model="billing.SaleInvoice",
            reference_pk=str(inv.pk),
        )
        tabs._deduct_linked_supplier(cust, credit_total, inv)

    for line in order.lines.select_related("product"):
        consume_for_sale(product=line.product, quantity=as_decimal(line.quantity), session=session, invoice_pk=inv.pk)

    from apps.accounting.services import post_sale_invoice_journal

    post_sale_invoice_journal(invoice=inv, pay_by_method=pay_by_method, user=user)

    tabs._record_commission_vendor_payables(inv)

    log_audit(
        user,
        "sale.invoice.updated_from_order",
        "billing.SaleInvoice",
        inv.pk,
        {"invoice_number": inv.invoice_number, "total": str(grand)},
    )
    return inv
