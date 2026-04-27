from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.contacts.models import Customer
from apps.core.models import SoftDeleteModel, TimeStampedModel, WorkSession


class SaleInvoice(SoftDeleteModel):
    class PaymentStatus(models.TextChoices):
        PAID = "paid", _("مدفوع بالكامل")
        PARTIAL = "partial", _("جزئي")
        UNPAID = "unpaid", _("غير مدفوع")

    invoice_number = models.CharField(_("رقم الفاتورة"), max_length=32, unique=True)
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        on_delete=models.PROTECT,
        related_name="sale_invoices",
    )
    order = models.OneToOneField(
        "pos.Order",
        verbose_name=_("الطلب"),
        on_delete=models.PROTECT,
        related_name="sale_invoice",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name=_("عميل ائتمان"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    supplier_buyer = models.ForeignKey(
        "purchasing.Supplier",
        verbose_name=_("مورد مشتري من المقهى"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=_("اختياري: بيع لمورد"),
    )
    subtotal = models.DecimalField(_("المجموع قبل الخصم"), max_digits=14, decimal_places=2, default=0)
    discount_total = models.DecimalField(_("إجمالي الخصم"), max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(_("الإجمالي"), max_digits=14, decimal_places=2, default=0)
    total_cost = models.DecimalField(_("إجمالي التكلفة"), max_digits=14, decimal_places=2, default=0)
    total_profit = models.DecimalField(_("إجمالي الربح المعترف"), max_digits=14, decimal_places=2, default=0)
    service_charge_total = models.DecimalField(_("إجمالي خدمة"), max_digits=14, decimal_places=2, default=0)
    tax_total = models.DecimalField(_("إجمالي ضريبة"), max_digits=14, decimal_places=2, default=0)
    payment_status = models.CharField(
        _("حالة السداد"),
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PAID,
    )

    class Meta:
        verbose_name = _("فاتورة بيع")
        verbose_name_plural = _("فواتير البيع")
        ordering = ("-created_at",)

    def __str__(self):
        return self.invoice_number


class SaleInvoiceLine(TimeStampedModel):
    invoice = models.ForeignKey(
        SaleInvoice,
        verbose_name=_("الفاتورة"),
        related_name="lines",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(Product, verbose_name=_("المنتج"), on_delete=models.PROTECT)
    quantity = models.DecimalField(_("الكمية"), max_digits=14, decimal_places=3, validators=[MinValueValidator(0)])
    unit_price = models.DecimalField(_("سعر البيع"), max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    line_subtotal = models.DecimalField(_("المجموع"), max_digits=14, decimal_places=2, default=0)
    unit_cost_snapshot = models.DecimalField(_("تكلفة الوحدة وقت البيع"), max_digits=18, decimal_places=6, default=0)
    line_cost_total = models.DecimalField(_("إجمالي التكلفة"), max_digits=14, decimal_places=2, default=0)
    recognized_revenue = models.DecimalField(_("الإيراد المعترف"), max_digits=14, decimal_places=2, default=0)
    line_profit = models.DecimalField(_("الربح المعترف"), max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = _("سطر فاتورة")
        verbose_name_plural = _("أسطر الفواتير")


class InvoicePayment(TimeStampedModel):
    class Method(models.TextChoices):
        CASH = "cash", _("كاش")
        BANK = "bank", _("تطبيق")
        CREDIT = "credit", _("آجل")

    invoice = models.ForeignKey(
        SaleInvoice,
        verbose_name=_("الفاتورة"),
        related_name="payments",
        on_delete=models.CASCADE,
    )
    method = models.CharField(_("طريقة الدفع"), max_length=16, choices=Method.choices)
    amount = models.DecimalField(_("المبلغ"), max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = _("دفعة فاتورة")
        verbose_name_plural = _("دفعات الفواتير")


class OrderPayment(TimeStampedModel):
    """دفعات على الطلب قبل إصدار الفاتورة (تاب / طاولة)."""

    class Method(models.TextChoices):
        CASH = "cash", _("كاش")
        BANK = "bank", _("تطبيق")
        CREDIT = "credit", _("آجل")

    order = models.ForeignKey(
        "pos.Order",
        verbose_name=_("الطلب"),
        related_name="tab_payments",
        on_delete=models.CASCADE,
    )
    method = models.CharField(_("طريقة الدفع"), max_length=16, choices=Method.choices)
    amount = models.DecimalField(_("المبلغ"), max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    note = models.CharField(_("ملاحظة"), max_length=255, blank=True)
    sale_invoice = models.ForeignKey(
        SaleInvoice,
        verbose_name=_("فاتورة التسوية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_tab_payments",
    )

    class Meta:
        verbose_name = _("دفعة على طلب")
        verbose_name_plural = _("دفعات الطلب (تاب)")


class SaleReturn(TimeStampedModel):
    invoice = models.ForeignKey(
        SaleInvoice,
        verbose_name=_("الفاتورة الأصلية"),
        related_name="returns",
        on_delete=models.PROTECT,
    )
    return_number = models.CharField(_("رقم المرتجع"), max_length=32, unique=True)
    reason = models.TextField(_("السبب"), blank=True)
    total_refund = models.DecimalField(_("إجمالي المرتجع"), max_digits=14, decimal_places=2, default=0)
    refund_method = models.CharField(
        _("طريقة الاسترداد"),
        max_length=16,
        choices=[("cash", _("نقدي")), ("credit", _("رصيد عميل"))],
        default="cash",
    )

    class Meta:
        verbose_name = _("مرتجع بيع")
        verbose_name_plural = _("مرتجعات البيع")
        ordering = ("-created_at",)

    def __str__(self):
        return self.return_number


class SaleReturnLine(TimeStampedModel):
    sale_return = models.ForeignKey(
        SaleReturn,
        verbose_name=_("المرتجع"),
        related_name="lines",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        "catalog.Product",
        verbose_name=_("المنتج"),
        on_delete=models.PROTECT,
    )
    quantity = models.DecimalField(_("الكمية المرتجعة"), max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(_("سعر الوحدة"), max_digits=14, decimal_places=2)
    line_total = models.DecimalField(_("المجموع"), max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = _("سطر مرتجع")
        verbose_name_plural = _("أسطر المرتجع")
