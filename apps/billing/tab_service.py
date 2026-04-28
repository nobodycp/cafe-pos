"""تاب الطاولة، ضريبة وخدمة، تسوية فاتورة."""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.db import transaction
from django.db.models import Sum

from apps.billing.models import InvoicePayment, OrderPayment, SaleInvoice, SaleInvoiceLine
from apps.contacts.models import Customer, CustomerLedgerEntry
from apps.core.models import get_pos_settings, log_audit
from apps.core.sequences import next_int
from apps.core.services import SessionService
from apps.inventory.services import check_stock_available, consume_for_sale, get_unit_cost
from apps.pos.models import Order, OrderLine, TableSession


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def next_invoice_number() -> str:
    return f"INV-{next_int('sale_invoice'):06d}"


def _record_commission_vendor_payables(invoice: SaleInvoice) -> None:
    """For each commission-product line, record the vendor's payable in their ledger."""
    from apps.purchasing.models import Supplier, SupplierLedgerEntry

    vendor_totals: Dict[int, Decimal] = {}
    for sil in invoice.lines.select_related("product"):
        p = sil.product
        if p.product_type != p.ProductType.COMMISSION or not p.commission_vendor_id:
            continue
        pct = _d(p.commission_percentage or 0)
        vendor_payable = (sil.line_subtotal - sil.line_subtotal * pct / Decimal("100")).quantize(Decimal("0.01"))
        vendor_totals[p.commission_vendor_id] = vendor_totals.get(p.commission_vendor_id, Decimal("0")) + vendor_payable

    for vendor_id, total in vendor_totals.items():
        if total <= 0:
            continue
        supplier = Supplier.objects.select_for_update().get(pk=vendor_id)
        supplier.balance = (_d(supplier.balance) + total).quantize(Decimal("0.01"))
        supplier.save(update_fields=["balance", "updated_at"])
        SupplierLedgerEntry.objects.create(
            supplier=supplier,
            entry_type=SupplierLedgerEntry.EntryType.PURCHASE,
            amount=total,
            note=f"مستحقات بائع نسبة — فاتورة {invoice.invoice_number}",
            reference_model="billing.SaleInvoice",
            reference_pk=str(invoice.pk),
        )


def _deduct_linked_supplier(customer: Customer, credit_amount: Decimal, invoice) -> None:
    """If customer is linked to a supplier, deduct the credit sale from supplier balance."""
    linked = getattr(customer, "linked_supplier", None)
    if linked is None:
        return
    from apps.purchasing.models import SupplierLedgerEntry

    linked.balance = (linked.balance - credit_amount).quantize(Decimal("0.01"))
    linked.save(update_fields=["balance", "updated_at"])
    SupplierLedgerEntry.objects.create(
        supplier=linked,
        entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
        amount=-credit_amount,
        note=f"مشتريات العميل — فاتورة {invoice.invoice_number}",
        reference_model="billing.SaleInvoice",
        reference_pk=str(invoice.pk),
    )


def _line_gross(line: OrderLine) -> Decimal:
    return (_d(line.quantity) * (_d(line.unit_price) + _d(line.extra_unit_price))).quantize(Decimal("0.01"))


def _discount_amount(order: Order, gross: Decimal) -> Decimal:
    pct = _d(order.discount_percent)
    amt = _d(order.discount_amount)
    from_pct = (gross * pct / Decimal("100")).quantize(Decimal("0.01")) if pct else Decimal("0")
    disc = (amt + from_pct).quantize(Decimal("0.01"))
    if disc > gross:
        disc = gross
    return disc


def compute_order_totals(order: Order) -> Dict[str, Decimal]:
    gross = sum((_line_gross(ln) for ln in order.lines.all()), Decimal("0")).quantize(Decimal("0.01"))
    disc = _discount_amount(order, gross)
    net = (gross - disc).quantize(Decimal("0.01"))
    if net < 0:
        net = Decimal("0")
    pos = get_pos_settings()
    svc_pct = _d(order.service_charge_percent_override) if order.service_charge_percent_override is not None else _d(pos.default_service_charge_percent)
    tax_pct = _d(order.tax_percent_override) if order.tax_percent_override is not None else _d(pos.default_tax_percent)
    svc = (net * svc_pct / Decimal("100")).quantize(Decimal("0.01"))
    tax = ((net + svc) * tax_pct / Decimal("100")).quantize(Decimal("0.01"))
    grand = (net + svc + tax).quantize(Decimal("0.01"))
    return {"gross": gross, "discount": disc, "net": net, "service": svc, "tax": tax, "grand": grand}


