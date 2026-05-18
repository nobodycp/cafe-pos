"""تسجيل مرتجع بيع — منطق معاملات منفصل عن طبقة HTTP."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple

from django.db import transaction

from apps.billing.models import SaleInvoice, SaleInvoiceLine, SaleReturn, SaleReturnLine
from apps.contacts.models import CustomerLedgerEntry
from apps.core.models import log_audit
from apps.core.sequences import next_int
from apps.core.services import SessionService
from apps.inventory.models import StockMovement
from apps.inventory.services import adjust_stock


from apps.core.exceptions import BusinessError


class SaleReturnValidationError(BusinessError):
    """أخطاء تحقق قبل إنشاء المرتجع."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors), code="SALE_RETURN_VALIDATION")


def parse_sale_return_lines_from_post(
    invoice: SaleInvoice,
    post_data,
) -> Tuple[List[Tuple[SaleInvoiceLine, Decimal]], List[str]]:
    """يحلّل كميات المرتجع من POST ويُرجع (أسطر صالحة، أخطاء)."""
    inv_lines = list(invoice.lines.select_related("product").order_by("pk"))
    errors: List[str] = []
    return_lines: List[Tuple[SaleInvoiceLine, Decimal]] = []

    for line in inv_lines:
        qty_str = (post_data.get(f"qty_{line.pk}") or "").strip()
        if not qty_str:
            continue
        try:
            qty = Decimal(qty_str)
            if qty <= 0:
                continue
            if qty > line.quantity:
                errors.append(f"الكمية المرتجعة لـ {line.product.name_ar} أكبر من المباعة")
                continue
            return_lines.append((line, qty))
        except (InvalidOperation, ValueError):
            errors.append(f"كمية غير صالحة لـ {line.product.name_ar}")

    if not return_lines and not errors:
        errors.append("يرجى تحديد كمية مرتجعة واحدة على الأقل")

    return return_lines, errors


@transaction.atomic
def create_sale_return(
    *,
    invoice: SaleInvoice,
    return_lines: List[Tuple[SaleInvoiceLine, Decimal]],
    reason: str,
    refund_method: str,
    user,
) -> SaleReturn:
    """يُنشئ مرتجع البيع مع تحديث المخزون والذمّة عند الحاجة."""
    total_refund = Decimal("0")
    ret = SaleReturn.objects.create(
        invoice=invoice,
        return_number=f"RET-{next_int('sale_return'):06d}",
        reason=reason,
        refund_method=refund_method,
    )

    session = SessionService.get_open_session()
    for inv_line, qty in return_lines:
        line_total = (qty * inv_line.unit_price).quantize(Decimal("0.01"))
        SaleReturnLine.objects.create(
            sale_return=ret,
            product=inv_line.product,
            quantity=qty,
            unit_price=inv_line.unit_price,
            line_total=line_total,
        )
        total_refund += line_total

        if inv_line.product.is_stock_tracked:
            adjust_stock(
                product=inv_line.product,
                quantity_delta=qty,
                movement_type=StockMovement.MovementType.ADJUSTMENT,
                session=session,
                reference_model="billing.SaleReturn",
                reference_pk=str(ret.pk),
                note=f"مرتجع بيع {ret.return_number}",
            )

    ret.total_refund = total_refund
    ret.save(update_fields=["total_refund", "updated_at"])

    if refund_method == "credit" and invoice.customer:
        cust = invoice.customer
        cust.balance = (cust.balance - total_refund).quantize(Decimal("0.01"))
        cust.save(update_fields=["balance", "updated_at"])
        CustomerLedgerEntry.objects.create(
            customer=cust,
            entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
            amount=-total_refund,
            note=f"مرتجع بيع {ret.return_number}",
            reference_model="billing.SaleReturn",
            reference_pk=str(ret.pk),
        )

    log_audit(
        user,
        "sale.return.created",
        "billing.SaleReturn",
        ret.pk,
        {"total": str(total_refund)},
    )
    return ret
