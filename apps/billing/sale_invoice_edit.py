"""تعديل بنود فاتورة بيع صادرة (مع مخزون) — يُفعّل من إعدادات النظام فقط."""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from django.db import transaction
from apps.billing.models import InvoicePayment, SaleInvoice, SaleInvoiceLine
from apps.catalog.models import Product
from apps.contacts.models import Customer
from apps.core.decimalutil import as_decimal
from apps.core.models import get_pos_settings, log_audit
from apps.core.payment_methods import (
    credit_method_codes,
    get_payment_method_codes,
    load_payment_method_rows,
    method_codes_requiring_payer_details,
)
from apps.inventory.services import (
    check_stock_available,
    consume_for_sale,
    get_unit_cost,
    return_sale_consumption,
)


def _line_gross(qty: Decimal, unit_price: Decimal) -> Decimal:
    return (as_decimal(qty) * as_decimal(unit_price)).quantize(Decimal("0.01"))


PaymentEditRow = Tuple[str, Decimal, str, str]  # method, amount, payer_name, payer_phone


def _payments_from_sale_edit_post(post) -> List[PaymentEditRow]:
    """دفعة واحدة أو دفع مختلط — نفس حقول إتمام الدفع في السلة."""
    codes = set(get_payment_method_codes())
    payer_name = (post.get("payer_name") or "").strip()[:120]
    payer_phone = (post.get("payer_phone") or "").strip()[:40]
    use_splits = (post.get("use_payment_splits") or "").strip().lower() in ("1", "true", "on", "yes")
    raw_json = (post.get("payment_splits_json") or "").strip()

    if use_splits:
        if not raw_json:
            raise ValueError("INVALID_PAYMENT_SPLITS")
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            raise ValueError("INVALID_PAYMENT_SPLITS") from None
        if not isinstance(data, list) or len(data) > 24:
            raise ValueError("INVALID_PAYMENT_SPLITS")
        out: List[PaymentEditRow] = []
        for item in data:
            if isinstance(item, dict):
                method = str(item.get("method") or "").strip().lower()
                amt_raw = item.get("amount")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                method = str(item[0] or "").strip().lower()
                amt_raw = item[1]
            else:
                continue
            if method not in codes:
                continue
            try:
                a = as_decimal(amt_raw)
            except (InvalidOperation, ValueError, TypeError):
                raise ValueError("BAD_NUMBER") from None
            if a <= 0:
                continue
            out.append((method, a, payer_name, payer_phone))
        if not out:
            raise ValueError("NO_PAYMENTS_ON_INVOICE")
        return out

    mode = (post.get("payment_mode") or "").strip().lower()
    if mode:
        if mode not in codes:
            raise ValueError("INVALID_PAYMENT_METHOD")
        raw_amt = (post.get("pay_amount") or "").strip()
        try:
            amt = as_decimal(raw_amt) if raw_amt else Decimal("0")
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("BAD_NUMBER") from None
        if amt <= 0:
            raise ValueError("NO_PAYMENTS_ON_INVOICE")
        return [(mode, amt, payer_name, payer_phone)]

    return _parse_legacy_payment_rows(post)


def _parse_legacy_payment_rows(post) -> List[PaymentEditRow]:
    """توافق مع الحقول القديمة pay_m_{i} / pay_a_{i}."""
    pay_rows: List[PaymentEditRow] = []
    for i in range(16):
        m = (post.get(f"pay_m_{i}") or "").strip().lower()
        ra = (post.get(f"pay_a_{i}") or "").strip()
        if not m and not ra:
            continue
        if not m or not ra:
            raise ValueError("MISSING_FIELDS")
        try:
            a = Decimal(str(ra).replace(",", "."))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("BAD_NUMBER") from None
        if a < 0:
            raise ValueError("NEGATIVE_NOT_ALLOWED")
        if a > 0:
            pay_rows.append((m, a, "", ""))
    return pay_rows


