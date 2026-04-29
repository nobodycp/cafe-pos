from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional, Sequence, Tuple

from django.db import transaction
from django.db.models import F

from apps.catalog.models import Product, ProductModifierOption
from apps.contacts.models import Customer
from apps.core.models import log_audit
from apps.core.services import SessionService
from apps.pos.models import DiningTable, Order, OrderLine, TableSession


def _d(v):
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _modifiers_payload(options: Sequence[ProductModifierOption]) -> list:
    out = []
    for o in sorted(options, key=lambda x: x.pk):
        out.append({"id": o.pk, "name_ar": o.name_ar, "delta": str(o.price_delta)})
    return out


def resolve_modifier_options(
    *, product: Product, option_ids: Sequence[int]
) -> Tuple[list, Decimal, list]:
    """يتحقق من المجموعات (min/max) ويعيد الخيارات والمبلغ الإضافي و JSON للتخزين."""
    ids = [int(x) for x in option_ids if x is not None]
    if not ids:
        for g in product.modifier_groups.all():
            if g.min_select > 0:
                raise ValueError("MODIFIERS_REQUIRED")
        return [], Decimal("0"), []

    opts = list(
        ProductModifierOption.objects.filter(pk__in=ids, group__product=product).select_related("group")
    )
    if len(opts) != len(set(ids)):
        raise ValueError("INVALID_MODIFIER")
    by_group: dict = {}
    for o in opts:
        by_group.setdefault(o.group_id, []).append(o)
    for g in product.modifier_groups.all():
        picked = by_group.get(g.pk, [])
        n = len(picked)
        if n < g.min_select or n > g.max_select:
            raise ValueError("MODIFIER_COUNT_INVALID")
    extra = sum((_d(o.price_delta) for o in opts), Decimal("0")).quantize(Decimal("0.01"))
    return opts, extra, _modifiers_payload(opts)


@transaction.atomic
def create_order(
    *,
    user,
    order_type: str,
    table: Optional[DiningTable] = None,
    customer: Optional[Customer] = None,
    order_note: str = "",
    table_session: Optional[TableSession] = None,
) -> Order:
    session = SessionService.require_open_session()
    order = Order.objects.create(
        work_session=session,
        order_type=order_type,
        table=table,
        customer=customer,
        order_note=order_note or "",
        table_session=table_session,
    )
    log_audit(user, "pos.order.create", "pos.Order", order.pk, {"type": order_type})
    return order


@transaction.atomic
def add_or_update_line(
    *,
    order: Order,
    product: Product,
    quantity_delta: Decimal,
    user,
    modifier_option_ids: Optional[Sequence[int]] = None,
    line_note: str = "",
    bump_kitchen: bool = False,
) -> Optional[OrderLine]:
    if order.status != Order.Status.OPEN:
        raise ValueError("ORDER_NOT_OPEN")
    if order.is_held:
        raise ValueError("ORDER_HELD")
    qty_delta = _d(quantity_delta)
    _opts, extra_unit, mod_json = resolve_modifier_options(
        product=product, option_ids=modifier_option_ids or ()
    )
    mod_key = json.dumps(mod_json, sort_keys=True, ensure_ascii=False)
    unit_price = product.selling_price
    line_note = (line_note or "")[:255]

    if bump_kitchen:
        Order.objects.filter(pk=order.pk).update(kitchen_batch_no=F("kitchen_batch_no") + 1)
        order.refresh_from_db(fields=["kitchen_batch_no", "updated_at"])
    batch_no = order.kitchen_batch_no

    line = None
    for ln in OrderLine.objects.filter(order=order, product=product, line_note=line_note):
        if json.dumps(ln.modifiers_json or [], sort_keys=True, ensure_ascii=False) == mod_key:
            line = ln
            break

    if line:
        new_q = _d(line.quantity) + qty_delta
        if new_q <= 0:
            line.delete()
            log_audit(user, "pos.line.remove", "pos.Order", order.pk, {"product": product.pk})
            return None
        line.quantity = new_q
        # لا نعيد كتابة unit_price عند دمج الكمية — يحافظ على السعر اليدوي إن وُجد
        line.extra_unit_price = extra_unit
        line.modifiers_json = mod_json
        line.kitchen_batch_no = batch_no
        line.save(
            update_fields=[
                "quantity",
                "extra_unit_price",
                "modifiers_json",
                "kitchen_batch_no",
                "updated_at",
            ]
        )
        log_audit(user, "pos.line.update", "pos.Order", order.pk, {"product": product.pk, "qty": str(new_q)})
        return line
    if qty_delta <= 0:
        raise ValueError("INVALID_QTY")
    line = OrderLine.objects.create(
        order=order,
        product=product,
        quantity=qty_delta,
        unit_price=unit_price,
        extra_unit_price=extra_unit,
        line_note=line_note,
        modifiers_json=mod_json,
        kitchen_batch_no=batch_no,
    )
    log_audit(user, "pos.line.add", "pos.Order", order.pk, {"product": product.pk, "batch": batch_no})
    return line


