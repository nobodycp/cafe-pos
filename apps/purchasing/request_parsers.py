"""تحليل POST لفواتير الشراء — مشترك بين shell و POS."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from apps.catalog.models import Product, Unit
from apps.core.payment_methods import credit_method_codes, load_payment_method_rows
from apps.purchasing.models import Supplier


def payment_rows():
    """صفوف طرق الدفع للنماذج (نفس load_payment_method_rows)."""
    return load_payment_method_rows()


def purchase_payments_from_request(request, total: Decimal, errors: list) -> list[tuple[str, Decimal]]:
    codes = {row["code"] for row in payment_rows()}
    credit_codes = credit_method_codes()
    raw_splits = (request.POST.get("payment_splits_json") or "").strip()

    if raw_splits:
        from apps.core.payment_splits import PaymentSplitsParseError, parse_payment_splits_json

        try:
            lines = parse_payment_splits_json(
                raw_splits, allowed_codes=frozenset(codes), quantize=True
            )
        except PaymentSplitsParseError as exc:
            if exc.code == "INVALID_JSON":
                errors.append("بيانات تقسيم الدفع غير صالحة.")
            elif exc.code in ("INVALID_SHAPE", "TOO_MANY_ROWS"):
                errors.append("عدد أسطر الدفع غير صالح.")
            elif exc.code == "INVALID_METHOD":
                errors.append("طريقة دفع غير صالحة في التقسيم.")
            else:
                errors.append("مبلغ غير صالح في تقسيم الدفع.")
            return []
        if not lines:
            errors.append("أضف سطر دفع واحداً على الأقل في وضع التقسيم.")
            return []
        paid_sum = sum((a for _, a in lines), Decimal("0")).quantize(Decimal("0.01"))
        remainder = (total - paid_sum).quantize(Decimal("0.01"))
        if remainder > 0:
            ccode = next(iter(credit_codes), None) if credit_codes else None
            if not ccode:
                errors.append(
                    "مجموع أسطر الدفع أقل من صافي الفاتورة، وليست هناك طريقة «ذمّة/آجل» لتسجيل المتبقي."
                )
                return []
            lines.append((ccode, remainder))
        elif remainder < 0:
            errors.append("مجموع أسطر الدفع أكبر من صافي الفاتورة.")
            return []
        return lines

    pay_method = (request.POST.get("pay_method") or "").strip()
    pay_amount_str = request.POST.get("pay_amount", "0")

    if pay_method not in codes:
        errors.append("اختر طريقة دفع صالحة.")
        return []

    try:
        pay_amount = Decimal(str(pay_amount_str or "0")).quantize(Decimal("0.01"))
    except InvalidOperation:
        errors.append("المبلغ المدفوع غير صالح.")
        return []

    if pay_amount < 0:
        errors.append("المبلغ المدفوع لا يمكن أن يكون سالباً.")
        return []

    if pay_method in credit_codes:
        return [(pay_method, total)]

    if pay_amount <= 0:
        errors.append("أدخل المبلغ المدفوع أو اختر طريقة دفع آجلة.")
        return []
    if pay_amount > total:
        errors.append("المبلغ المدفوع أكبر من صافي الفاتورة.")
        return []

    payments = [(pay_method, pay_amount)]
    credit = (total - pay_amount).quantize(Decimal("0.01"))
    if credit > 0:
        credit_code = next(iter(credit_codes), "credit")
        payments.append((credit_code, credit))
    return payments


def purchase_lines_from_request(request, errors: list) -> list:
    lines = []
    for i in range(20):
        prod_id = (request.POST.get(f"product_{i}") or "").strip()
        qty = (request.POST.get(f"qty_{i}") or "").strip()
        cost = (request.POST.get(f"cost_{i}") or "").strip()
        discount = request.POST.get(f"discount_{i}", "0")
        if not prod_id:
            continue
        if not qty:
            errors.append(f"سطر {i + 1}: أدخل الكمية للصنف المختار")
            continue
        if not cost:
            errors.append(f"سطر {i + 1}: أدخل تكلفة الوحدة للصنف المختار")
            continue
        try:
            product = Product.objects.get(pk=int(prod_id))
            unit_id = request.POST.get(f"unit_{i}")
            if unit_id:
                try:
                    unit = Unit.objects.get(pk=int(unit_id))
                    if product.unit_id != unit.pk:
                        product.unit = unit
                        product.save(update_fields=["unit", "updated_at"])
                except (Unit.DoesNotExist, ValueError):
                    errors.append(f"سطر {i + 1}: وحدة غير صالحة")
                    continue
            q = Decimal(qty)
            c = Decimal(cost)
            d = Decimal(str(discount or "0"))
            if q <= 0 or c <= 0:
                errors.append(f"سطر {i + 1}: الكمية وسعر الوحدة يجب أن يكونا أكبر من صفر")
                continue
            if d < 0:
                errors.append(f"سطر {i + 1}: الخصم لا يمكن أن يكون سالباً")
                continue
            line_total = (q * c).quantize(Decimal("0.01"))
            if d > line_total:
                errors.append(f"سطر {i + 1}: الخصم أكبر من إجمالي السطر")
                continue
            effective_cost = ((line_total - d) / q).quantize(Decimal("0.000001"))
            lines.append((product, q, effective_cost))
        except (Product.DoesNotExist, InvalidOperation, ValueError):
            errors.append(f"سطر {i + 1}: بيانات غير صالحة")
    if not lines:
        if not any((e.startswith("سطر ") for e in errors)):
            errors.append("يرجى إدخال صنف واحد على الأقل")
    return lines


def purchase_form_state(request) -> dict:
    if request.method != "POST":
        return {}

    supplier_id = request.POST.get("supplier_id") or ""
    supplier_label = ""
    if supplier_id:
        try:
            supplier_label = Supplier.objects.get(pk=int(supplier_id)).name_ar
        except (Supplier.DoesNotExist, ValueError):
            supplier_label = request.POST.get("supplier_label", "")

    rows = []
    for i in range(20):
        product_id = request.POST.get(f"product_{i}") or ""
        unit_id = request.POST.get(f"unit_{i}") or ""
        product_label = ""
        unit_label = ""
        if product_id:
            try:
                product_label = Product.objects.get(pk=int(product_id)).name_ar
            except (Product.DoesNotExist, ValueError):
                product_label = ""
        if unit_id:
            try:
                unit_label = Unit.objects.get(pk=int(unit_id)).name_ar
            except (Unit.DoesNotExist, ValueError):
                unit_label = ""
        rows.append({
            "product_id": product_id,
            "product_label": product_label,
            "unit_id": unit_id,
            "unit_label": unit_label,
            "qty": request.POST.get(f"qty_{i}") or "",
            "cost": request.POST.get(f"cost_{i}") or "",
            "discount": request.POST.get(f"discount_{i}") or "",
        })

    return {
        "supplier_id": supplier_id,
        "supplier_label": supplier_label,
        "rows": rows,
        "general_discount": request.POST.get("general_discount") or "0.00",
        "pay_method": request.POST.get("pay_method") or "",
        "pay_amount": request.POST.get("pay_amount") or "",
        "payment_splits_json": (request.POST.get("payment_splits_json") or "").strip(),
        "use_payment_splits": (request.POST.get("use_payment_splits") or "").strip(),
    }
