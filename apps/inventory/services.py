from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django.db import transaction
from apps.catalog.models import Product, RecipeLine
from apps.core.models import WorkSession, get_pos_settings
from apps.core.decimalutil import as_decimal
from apps.inventory.models import StockBalance, StockMovement


def ensure_stock_balance(product: Product) -> StockBalance:
    sb, _ = StockBalance.objects.select_for_update().get_or_create(
        product=product,
        defaults={"quantity_on_hand": Decimal("0"), "average_cost": Decimal("0")},
    )
    return sb


def get_unit_cost(product: Product, _seen: Optional[set] = None) -> Decimal:
    """Latest valuation for one salable unit (manufacturing = rolled-up BOM cost)."""
    if product.product_type == Product.ProductType.COMMISSION:
        return Decimal("0")
    if product.product_type == Product.ProductType.SERVICE:
        return Decimal("0")
    if product.product_type == Product.ProductType.MANUFACTURED:
        if _seen is None:
            _seen = set()
        if product.pk in _seen:
            return Decimal("0")
        _seen.add(product.pk)
        total = Decimal("0")
        for line in RecipeLine.objects.filter(manufactured_product=product).select_related("component"):
            comp = line.component
            comp_cost = get_unit_cost(comp, _seen)
            total += as_decimal(line.quantity_per_unit) * comp_cost
        return total.quantize(Decimal("0.000001"))
    if product.is_stock_tracked:
        try:
            return as_decimal(product.stock_balance.average_cost)
        except StockBalance.DoesNotExist:
            return Decimal("0")
    return Decimal("0")


@transaction.atomic
def receive_purchase_stock(
    *,
    product: Product,
    quantity: Decimal,
    unit_cost: Decimal,
    session: Optional[WorkSession],
    reference_model: str,
    reference_pk: str,
    note: str = "",
) -> StockMovement:
    if not product.is_stock_tracked:
        raise ValueError("PRODUCT_NOT_STOCK_TRACKED")
    qty = as_decimal(quantity)
    cost = as_decimal(unit_cost)
    if qty <= 0:
        raise ValueError("INVALID_QTY")
    sb = ensure_stock_balance(product)
    old_q = as_decimal(sb.quantity_on_hand)
    old_avg = as_decimal(sb.average_cost)
    new_q = old_q + qty
    if new_q > 0:
        new_avg = ((old_q * old_avg) + (qty * cost)) / new_q
    else:
        new_avg = old_avg
    sb.quantity_on_hand = new_q
    sb.average_cost = new_avg
    sb.save(update_fields=["quantity_on_hand", "average_cost", "updated_at"])
    mv = StockMovement.objects.create(
        product=product,
        movement_type=StockMovement.MovementType.PURCHASE,
        quantity_delta=qty,
        unit_cost=cost,
        work_session=session,
        reference_model=reference_model,
        reference_pk=str(reference_pk),
        note=note,
    )
    return mv


@transaction.atomic
def adjust_stock(
    *,
    product: Product,
    quantity_delta: Decimal,
    movement_type: str,
    session: Optional[WorkSession],
    reference_model: str,
    reference_pk: str,
    note: str = "",
) -> StockMovement:
    if not product.is_stock_tracked:
        raise ValueError("PRODUCT_NOT_STOCK_TRACKED")
    sb = ensure_stock_balance(product)
    new_q = as_decimal(sb.quantity_on_hand) + as_decimal(quantity_delta)
    if new_q < 0 and not get_pos_settings().allow_negative_stock:
        raise ValueError("INSUFFICIENT_STOCK")
    sb.quantity_on_hand = new_q
    sb.save(update_fields=["quantity_on_hand", "updated_at"])
    return StockMovement.objects.create(
        product=product,
        movement_type=movement_type,
        quantity_delta=as_decimal(quantity_delta),
        unit_cost=as_decimal(sb.average_cost),
        work_session=session,
        reference_model=reference_model,
        reference_pk=str(reference_pk),
        note=note,
    )