@transaction.atomic
def set_line_quantity(*, order: Order, line_id: int, quantity: Decimal, user) -> Optional[OrderLine]:
    if order.status != Order.Status.OPEN or order.is_held:
        raise ValueError("ORDER_NOT_OPEN")
    new_q = _d(quantity).quantize(Decimal("0.001"))
    line = OrderLine.objects.filter(pk=line_id, order=order).first()
    if not line:
        raise ValueError("LINE_NOT_FOUND")
    if new_q <= 0:
        line.delete()
        log_audit(user, "pos.line.remove", "pos.Order", order.pk, {"line": line_id})
        return None
    line.quantity = new_q
    line.save(update_fields=["quantity", "updated_at"])
    log_audit(user, "pos.line.update", "pos.Order", order.pk, {"line": line_id, "qty": str(new_q)})
    return line


@transaction.atomic
def adjust_line_quantity(*, order: Order, line_id: int, quantity_delta: Decimal, user) -> Optional[OrderLine]:
    if order.status != Order.Status.OPEN or order.is_held:
        raise ValueError("ORDER_NOT_OPEN")
    qty_delta = _d(quantity_delta)
    line = OrderLine.objects.filter(pk=line_id, order=order).first()
    if not line:
        raise ValueError("LINE_NOT_FOUND")
    new_q = _d(line.quantity) + qty_delta
    if new_q <= 0:
        line.delete()
        log_audit(user, "pos.line.remove", "pos.Order", order.pk, {"line": line_id})
        return None
    line.quantity = new_q
    line.save(update_fields=["quantity", "updated_at"])
    log_audit(user, "pos.line.update", "pos.Order", order.pk, {"line": line_id, "qty": str(new_q)})
    return line


@transaction.atomic
def delete_order_line(*, order: Order, line_id: int, user) -> None:
    if order.status != Order.Status.OPEN or order.is_held:
        raise ValueError("ORDER_NOT_OPEN")
    n, _ = OrderLine.objects.filter(pk=line_id, order=order).delete()
    if n:
        log_audit(user, "pos.line.remove", "pos.Order", order.pk, {"line": line_id})


@transaction.atomic
def set_line_note(*, order: Order, line_id: int, line_note: str, user) -> None:
    line = OrderLine.objects.filter(pk=line_id, order=order).first()
    if not line or order.status != Order.Status.OPEN:
        raise ValueError("LINE_NOT_FOUND")
    line.line_note = (line_note or "")[:255]
    line.save(update_fields=["line_note", "updated_at"])
    log_audit(user, "pos.line.note", "pos.Order", order.pk, {"line": line_id})


@transaction.atomic
def set_line_unit_price(*, order: Order, line_id: int, unit_price: Decimal, user) -> None:
    if order.status != Order.Status.OPEN or order.is_held:
        raise ValueError("ORDER_NOT_OPEN")
    line = OrderLine.objects.select_for_update().filter(pk=line_id, order=order).first()
    if not line:
        raise ValueError("LINE_NOT_FOUND")
    up = _d(unit_price).quantize(Decimal("0.01"))
    if up < 0:
        raise ValueError("INVALID_UNIT_PRICE")
    line.unit_price = up
    line.save(update_fields=["unit_price", "updated_at"])
    log_audit(user, "pos.line.unit_price", "pos.Order", order.pk, {"line": line_id, "unit_price": str(up)})


@transaction.atomic
def hold_order(*, order: Order, user) -> None:
    if order.status != Order.Status.OPEN:
        raise ValueError("ORDER_NOT_OPEN")
    order.is_held = True
    order.save(update_fields=["is_held", "updated_at"])
    log_audit(user, "pos.order.hold", "pos.Order", order.pk, {})
