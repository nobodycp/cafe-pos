from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import DecimalField, F, Value
from django.db.models.functions import Coalesce
from apps.catalog.models import Product, RecipeLine
from apps.core.models import WorkSession, get_pos_settings
from apps.core.decimalutil import as_decimal
from apps.inventory.models import ManufacturingBatch, StockBalance, StockMovement


def is_manual_stock_movement(mv: StockMovement) -> bool:
    """حركات التسوية/الشراء اليدوية من الواجهة (قابلة للتعديل أو الحذف)."""
    return (mv.reference_model or "").strip() == "manual"


def _locked_balance(product: Product) -> StockBalance:
    sb, _ = StockBalance.objects.select_for_update().get_or_create(
        product=product,
        defaults={"quantity_on_hand": Decimal("0"), "average_cost": Decimal("0")},
    )
    return StockBalance.objects.select_for_update().get(pk=sb.pk)


def _apply_purchase_to_balance(product: Product, quantity: Decimal, unit_cost: Decimal) -> None:
    qty = as_decimal(quantity)
    cost = as_decimal(unit_cost)
    if qty <= 0:
        raise ValueError("INVALID_QTY")
    sb = _locked_balance(product)
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


def _apply_quantity_delta_to_balance(product: Product, quantity_delta: Decimal) -> None:
    sb = _locked_balance(product)
    new_q = as_decimal(sb.quantity_on_hand) + as_decimal(quantity_delta)
    if new_q < 0 and not get_pos_settings().allow_negative_stock:
        raise ValueError("INSUFFICIENT_STOCK")
    sb.quantity_on_hand = new_q
    sb.save(update_fields=["quantity_on_hand", "updated_at"])


def ensure_stock_balance(product: Product) -> StockBalance:
    sb, _ = StockBalance.objects.select_for_update().get_or_create(
        product=product,
        defaults={"quantity_on_hand": Decimal("0"), "average_cost": Decimal("0")},
    )
    return sb


def sync_missing_stock_balance_rows() -> int:
    """يُنشئ سطر رصيد صفر لكل منتج متتبع بلا StockBalance (لظهوره في شاشة المخزون)."""
    tracked_ids = list(Product.objects.filter(is_stock_tracked=True).values_list("pk", flat=True))
    if not tracked_ids:
        return 0
    have = set(StockBalance.objects.filter(product_id__in=tracked_ids).values_list("product_id", flat=True))
    created = 0
    for pid in tracked_ids:
        if pid in have:
            continue
        StockBalance.objects.get_or_create(
            product_id=pid,
            defaults={"quantity_on_hand": Decimal("0"), "average_cost": Decimal("0")},
        )
        created += 1
    return created


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


def assert_manufactured_bom_available(product: Product, units: Decimal) -> None:
    """تحقق كفاية مكوّنات المعادلة لعدد وحدات من المنتج المصنع (تصنيع دفعي أو بيع من BOM)."""
    if get_pos_settings().allow_negative_stock:
        return
    u = as_decimal(units)
    if u <= 0:
        return
    aggregated: dict[int, Decimal] = {}
    for line in RecipeLine.objects.filter(manufactured_product=product).select_related("component"):
        if line.component.is_stock_tracked:
            cid = line.component_id
            aggregated[cid] = aggregated.get(cid, Decimal("0")) + as_decimal(line.quantity_per_unit) * u
    for cid, need in aggregated.items():
        sb = StockBalance.objects.select_for_update().filter(product_id=cid).first()
        on_hand = as_decimal(sb.quantity_on_hand) if sb else Decimal("0")
        if on_hand < need:
            raise ValueError(f"INSUFFICIENT_STOCK:{cid}")