@transaction.atomic
def consume_for_sale(
    *,
    product: Product,
    quantity: Decimal,
    session: WorkSession,
    invoice_pk: int,
) -> None:
    qty = as_decimal(quantity)
    if qty <= 0:
        return
    ptype = product.product_type
    if ptype == Product.ProductType.SERVICE or ptype == Product.ProductType.COMMISSION:
        return
    if ptype == Product.ProductType.MANUFACTURED:
        aggregated = {}
        for line in RecipeLine.objects.filter(manufactured_product=product).select_related("component"):
            if not line.component.is_stock_tracked:
                continue
            cid = line.component_id
            comp_qty = as_decimal(line.quantity_per_unit) * qty
            aggregated[cid] = aggregated.get(cid, Decimal("0")) + comp_qty
        for cid, total_qty in aggregated.items():
            comp = Product.objects.get(pk=cid)
            adjust_stock(
                product=comp,
                quantity_delta=-total_qty,
                movement_type=StockMovement.MovementType.MANUFACTURING,
                session=session,
                reference_model="billing.SaleInvoice",
                reference_pk=str(invoice_pk),
                note=f"BOM for product {product.pk}",
            )
        return
    if product.is_stock_tracked:
        adjust_stock(
            product=product,
            quantity_delta=-qty,
            movement_type=StockMovement.MovementType.SALE,
            session=session,
            reference_model="billing.SaleInvoice",
            reference_pk=str(invoice_pk),
            note="",
        )


@transaction.atomic
def return_sale_consumption(
    *,
    product: Product,
    quantity: Decimal,
    session: WorkSession,
    invoice_pk: int,
) -> None:
    """عكس استهلاك البيع: إرجاع كمية للمخزون عند تقليل كمية سطر فاتورة (تعديل فاتورة)."""
    qty = as_decimal(quantity)
    if qty <= 0:
        return
    ptype = product.product_type
    if ptype == Product.ProductType.SERVICE or ptype == Product.ProductType.COMMISSION:
        return
    if ptype == Product.ProductType.MANUFACTURED:
        aggregated: dict[int, Decimal] = {}
        for line in RecipeLine.objects.filter(manufactured_product=product).select_related("component"):
            if not line.component.is_stock_tracked:
                continue
            cid = line.component_id
            comp_qty = as_decimal(line.quantity_per_unit) * qty
            aggregated[cid] = aggregated.get(cid, Decimal("0")) + comp_qty
        for cid, total_qty in aggregated.items():
            comp = Product.objects.get(pk=cid)
            adjust_stock(
                product=comp,
                quantity_delta=total_qty,
                movement_type=StockMovement.MovementType.ADJUSTMENT,
                session=session,
                reference_model="billing.SaleInvoice",
                reference_pk=str(invoice_pk),
                note=f"إرجاع مخزون بعد تعديل فاتورة (BOM {product.pk})",
            )
        return
    if product.is_stock_tracked:
        adjust_stock(
            product=product,
            quantity_delta=qty,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            session=session,
            reference_model="billing.SaleInvoice",
            reference_pk=str(invoice_pk),
            note="إرجاع مخزون بعد تعديل فاتورة",
        )


def check_stock_available(product: Product, quantity: Decimal) -> None:
    if get_pos_settings().allow_negative_stock:
        return
    qty = as_decimal(quantity)
    ptype = product.product_type
    if ptype == Product.ProductType.SERVICE or ptype == Product.ProductType.COMMISSION:
        return
    if ptype == Product.ProductType.MANUFACTURED:
        aggregated = {}
        for line in RecipeLine.objects.filter(manufactured_product=product).select_related("component"):
            if line.component.is_stock_tracked:
                cid = line.component_id
                aggregated[cid] = aggregated.get(cid, Decimal("0")) + as_decimal(line.quantity_per_unit) * qty
        for cid, need in aggregated.items():
            sb = StockBalance.objects.select_for_update().filter(product_id=cid).first()
            on_hand = as_decimal(sb.quantity_on_hand) if sb else Decimal("0")
            if on_hand < need:
                raise ValueError(f"INSUFFICIENT_STOCK:{cid}")
        return
    if product.is_stock_tracked:
        sb = StockBalance.objects.select_for_update().filter(product=product).first()
        on_hand = as_decimal(sb.quantity_on_hand) if sb else Decimal("0")
        if on_hand < qty:
            raise ValueError(f"INSUFFICIENT_STOCK:{product.pk}")