def sum_tab_payments(order: Order) -> Decimal:
    s = order.tab_payments.filter(sale_invoice__isnull=True).aggregate(s=Sum("amount"))["s"]
    return _d(s)


def payments_list_to_dict(payments: List[Tuple[str, Decimal]]) -> Dict[str, Decimal]:
    d = {
        "cash": Decimal("0"),
        "bank": Decimal("0"),
        "bank_ps": Decimal("0"),
        "palpay": Decimal("0"),
        "jawwalpay": Decimal("0"),
        "credit": Decimal("0"),
    }
    for method, amt in payments:
        m = str(method)
        if m in d:
            d[m] += _d(amt)
    return d


def _aggregate_tab_payments(order: Order) -> Dict[str, Decimal]:
    d = {
        "cash": Decimal("0"),
        "bank": Decimal("0"),
        "bank_ps": Decimal("0"),
        "palpay": Decimal("0"),
        "jawwalpay": Decimal("0"),
        "credit": Decimal("0"),
    }
    for p in order.tab_payments.filter(sale_invoice__isnull=True):
        if p.method in d:
            d[p.method] += _d(p.amount)
    return d


def record_tab_payments(*, order: Order, user, payments: List[Tuple[str, Decimal]]) -> None:
    for method, amt in payments:
        a = _d(amt)
        if a <= 0:
            continue
        OrderPayment.objects.create(order=order, method=method, amount=a)
    log_audit(user, "pos.tab.payment", "pos.Order", order.pk, {"payments": str(payments)})


@transaction.atomic
def create_sale_invoice_core(
    *,
    order: Order,
    user,
    pay_by_method: Dict[str, Decimal],
    customer: Optional[Customer] = None,
) -> SaleInvoice:
    """يُنشئ فاتورة + دفعات فاتورة + مخزون + قيد ائتمان. لا يغيّر حالة الطلب."""
    if SaleInvoice.objects.filter(order=order).exists():
        raise ValueError("INVOICE_ALREADY_EXISTS")
    session = SessionService.require_open_session()
    if order.work_session_id != session.id:
        raise ValueError("ORDER_SESSION_MISMATCH")
    if order.status != Order.Status.OPEN:
        raise ValueError("ORDER_NOT_OPEN")
    if not order.lines.exists():
        raise ValueError("ORDER_EMPTY")

    for line in order.lines.select_related("product"):
        check_stock_available(line.product, _d(line.quantity))

    totals = compute_order_totals(order)
    gross = totals["gross"]
    discount_total = totals["discount"]
    grand = totals["grand"]
    svc = totals["service"]
    tax = totals["tax"]

    line_grosses = [(ln, _line_gross(ln)) for ln in order.lines.select_related("product")]
    gross_sum = sum((x[1] for x in line_grosses), Decimal("0")) or Decimal("1")

    inv = SaleInvoice.objects.create(
        invoice_number=next_invoice_number(),
        work_session=session,
        order=order,
        customer=customer or order.customer,
        supplier_buyer=None,
        subtotal=gross,
        discount_total=discount_total,
        total=grand,
        service_charge_total=svc,
        tax_total=tax,
        total_cost=Decimal("0"),
        total_profit=Decimal("0"),
        payment_status=SaleInvoice.PaymentStatus.PAID,
    )

    total_cost = Decimal("0")
    total_profit = Decimal("0")
    for line, lg in line_grosses:
        share = (lg / gross_sum) if gross_sum else Decimal("0")
        line_discount = (discount_total * share).quantize(Decimal("0.01"))
        adjusted_line_sub = (lg - line_discount).quantize(Decimal("0.01"))
        if adjusted_line_sub < 0:
            adjusted_line_sub = Decimal("0")
        qty = _d(line.quantity)
        uc = get_unit_cost(line.product)
        line_cost = (qty * uc).quantize(Decimal("0.01"))
        p = line.product
        if p.product_type == p.ProductType.COMMISSION:
            pct = _d(p.commission_percentage or 0)
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
            unit_price=_d(line.unit_price) + _d(line.extra_unit_price),
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

    inv.save(update_fields=["total_cost", "total_profit", "supplier_buyer", "updated_at"])

    pay_sum = sum(pay_by_method.values(), Decimal("0")).quantize(Decimal("0.01"))
    if pay_sum != grand:
        raise ValueError("PAYMENT_SUM_MISMATCH")

    credit_total = Decimal("0")
    for method, amount in pay_by_method.items():
        if amount <= 0:
            continue
        InvoicePayment.objects.create(invoice=inv, method=method, amount=amount)
        if method == InvoicePayment.Method.CREDIT:
            credit_total += amount

    cust = customer or order.customer
    if credit_total > 0:
        if not cust:
            raise ValueError("CREDIT_REQUIRES_CUSTOMER")
        cust.balance = (cust.balance + credit_total).quantize(Decimal("0.01"))
        cust.save(update_fields=["balance", "updated_at"])
        CustomerLedgerEntry.objects.create(
            customer=cust,
            entry_type=CustomerLedgerEntry.EntryType.INVOICE,
            amount=credit_total,
            note=f"فاتورة {inv.invoice_number}",
            reference_model="billing.SaleInvoice",
            reference_pk=str(inv.pk),
        )
        _deduct_linked_supplier(cust, credit_total, inv)

    for line in order.lines.select_related("product"):
        consume_for_sale(product=line.product, quantity=_d(line.quantity), session=session, invoice_pk=inv.pk)

    from apps.accounting.services import post_sale_invoice_journal

    post_sale_invoice_journal(invoice=inv, pay_by_method=pay_by_method, user=user)

    _record_commission_vendor_payables(inv)

    log_audit(user, "sale.invoice.created", "billing.SaleInvoice", inv.pk, {"total": str(grand)})
    return inv


