from decimal import Decimal

from django.db import models
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Customer(TimeStampedModel):
    name_ar = models.CharField(_("الاسم"), max_length=200)
    name_en = models.CharField(_("الاسم (إنجليزي)"), max_length=200, blank=True)
    phone = models.CharField(_("الهاتف"), max_length=32, blank=True)
    balance = models.DecimalField(_("الرصيد (عليه)"), max_digits=14, decimal_places=2, default=0)
    is_active = models.BooleanField(_("نشط"), default=True)

    class Meta:
        verbose_name = _("عميل")
        verbose_name_plural = _("العملاء")
        ordering = ("name_ar",)

    def __str__(self):
        return self.name_ar

    def display(self, lang: str = "ar") -> str:
        if lang == "en" and self.name_en:
            return self.name_en
        return self.name_ar

    @property
    def computed_balance(self) -> Decimal:
        """الرصيد المحسوب من دفتر العميل (مجموع القيود)."""
        agg = self.ledger_entries.aggregate(s=Sum("amount"))
        return (agg["s"] or Decimal("0")).quantize(Decimal("0.01"))


class CustomerLedgerEntry(TimeStampedModel):
    class EntryType(models.TextChoices):
        INVOICE = "invoice", _("فاتورة بيع آجل")
        PAYMENT = "payment", _("سداد")
        ADJUSTMENT = "adjustment", _("تسوية")

    customer = models.ForeignKey(
        Customer,
        verbose_name=_("العميل"),
        related_name="ledger_entries",
        on_delete=models.PROTECT,
    )
    entry_type = models.CharField(_("نوع القيد"), max_length=20, choices=EntryType.choices)
    amount = models.DecimalField(_("المبلغ (+ دين / - سداد)"), max_digits=14, decimal_places=2)
    note = models.TextField(_("ملاحظة"), blank=True)
    reference_model = models.CharField(_("مرجع"), max_length=128, blank=True)
    reference_pk = models.CharField(_("معرف المرجع"), max_length=64, blank=True)

    class Meta:
        verbose_name = _("قيد عميل")
        verbose_name_plural = _("دفتر العملاء")
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.customer_id} {self.amount}"