def _resolve_edit_customer(post, inv: SaleInvoice) -> Optional[Customer]:
    from apps.contacts.services import resolve_or_create_active_customer_by_name

    cid = (post.get("customer_id") or "").strip()
    if cid.isdigit():
        c = Customer.objects.filter(pk=int(cid), is_active=True).first()
        if c:
            return c
    draft = (post.get("customer_name_draft") or "").strip()[:200]
    if len(draft) >= 2:
        c, _ = resolve_or_create_active_customer_by_name(draft)
        if c:
            return c
    return inv.customer


def _validate_payment_rows(payment_rows: List[PaymentEditRow], customer: Optional[Customer]) -> None:
    ar_codes = credit_method_codes()
    payer_req = method_codes_requiring_payer_details()
    credit_total = Decimal("0")
    for method, amt, pn, ph in payment_rows:
        if amt <= 0:
            continue
        if str(method).strip().lower() in ar_codes:
            credit_total += as_decimal(amt)
        if str(method).strip().lower() in payer_req:
            if len((pn or "").strip()) < 2 or len("".join(ch for ch in (ph or "") if ch.isdigit())) < 8:
                raise ValueError("PAYER_DETAILS_REQUIRED")
    if credit_total > 0 and not customer:
        raise ValueError("CREDIT_REQUIRES_CUSTOMER")


def parse_sale_invoice_full_edit_post(post) -> Tuple[List[Tuple[Product, Decimal, Decimal]], List[PaymentEditRow]]:
    """يحلّل حقول نموذج التعديل: أسطر + دفعات (نمط السلة)."""

    line_rows: List[Tuple[Product, Decimal, Decimal]] = []
    for i in range(50):
        pid = (post.get(f"line_{i}_product") or "").strip()
        if not pid:
            continue
        try:
            prod = Product.objects.get(pk=int(pid), is_active=True)
        except (ValueError, Product.DoesNotExist):
            raise ValueError("INVALID_LINE_PRODUCT") from None
        rq = (post.get(f"line_{i}_qty") or "").strip()
        rp = (post.get(f"line_{i}_price") or "").strip()
        if not rq or not rp:
            raise ValueError("MISSING_FIELDS")
        try:
            q = Decimal(str(rq).replace(",", "."))
            p = Decimal(str(rp).replace(",", "."))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("BAD_NUMBER") from None
        line_rows.append((prod, q, p))

    pay_rows = _payments_from_sale_edit_post(post)
    if not pay_rows:
        raise ValueError("NO_PAYMENTS_ON_INVOICE")

    return line_rows, pay_rows


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
            consume_for_sale(product=prod, quantity=delta, session=session, invoice_pk=inv.pk, sale_line=ln)
        else:
            return_sale_consumption(product=prod, quantity=-delta, session=session, invoice_pk=inv.pk, sale_line=ln)

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


