from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel, WorkSession


class ExpenseCategory(TimeStampedModel):
    class Code(models.TextChoices):
        SALARIES = "salaries", _("رواتب")
        FUEL = "fuel", _("وقود")
        CLEANING = "cleaning", _("تنظيف")
        SUPPLIES = "supplies", _("مستلزمات")
        INTERNET = "internet", _("إنترنت واتصالات")
        TRANSPORT = "transport", _("نقل")
        MAINTENANCE = "maintenance", _("صيانة")
        OTHER = "other", _("أخرى")

    code = models.CharField(_("الرمز"), max_length=32, choices=Code.choices, unique=True)
    name_ar = models.CharField(_("الاسم"), max_length=120)
    name_en = models.CharField(_("الاسم (إنجليزي)"), max_length=120, blank=True)

    class Meta:
        verbose_name = _("تصنيف مصروف")
        verbose_name_plural = _("تصنيفات المصروفات")

    def __str__(self):
        return self.name_ar


class Expense(TimeStampedModel):
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses",
    )
    category = models.ForeignKey(
        ExpenseCategory,
        verbose_name=_("التصنيف"),
        on_delete=models.PROTECT,
    )
    expense_date = models.DateField(_("تاريخ المصروف"))
    amount = models.DecimalField(_("المبلغ"), max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    payment_method = models.CharField(_("طريقة الدفع"), max_length=32)
    notes = models.TextField(_("ملاحظات"), blank=True)

    class Meta:
        verbose_name = _("مصروف")
        verbose_name_plural = _("المصروفات")
        ordering = ("-expense_date", "-created_at")

    def __str__(self):
        return f"{self.category_id} {self.amount}"

    @property
    def payment_method_label_ar(self) -> str:
        """عرض عربي لرمز طريقة الدفع (الحقل بدون choices في النموذج)."""
        from apps.core.payment_methods import get_payment_method_choices

        code = (self.payment_method or "").strip()
        for c, label in get_payment_method_choices():
            if c == code:
                return label
        return code or "—"
