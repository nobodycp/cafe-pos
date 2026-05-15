"""نصوص الإيصال الحراري الافتراضية + دمج التخصيصات من PosSettings.receipt_label_overrides."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from apps.core.models import PosSettings

# (مفتاح JSON، القيمة الافتراضية، عنوان الحقل في الإعدادات، تلميح قصير)
RECEIPT_LABEL_FORM_META: List[Tuple[str, str, str, str]] = [
    ("inv_title", "فاتورة بيع رقم:", "عنوان الفاتورة (قبل الرقم)", ""),
    ("cancelled", "ملغاة", "نص «ملغاة»", ""),
    ("table_lbl", "الطاولة:", "تسمية الطاولة", ""),
    ("customer_lbl", "العميل:", "تسمية العميل", ""),
    ("session_lbl", "الوردية:", "تسمية الوردية", ""),
    ("th_hash", "#", "عمود الترقيم في الجدول", ""),
    ("th_item", "الصنف", "عمود اسم الصنف", ""),
    ("th_qty", "الكمية", "عمود الكمية", ""),
    ("th_price", "السعر", "عمود السعر", ""),
    ("th_total", "المجموع", "عمود المجموع", ""),
    ("sum_subtotal", "المجموع قبل الخصم", "سطر المجموع الفرعي", ""),
    ("sum_discount", "الخصم", "سطر الخصم", ""),
    ("sum_service", "خدمة", "سطر الخدمة", ""),
    ("sum_tax", "ضريبة", "سطر الضريبة", ""),
    ("sum_grand", "الإجمالي", "سطر الإجمالي النهائي", ""),
    ("pay_title", "طرق الدفع", "عنوان قسم الدفعات", ""),
    ("thanks", "شكراً لزيارتكم", "سطر الشكر (اتركه فارغاً لإخفائه)", ""),
    ("phone_lbl", "هاتف:", "قبل رقم الهاتف (من إعدادات المقهى)", ""),
    ("address_lbl", "العنوان:", "قبل نص العنوان", ""),
    ("tax_id_lbl", "الرقم الضريبي:", "قبل الرقم الضريبي", ""),
    ("kitchen_head", "مطبخ — طلب #", "بداية سطر طلب المطبخ (قبل رقم الطلب)", ""),
    ("kitchen_batch_full", "طلب كامل", "تسمية دفعة المطبخ الكاملة", ""),
    ("kitchen_batch_prefix", "دفعة", "بادئة رقم الدفعة (مثال: دفعة 2)", ""),
]

DEFAULTS: Dict[str, str] = {k: v for k, v, _, _ in RECEIPT_LABEL_FORM_META}


def merged_receipt_label_dict(pos: PosSettings) -> Dict[str, str]:
    raw: Any = getattr(pos, "receipt_label_overrides", None) or {}
    if not isinstance(raw, dict):
        raw = {}
    out: Dict[str, str] = {}
    for k, default in DEFAULTS.items():
        if k in raw and raw[k] is not None:
            out[k] = str(raw[k])
        else:
            out[k] = default
    return out


class ReceiptLabelsView:
    """يسمح في القالب بـ {{ POS_SETTINGS.receipt_labels.inv_title }}."""

    __slots__ = ("_d",)

    def __init__(self, d: Dict[str, str]):
        object.__setattr__(self, "_d", d)

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._d.get(name, "")