@transaction.atomic
def apply_sale_invoice_full_edit(
    *,
    invoice: SaleInvoice,
    user,
    line_rows: List[Tuple[Product, Decimal, Decimal]],
    payment_rows: List[PaymentEditRow],
    post=None,
) -> None:
    """
    إعادة بناء فاتورة البيع بالكامل: أسطر جديدة (منتج + كمية + سعر) ودفعات جديدة.
    يعكس القيود والذمم ومستحقات العمولة ثم المخزون القديم، ثم يعيد التسجيل كفاتورة جديدة بنفس الرقم والطلب.
    """
    from apps.accounting.services import post_sale_invoice_journal
    from apps.billing import tab_service as tab_svc
    from apps.billing.invoice_resume_service import (
        _reverse_commission_vendor_payables,
        _reverse_customer_credit_for_invoice,
        _reverse_sale_invoice_journals,
    )

    ok, msg = can_edit_sale_invoice(invoice)
    if not ok:
        raise ValueError(msg)

    if not line_rows:
        raise ValueError("NO_LINES")

    if not payment_rows:
        raise ValueError("NO_PAYMENTS_ON_INVOICE")

    valid_codes = {str(r["code"] or "").strip().lower() for r in load_payment_method_rows()}
    for method, _, _, _ in payment_rows:
        if str(method or "").strip().lower() not in valid_codes:
            raise ValueError("INVALID_PAYMENT_METHOD")

    inv = (
        SaleInvoice.objects.select_for_update()
        .select_related("work_session", "order", "customer")
        .get(pk=invoice.pk)
    )
    session = inv.work_session
    if not session:
        raise ValueError("NO_SESSION")

    discount_total = as_decimal(inv.discount_total)
    svc = as_decimal(inv.service_charge_total)
    tax = as_decimal(inv.tax_total)

    gross_by_idx: List[Tuple[Product, Decimal, Decimal, Decimal]] = []
    for prod, qty, price in line_rows:
        q = as_decimal(qty)
        p = as_decimal(price)
        if q < 0 or p < 0:
            raise ValueError("NEGATIVE_NOT_ALLOWED")
        if q == 0:
            raise ValueError("ZERO_QTY_NOT_ALLOWED")
        gross_by_idx.append((prod, q, p, _line_gross(q, p)))

    gross_sum = sum((x[3] for x in gross_by_idx), Decimal("0")).quantize(Decimal("0.01"))
    if gross_sum <= 0:
        raise ValueError("INVALID_TOTALS")

    new_total = (gross_sum - discount_total + svc + tax).quantize(Decimal("0.01"))
    pay_sum = sum((as_decimal(a) for _, a, _, _ in payment_rows), Decimal("0")).quantize(Decimal("0.01"))
    if abs(pay_sum - new_total) > Decimal("0.02"):
        raise ValueError(f"PAYMENT_SUM_MISMATCH:{pay_sum}:{new_total}")

    cust = _resolve_edit_customer(post, inv) if post is not None else inv.customer
    if cust and (not inv.customer_id or inv.customer_id != cust.pk):
        inv.customer = cust
        inv.save(update_fields=["customer", "updated_at"])

    _validate_payment_rows(payment_rows, inv.customer)

    credit_total = sum(
        (as_decimal(a) for m, a, _, _ in payment_rows if str(m).strip().lower() in credit_method_codes()),
        Decimal("0"),
    ).quantize(Decimal("0.01"))

    for prod, qty, _, _lg in gross_by_idx:
        check_stock_available(prod, qty)

    _reverse_commission_vendor_payables(invoice=inv)
    _reverse_customer_credit_for_invoice(invoice=inv)
    _reverse_sale_invoice_journals(
        invoice=inv,
        user=user,
        reason=f"تعديل كامل فاتورة {inv.invoice_number}",
    )

    old_lines = list(inv.lines.select_related("product").all())
    for ln in old_lines:
        return_sale_consumption(
            product=ln.product,
            quantity=as_decimal(ln.quantity),
            session=session,
            invoice_pk=inv.pk,
            sale_line=ln,
        )

    inv.payments.all().delete()
    inv.lines.all().delete()

    total_cost = Decimal("0")
    total_profit = Decimal("0")
    created: List[SaleInvoiceLine] = []
    for prod, qty, price, lg in gross_by_idx:
        share = (lg / gross_sum) if gross_sum else Decimal("0")
        line_discount = (discount_total * share).quantize(Decimal("0.01"))
        adjusted_line_sub = (lg - line_discount).quantize(Decimal("0.01"))
        if adjusted_line_sub < 0:
            adjusted_line_sub = Decimal("0")
        uc = get_unit_cost(prod)
        line_cost = (as_decimal(qty) * uc).quantize(Decimal("0.01"))
        if prod.product_type == prod.ProductType.COMMISSION:
            pct = as_decimal(prod.commission_percentage or 0)
            recognized = (adjusted_line_sub * pct / Decimal("100")).quantize(Decimal("0.01"))
            line_cost = Decimal("0")
            line_profit = recognized
        else:
            recognized = adjusted_line_sub
            line_profit = (recognized - line_cost).quantize(Decimal("0.01"))
        sil = SaleInvoiceLine.objects.create(
            invoice=inv,
            product=prod,
            quantity=as_decimal(qty),
            unit_price=as_decimal(price),
            line_subtotal=adjusted_line_sub,
            unit_cost_snapshot=uc,
            line_cost_total=line_cost,
            recognized_revenue=recognized,
            line_profit=line_profit,
        )
        created.append(sil)
        total_cost += line_cost
        total_profit += line_profit

    inv.subtotal = gross_sum
    inv.total = new_total
    inv.total_cost = total_cost.quantize(Decimal("0.01"))
    inv.total_profit = total_profit.quantize(Decimal("0.01"))

    commission_vendors = set()
    for prod, _, _, _ in gross_by_idx:
        if prod.product_type == prod.ProductType.COMMISSION and prod.commission_vendor_id:
            commission_vendors.add(prod.commission_vendor_id)
    if len(commission_vendors) == 1:
        inv.supplier_buyer_id = commission_vendors.pop()
    else:
        inv.supplier_buyer_id = None

    inv.save(
        update_fields=[
            "subtotal",
            "total",
            "total_cost",
            "total_profit",
            "supplier_buyer",
            "updated_at",
        ]
    )

    for method, amount, payer_name, payer_phone in payment_rows:
        amt = as_decimal(amount)
        if amt <= 0:
            continue
        InvoicePayment.objects.create(
            invoice=inv,
            method=str(method).strip().lower(),
            amount=amt,
            payer_name=str(payer_name or "").strip()[:120],
            payer_phone=str(payer_phone or "").strip()[:40],
            payment_source="",
        )

    cust = inv.customer
    if credit_total > 0 and cust:
        cust.balance = (as_decimal(cust.balance) + credit_total).quantize(Decimal("0.01"))
        cust.save(update_fields=["balance", "updated_at"])
        from apps.contacts.models import CustomerLedgerEntry

        CustomerLedgerEntry.objects.create(
            customer=cust,
            entry_type=CustomerLedgerEntry.EntryType.INVOICE,
            amount=credit_total,
            note=f"فاتورة {inv.invoice_number}",
            reference_model="billing.SaleInvoice",
            reference_pk=str(inv.pk),
        )
        from apps.payroll.invoice_link import maybe_record_employee_cafe_from_invoice_credit
        from apps.purchasing.invoice_link import maybe_record_supplier_cafe_from_invoice_credit

        maybe_record_employee_cafe_from_invoice_credit(
            invoice=inv,
            customer=cust,
            credit_total=credit_total,
            work_session=inv.work_session,
        )
        maybe_record_supplier_cafe_from_invoice_credit(
            invoice=inv,
            customer=cust,
            credit_total=credit_total,
            work_session=inv.work_session,
        )

    for sil, (prod, qty, _, _) in zip(created, gross_by_idx):
        consume_for_sale(
            product=prod,
            quantity=as_decimal(qty),
            session=session,
            invoice_pk=inv.pk,
            sale_line=sil,
        )

    pay_by_method: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for method, amount, _, _ in payment_rows:
        amt = as_decimal(amount)
        if amt <= 0:
            continue
        pay_by_method[str(method).strip().lower()] += amt

    post_sale_invoice_journal(invoice=inv, pay_by_method=dict(pay_by_method), user=user)
    tab_svc._record_commission_vendor_payables(inv)

    log_audit(
        user,
        "sale.invoice.full_edited",
        "billing.SaleInvoice",
        str(inv.pk),
        {"invoice_number": inv.invoice_number, "new_total": str(new_total)},
    )
