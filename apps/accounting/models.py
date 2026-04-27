"""
دليل الحسابات + القيود اليومية (قيد مزدوج مبسّط).
كل عملية مالية تُسجَّل كقيد يومي متوازن: مجموع المدين = مجموع الدائن.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel, WorkSession


class Account(TimeStampedModel):
    """حساب في دليل الحسابات."""

    class AccountType(models.TextChoices):
        ASSET = "asset", _("أصول")
        LIABILITY = "liability", _("خصوم")
        EQUITY = "equity", _("حقوق ملكية")
        REVENUE = "revenue", _("إيرادات")
        EXPENSE = "expense", _("مصروفات")

    code = models.CharField(_("رمز الحساب"), max_length=16, unique=True, db_index=True)
    name_ar = models.CharField(_("الاسم"), max_length=200)
    name_en = models.CharField(_("الاسم (إنجليزي)"), max_length=200, blank=True)
    account_type = models.CharField(_("نوع الحساب"), max_length=16, choices=AccountType.choices)
    parent = models.ForeignKey(
        "self",
        verbose_name=_("الحساب الأب"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    system_code = models.CharField(
        _("رمز النظام"),
        max_length=40,
        unique=True,
        null=True,
        blank=True,
        help_text=_("للربط التلقائي: CASH, BANK, AR, INVENTORY..."),
    )
    is_active = models.BooleanField(_("نشط"), default=True)

    class Meta:
        verbose_name = _("حساب")
        verbose_name_plural = _("دليل الحسابات")
        ordering = ("code",)

    def __str__(self):
        return f"{self.code} — {self.name_ar}"

    @property
    def computed_balance(self) -> Decimal:
        """الرصيد المحسوب من القيود. المدين (+) للأصول والمصروفات، الدائن (+) للخصوم والإيرادات والملكية."""
        from django.db.models import Sum

        agg = self.journal_lines.aggregate(d=Sum("debit"), c=Sum("credit"))
        total_d = agg["d"] or Decimal("0")
        total_c = agg["c"] or Decimal("0")
        if self.account_type in (self.AccountType.ASSET, self.AccountType.EXPENSE):
            return (total_d - total_c).quantize(Decimal("0.01"))
        return (total_c - total_d).quantize(Decimal("0.01"))


class JournalEntry(TimeStampedModel):
    """قيد يومي — مجموعة أسطر متوازنة."""

    entry_number = models.CharField(_("رقم القيد"), max_length=32, unique=True)
    date = models.DateField(_("التاريخ"))
    description = models.TextField(_("الوصف"), blank=True)
    reference_type = models.CharField(
        _("نوع المرجع"),
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("مثل: billing.SaleInvoice, purchasing.PurchaseInvoice"),
    )
    reference_pk = models.CharField(_("معرف المرجع"), max_length=64, blank=True, db_index=True)
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("المستخدم"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    is_reversed = models.BooleanField(_("معكوس"), default=False)
    reversed_by = models.ForeignKey(
        "self",
        verbose_name=_("عُكس بواسطة"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reverses",
    )

    class Meta:
        verbose_name = _("قيد يومي")
        verbose_name_plural = _("القيود اليومية")
        ordering = ("-date", "-created_at")

    def __str__(self):
        return self.entry_number

    @property
    def is_balanced(self) -> bool:
        from django.db.models import Sum

        agg = self.lines.aggregate(d=Sum("debit"), c=Sum("credit"))
        d = agg["d"] or Decimal("0")
        c = agg["c"] or Decimal("0")
        return abs(d - c) < Decimal("0.005")


class JournalLine(TimeStampedModel):
    """سطر قيد — مدين أو دائن."""

    entry = models.ForeignKey(
        JournalEntry,
        verbose_name=_("القيد"),
        related_name="lines",
        on_delete=models.CASCADE,
    )
    account = models.ForeignKey(
        Account,
        verbose_name=_("الحساب"),
        related_name="journal_lines",
        on_delete=models.PROTECT,
    )
    debit = models.DecimalField(
        _("مدين"),
        max_digits=14,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    credit = models.DecimalField(
        _("دائن"),
        max_digits=14,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    description = models.CharField(_("بيان السطر"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("سطر قيد")
        verbose_name_plural = _("أسطر القيود")

    def __str__(self):
        side = "مدين" if self.debit > 0 else "دائن"
        amt = self.debit if self.debit > 0 else self.credit
        return f"{self.account.code} {side} {amt}"