@transaction.atomic
def record_manufacturing_batch(
    *,
    product: Product,
    quantity: Decimal,
    session: Optional[WorkSession],
    note: str = "",
) -> ManufacturingBatch:
    """تسجيل دفعة إنتاج: خصم المواد حسب BOM وزيادة رصيد المنتج المصنع."""
    if product.product_type != Product.ProductType.MANUFACTURED:
        raise ValueError("NOT_MANUFACTURED")
    qty = as_decimal(quantity)
    if qty <= 0:
        raise ValueError("INVALID_QTY")
    if not RecipeLine.objects.filter(manufactured_product=product).exists():
        raise ValueError("NO_RECIPE")
    assert_manufactured_bom_available(product, qty)
    if not product.is_stock_tracked:
        product.is_stock_tracked = True
        product.save(update_fields=["is_stock_tracked", "updated_at"])
    ensure_stock_balance(product)
    batch = ManufacturingBatch.objects.create(
        product=product,
        quantity=qty,
        work_session=session,
        note=note or "",
    )
    ref_pk = str(batch.pk)
    for line in RecipeLine.objects.filter(manufactured_product=product).select_related("component"):
        if not line.component.is_stock_tracked:
            continue
        comp_qty = as_decimal(line.quantity_per_unit) * qty
        if comp_qty <= 0:
            continue
        adjust_stock(
            product=line.component,
            quantity_delta=-comp_qty,
            movement_type=StockMovement.MovementType.MANUFACTURING,
            session=session,
            reference_model="inventory.ManufacturingBatch",
            reference_pk=ref_pk,
            note=f"BOM {product.pk} batch {ref_pk}",
        )
    unit_cost = get_unit_cost(product)
    _apply_purchase_to_balance(product, qty, unit_cost)
    StockMovement.objects.create(
        product=product,
        movement_type=StockMovement.MovementType.PRODUCTION,
        quantity_delta=qty,
        unit_cost=unit_cost,
        work_session=session,
        reference_model="inventory.ManufacturingBatch",
        reference_pk=ref_pk,
        note=note or f"إنتاج دفعة {ref_pk}",
    )
    return batch


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
    _apply_purchase_to_balance(product, qty, cost)
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
    _apply_quantity_delta_to_balance(product, as_decimal(quantity_delta))
    sb = ensure_stock_balance(product)
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
def reverse_manual_stock_movement_effect(mv: StockMovement) -> None:
    """عكس أثر حركة يدوية على الرصيد فقط (قبل حذف السطر أو إعادة تطبيقه)."""
    if not is_manual_stock_movement(mv):
        raise ValueError("MOVEMENT_NOT_MANUAL")
    product = Product.objects.get(pk=mv.product_id)
    if mv.movement_type == StockMovement.MovementType.PURCHASE:
        qty = as_decimal(mv.quantity_delta)
        cost = as_decimal(mv.unit_cost or 0)
        if qty <= 0:
            raise ValueError("INVALID_MOVEMENT")
        sb = _locked_balance(product)
        cur_q = as_decimal(sb.quantity_on_hand)
        cur_avg = as_decimal(sb.average_cost)
        if cur_q < qty and not get_pos_settings().allow_negative_stock:
            raise ValueError("INSUFFICIENT_STOCK")
        new_q = cur_q - qty
        if new_q > 0:
            old_avg = (cur_q * cur_avg - qty * cost) / new_q
        else:
            old_avg = Decimal("0")
        sb.quantity_on_hand = new_q
        sb.average_cost = old_avg
        sb.save(update_fields=["quantity_on_hand", "average_cost", "updated_at"])
    else:
        sb = _locked_balance(product)
        cur_q = as_decimal(sb.quantity_on_hand)
        delta = as_decimal(mv.quantity_delta)
        new_q = cur_q - delta
        if new_q < 0 and not get_pos_settings().allow_negative_stock:
            raise ValueError("INSUFFICIENT_STOCK")
        sb.quantity_on_hand = new_q
        sb.save(update_fields=["quantity_on_hand", "updated_at"])


@transaction.atomic
def delete_manual_stock_movement(mv: StockMovement) -> None:
    if not is_manual_stock_movement(mv):
        raise ValueError("MOVEMENT_NOT_MANUAL")
    reverse_manual_stock_movement_effect(mv)
    mv.delete()