def _close_table_session_if_no_open_orders(*, table_session_id: Optional[int]) -> None:
    """After an order leaves OPEN, close the table session if no OPEN orders remain."""
    if not table_session_id:
        return
    if Order.objects.filter(table_session_id=table_session_id, status=Order.Status.OPEN).exists():
        return
    ts = TableSession.objects.filter(pk=table_session_id, status=TableSession.Status.OPEN).first()
    if not ts:
        return
    from django.utils import timezone

    ts.status = TableSession.Status.CLOSED
    ts.closed_at = timezone.now()
    ts.save(update_fields=["status", "closed_at", "updated_at"])


@transaction.atomic
def finalize_order_invoice(
    *, order: Order, user, customer: Optional[Customer] = None
) -> Optional[SaleInvoice]:
    totals = compute_order_totals(order)
    paid = sum_tab_payments(order)
    if paid + Decimal("0.005") < totals["grand"]:
        raise ValueError("TAB_NOT_FULLY_PAID")

    # طلب بمجموع صفر وبدون بنود — لا فاتورة؛ إغلاق الطلب والجلسة (كان create_sale_invoice_core يرفض ORDER_EMPTY)
    if not order.lines.exists() and totals["grand"] <= Decimal("0.005"):
        if paid > Decimal("0.005"):
            raise ValueError("TAB_PAYMENT_ON_EMPTY_ORDER")
        order.status = Order.Status.CHECKED_OUT
        order.save(update_fields=["status", "updated_at"])
        sid = order.table_session_id
        _close_table_session_if_no_open_orders(table_session_id=sid)
        log_audit(
            user,
            "sale.tab.empty_close",
            "pos.Order",
            order.pk,
            {"table_session_id": sid, "note": "no_invoice"},
        )
        return None

    pay_by_method = _aggregate_tab_payments(order)
    inv = create_sale_invoice_core(order=order, user=user, pay_by_method=pay_by_method, customer=customer)
    order.tab_payments.filter(sale_invoice__isnull=True).update(sale_invoice=inv)
    order.status = Order.Status.CHECKED_OUT
    order.save(update_fields=["status", "updated_at"])
    _close_table_session_if_no_open_orders(table_session_id=order.table_session_id)
    log_audit(user, "sale.tab.finalize", "billing.SaleInvoice", inv.pk, {"total": str(totals["grand"])})
    return inv


@transaction.atomic
def apply_tab_payments_and_maybe_finalize(
    *, order: Order, user, payments: List[Tuple[str, Decimal]], customer: Optional[Customer] = None
) -> Optional[SaleInvoice]:
    record_tab_payments(order=order, user=user, payments=payments)
    totals = compute_order_totals(order)
    paid = sum_tab_payments(order)
    if paid + Decimal("0.005") >= totals["grand"]:
        return finalize_order_invoice(order=order, user=user, customer=customer)
    return None
