from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel, WorkSession


class Employee(TimeStampedModel):
    name_ar = models.CharField(_("الاسم"), max_length=200)
    name_en = models.CharField(_("الاسم (إنجليزي)"), max_length=200, blank=True)
    daily_wage = models.DecimalField(_("أجر اليوم"), max_digits=12, decimal_places=2, default=0)
    work_days_balance = models.DecimalField(_("أيام مستحقة / رصيد"), max_digits=10, decimal_places=2, default=0)
    advance_balance = models.DecimalField(_("سلف معلقة"), max_digits=14, decimal_places=2, default=0)
    store_purchases_balance = models.DecimalField(_("مشتريات من المقهى"), max_digits=14, decimal_places=2, default=0)
    net_balance = models.DecimalField(_("صافي الرصيد للموظف"), max_digits=14, decimal_places=2, default=0)
    is_active = models.BooleanField(_("نشط"), default=True)

    class Meta:
        verbose_name = _("موظف")
        verbose_name_plural = _("الموظفون")
        ordering = ("name_ar",)

    def __str__(self):
        return self.name_ar


class EmployeeAdvance(TimeStampedModel):
    employee = models.ForeignKey(
        Employee,
        verbose_name=_("الموظف"),
        related_name="advances",
        on_delete=models.CASCADE,
    )
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    amount = models.DecimalField(_("مبلغ السلفة"), max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    note = models.TextField(_("ملاحظة"), blank=True)

    class Meta:
        verbose_name = _("سلفة")
        verbose_name_plural = _("السلف")


class EmployeeSalaryPayout(TimeStampedModel):
    employee = models.ForeignKey(
        Employee,
        verbose_name=_("الموظف"),
        related_name="salary_payouts",
        on_delete=models.CASCADE,
    )
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    days_count = models.DecimalField(_("عدد الأيام"), max_digits=8, decimal_places=2, validators=[MinValueValidator(0)])
    amount = models.DecimalField(_("المبلغ المدفوع"), max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    method = models.CharField(_("طريقة الدفع"), max_length=16, default="cash")
    note = models.TextField(_("ملاحظة"), blank=True)

    class Meta:
        verbose_name = _("صرف راتب")
        verbose_name_plural = _("صرف الرواتب")


class EmployeeCafePurchase(TimeStampedModel):
    employee = models.ForeignKey(
        Employee,
        verbose_name=_("الموظف"),
        related_name="cafe_purchases",
        on_delete=models.CASCADE,
    )
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    amount = models.DecimalField(_("المبلغ"), max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    note = models.TextField(_("ملاحظة"), blank=True)
    sale_invoice = models.ForeignKey(
        "billing.SaleInvoice",
        verbose_name=_("فاتورة مرتبطة"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        verbose_name = _("شراء موظف من المقهى")
        verbose_name_plural = _("مشتريات الموظفين")