@transaction.atomic
def update_manual_stock_movement(
    *,
    mv: StockMovement,
    product: Product,
    kind: str,
    quantity: Decimal,
    unit_cost: Decimal,
    note: str,
    session: Optional[WorkSession],
) -> StockMovement:
    """
    kind: purchase | adjustment | waste
    quantity: موجبة للشراء والهالك؛ للتسوية قد تكون سالبة.
    """
    if not is_manual_stock_movement(mv):
        raise ValueError("MOVEMENT_NOT_MANUAL")
    if not product.is_stock_tracked:
        raise ValueError("PRODUCT_NOT_STOCK_TRACKED")
    reverse_manual_stock_movement_effect(mv)
    qty = as_decimal(quantity)
    cost = as_decimal(unit_cost or 0)
    if kind == "purchase":
        if qty <= 0:
            raise ValueError("INVALID_QTY")
        _apply_purchase_to_balance(product, qty, cost)
        mv.product_id = product.pk
        mv.movement_type = StockMovement.MovementType.PURCHASE
        mv.quantity_delta = qty
        mv.unit_cost = cost
        mv.reference_pk = "adjust"
    elif kind == "waste":
        if qty <= 0:
            raise ValueError("INVALID_QTY")
        w = -qty
        _apply_quantity_delta_to_balance(product, w)
        sb = ensure_stock_balance(product)
        mv.product_id = product.pk
        mv.movement_type = StockMovement.MovementType.WASTE
        mv.quantity_delta = w
        mv.unit_cost = as_decimal(sb.average_cost)
        mv.reference_pk = "waste"
    elif kind == "adjustment":
        if qty == 0:
            raise ValueError("INVALID_QTY")
        _apply_quantity_delta_to_balance(product, qty)
        sb = ensure_stock_balance(product)
        mv.product_id = product.pk
        mv.movement_type = StockMovement.MovementType.ADJUSTMENT
        mv.quantity_delta = qty
        mv.unit_cost = as_decimal(sb.average_cost)
        mv.reference_pk = "adjust"
    else:
        raise ValueError("INVALID_KIND")
    mv.note = note or ""
    mv.work_session = session
    mv.reference_model = "manual"
    mv.save(
        update_fields=[
            "product_id",
            "movement_type",
            "quantity_delta",
            "unit_cost",
            "note",
            "work_session",
            "reference_model",
            "reference_pk",
            "updated_at",
        ]
    )
    return mv


