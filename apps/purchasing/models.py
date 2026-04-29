from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.core.models import SoftDeleteModel, TimeStampedModel, WorkSession


class Supplier(TimeStampedModel):
    name_ar = models.CharField(_("الاسم"), max_length=200)
    name_en = models.CharField(_("الاسم (إنجليزي)"), max_length=200, blank=True)
    phone = models.CharField(_("الهاتف"), max_length=32, blank=True)
    email = models.EmailField(_("البريد"), blank=True)
    balance = models.DecimalField(_("الرصيد (لنا / علينا)"), max_digits=14, decimal_places=2, default=0)
    is_active = models.BooleanField(_("نشط"), default=True)
    linked_customer = models.OneToOneField(
        "contacts.Customer",
        verbose_name=_("حساب العميل المرتبط"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linked_supplier",
        help_text=_("إذا كان المورد يشتري منا أيضاً"),
    )

    class Meta:
        verbose_name = _("مورد")
        verbose_name_plural = _("الموردون")
        ordering = ("name_ar",)

    def __str__(self):
        return self.name_ar

    @property
    def computed_balance(self) -> Decimal:
        """الرصيد المحسوب من دفتر المورد (مجموع القيود)."""
        agg = self.ledger_entries.aggregate(s=Sum("amount"))
        return (agg["s"] or Decimal("0")).quantize(Decimal("0.01"))


class SupplierLedgerEntry(TimeStampedModel):
    class EntryType(models.TextChoices):
        PURCHASE = "purchase", _("مشتريات")
        PAYMENT = "payment", _("سداد للمورد")
        ADJUSTMENT = "adjustment", _("تسوية")

    supplier = models.ForeignKey(
        Supplier,
        verbose_name=_("المورد"),
        related_name="ledger_entries",
        on_delete=models.PROTECT,
    )
    entry_type = models.CharField(_("نوع القيد"), max_length=20, choices=EntryType.choices)
    amount = models.DecimalField(
        _("المبلغ (+ علينا للمورد / - سداد)"),
        max_digits=14,
        decimal_places=2,
    )
    note = models.TextField(_("ملاحظة"), blank=True)
    reference_model = models.CharField(_("مرجع"), max_length=128, blank=True)
    reference_pk = models.CharField(_("معرف المرجع"), max_length=64, blank=True)

    class Meta:
        verbose_name = _("قيد مورد")
        verbose_name_plural = _("دفتر الموردين")
        ordering = ("-created_at",)


class PurchaseInvoice(SoftDeleteModel):
    class PaymentStatus(models.TextChoices):
        PAID = "paid", _("مدفوع")
        PARTIAL = "partial", _("جزئي")
        UNPAID = "unpaid", _("آجل")

    invoice_number = models.CharField(_("رقم فاتورة الشراء"), max_length=32, unique=True)
    supplier = models.ForeignKey(
        Supplier,
        verbose_name=_("المورد"),
        on_delete=models.PROTECT,
        related_name="purchase_invoices",
    )
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="purchase_invoices",
    )
    total = models.DecimalField(_("الإجمالي"), max_digits=14, decimal_places=2, default=0)
    payment_status = models.CharField(
        _("حالة السداد"),
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PAID,
    )

    class Meta:
        verbose_name = _("فاتورة شراء")
        verbose_name_plural = _("فواتير الشراء")
        ordering = ("-created_at",)


class PurchaseLine(TimeStampedModel):
    purchase = models.ForeignKey(
        PurchaseInvoice,
        verbose_name=_("الفاتورة"),
        related_name="lines",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(Product, verbose_name=_("المنتج"), on_delete=models.PROTECT)
    quantity = models.DecimalField(_("الكمية"), max_digits=14, decimal_places=4, validators=[MinValueValidator(0)])
    unit_cost = models.DecimalField(_("تكلفة الوحدة"), max_digits=18, decimal_places=6, validators=[MinValueValidator(0)])
    line_total = models.DecimalField(_("المجموع"), max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = _("سطر شراء")
        verbose_name_plural = _("أسطر الشراء")


class SupplierPayment(TimeStampedModel):
    supplier = models.ForeignKey(
        Supplier,
        verbose_name=_("المورد"),
        related_name="payments",
        on_delete=models.PROTECT,
    )
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    amount = models.DecimalField(_("المبلغ"), max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    method = models.CharField(_("طريقة الدفع"), max_length=32)
    note = models.TextField(_("ملاحظة"), blank=True)

    class Meta:
        verbose_name = _("سداد مورد")
        verbose_name_plural = _("سدادات الموردين")


class PurchaseReturn(TimeStampedModel):
    purchase_invoice = models.ForeignKey(
        PurchaseInvoice,
        verbose_name=_("فاتورة الشراء"),
        related_name="returns",
        on_delete=models.PROTECT,
    )
    return_number = models.CharField(_("رقم المرتجع"), max_length=32, unique=True)
    reason = models.TextField(_("السبب"), blank=True)
    total = models.DecimalField(_("الإجمالي"), max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = _("مرتجع مشتريات")
        verbose_name_plural = _("مرتجعات المشتريات")
        ordering = ("-created_at",)


class PurchaseReturnLine(TimeStampedModel):
    purchase_return = models.ForeignKey(
        PurchaseReturn,
        verbose_name=_("المرتجع"),
        related_name="lines",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        Product,
        verbose_name=_("المنتج"),
        on_delete=models.PROTECT,
    )
    quantity = models.DecimalField(_("الكمية المرتجعة"), max_digits=14, decimal_places=4)
    unit_cost = models.DecimalField(_("تكلفة الوحدة"), max_digits=18, decimal_places=6)
    line_total = models.DecimalField(_("المجموع"), max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = _("سطر مرتجع مشتريات")
        verbose_name_plural = _("أسطر مرتجع المشتريات")