@transaction.atomic
def consume_for_sale(
    *,
    product: Product,
    quantity: Decimal,
    session: WorkSession,
    invoice_pk: int,
    sale_line: "SaleInvoiceLine | None" = None,
) -> None:
    qty = as_decimal(quantity)
    if qty <= 0:
        return
    ptype = product.product_type
    if ptype == Product.ProductType.SERVICE or ptype == Product.ProductType.COMMISSION:
        return
    if ptype == Product.ProductType.MANUFACTURED:
        take_f = Decimal("0")
        remainder = qty
        if product.is_stock_tracked:
            sb = StockBalance.objects.select_for_update().filter(product=product).first()
            finished = as_decimal(sb.quantity_on_hand) if sb else Decimal("0")
            take_f = min(finished, qty)
            remainder = qty - take_f
        if take_f > 0:
            adjust_stock(
                product=product,
                quantity_delta=-take_f,
                movement_type=StockMovement.MovementType.SALE,
                session=session,
                reference_model="billing.SaleInvoice",
                reference_pk=str(invoice_pk),
                note="",
            )
        if remainder > 0:
            aggregated: dict[int, Decimal] = {}
            for line in RecipeLine.objects.filter(manufactured_product=product).select_related("component"):
                if not line.component.is_stock_tracked:
                    continue
                cid = line.component_id
                comp_qty = as_decimal(line.quantity_per_unit) * remainder
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
        if sale_line is not None and product.is_stock_tracked:
            prev_dec = (
                as_decimal(sale_line.manufacturing_finished_qty)
                if sale_line.manufacturing_finished_qty is not None
                else Decimal("0")
            )
            sale_line.manufacturing_finished_qty = prev_dec + take_f
            sale_line.save(update_fields=["manufacturing_finished_qty", "updated_at"])
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
    sale_line: "SaleInvoiceLine | None" = None,
) -> None:
    """عكس استهلاك البيع: إرجاع كمية للمخزون عند تقليل كمية سطر فاتورة (تعديل فاتورة)."""
    qty = as_decimal(quantity)
    if qty <= 0:
        return
    ptype = product.product_type
    if ptype == Product.ProductType.SERVICE or ptype == Product.ProductType.COMMISSION:
        return
    if ptype == Product.ProductType.MANUFACTURED:
        prev_fin = Decimal("0")
        if (
            sale_line is not None
            and product.is_stock_tracked
            and sale_line.manufacturing_finished_qty is not None
        ):
            prev_fin = as_decimal(sale_line.manufacturing_finished_qty)
        fin_ret = min(qty, prev_fin)
        bom_ret = qty - fin_ret
        if fin_ret > 0 and product.is_stock_tracked:
            adjust_stock(
                product=product,
                quantity_delta=fin_ret,
                movement_type=StockMovement.MovementType.ADJUSTMENT,
                session=session,
                reference_model="billing.SaleInvoice",
                reference_pk=str(invoice_pk),
                note="إرجاع مخزون جاهز بعد تعديل فاتورة",
            )
        if bom_ret > 0:
            aggregated: dict[int, Decimal] = {}
            for line in RecipeLine.objects.filter(manufactured_product=product).select_related("component"):
                if not line.component.is_stock_tracked:
                    continue
                cid = line.component_id
                comp_qty = as_decimal(line.quantity_per_unit) * bom_ret
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
        if sale_line is not None and product.is_stock_tracked and sale_line.manufacturing_finished_qty is not None:
            new_fin = as_decimal(sale_line.manufacturing_finished_qty) - fin_ret
            if new_fin <= 0:
                sale_line.manufacturing_finished_qty = None
            else:
                sale_line.manufacturing_finished_qty = new_fin
            sale_line.save(update_fields=["manufacturing_finished_qty", "updated_at"])
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
        remainder = qty
        if product.is_stock_tracked:
            sb = StockBalance.objects.select_for_update().filter(product=product).first()
            finished = as_decimal(sb.quantity_on_hand) if sb else Decimal("0")
            remainder = max(Decimal("0"), qty - finished)
        if remainder <= 0:
            return
        aggregated: dict[int, Decimal] = {}
        for line in RecipeLine.objects.filter(manufactured_product=product).select_related("component"):
            if line.component.is_stock_tracked:
                cid = line.component_id
                aggregated[cid] = aggregated.get(cid, Decimal("0")) + as_decimal(line.quantity_per_unit) * remainder
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


def stock_home_base_queryset(*, active_products_only: bool = False):
    """أرصدة كما في صفحة المخزون الرئيسية (بدون ترتيب أو select_related إضافي)."""
    qs = StockBalance.objects.filter(product__is_stock_tracked=True)
    if active_products_only:
        qs = qs.filter(product__is_active=True)
    return qs


def low_stock_alert_queryset():
    """أصناف عند أو تحت الحد الأدنى (نفس منطق فلتر المخزون «منخفض») — منتجات نشطة فقط."""
    zero_min = Value(Decimal("0"), output_field=DecimalField(max_digits=20, decimal_places=6))
    return (
        stock_home_base_queryset(active_products_only=True)
        .select_related("product", "product__unit")
        .annotate(_min_lvl=Coalesce(F("product__min_stock_level"), zero_min))
        .filter(quantity_on_hand__lte=F("_min_lvl"))
        .order_by("quantity_on_hand")
    )
