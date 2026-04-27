from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.core.models import SoftDeleteModel, TimeStampedModel, WorkSession


class DiningTable(SoftDeleteModel):
    name_ar = models.CharField(_("اسم الطاولة"), max_length=80)
    name_en = models.CharField(_("اسم الطاولة (إنجليزي)"), max_length=80, blank=True)
    sort_order = models.PositiveIntegerField(_("الترتيب"), default=0)
    is_active = models.BooleanField(_("نشط"), default=True)

    class Meta:
        verbose_name = _("طاولة")
        verbose_name_plural = _("الطاولات")
        ordering = ("sort_order", "name_ar")

    def __str__(self):
        return self.name_ar

    def display(self, lang: str = "ar") -> str:
        if lang == "en" and self.name_en:
            return self.name_en
        return self.name_ar


class TableSession(TimeStampedModel):
    """جلسة طاولة مفتوحة حتى التسوية — تجميع طلبات وتأجيل الدفع."""

    class Status(models.TextChoices):
        OPEN = "open", _("مفتوحة")
        CLOSED = "closed", _("مغلقة")
        MERGED = "merged", _("مدموجة")

    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        on_delete=models.PROTECT,
        related_name="table_sessions",
    )
    dining_table = models.ForeignKey(
        DiningTable,
        verbose_name=_("الطاولة"),
        on_delete=models.PROTECT,
        related_name="table_sessions",
    )
    customer = models.ForeignKey(
        "contacts.Customer",
        verbose_name=_("عميل"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    guest_label = models.CharField(_("اسم ضيف / بدون سجل"), max_length=160, blank=True)
    status = models.CharField(_("الحالة"), max_length=16, choices=Status.choices, default=Status.OPEN)
    closed_at = models.DateTimeField(_("تاريخ الإغلاق"), null=True, blank=True)
    merged_into = models.ForeignKey(
        "self",
        verbose_name=_("دمجت في"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_from",
    )

    class Meta:
        verbose_name = _("جلسة طاولة")
        verbose_name_plural = _("جلسات الطاولات")
        ordering = ("-created_at",)

    def __str__(self):
        return f"TableSession #{self.pk} {self.dining_table.name_ar}"


class Order(SoftDeleteModel):
    class OrderType(models.TextChoices):
        DINE_IN = "dine_in", _("صالة")
        TAKEAWAY = "takeaway", _("سفري")
        DELIVERY = "delivery", _("توصيل")

    class Status(models.TextChoices):
        OPEN = "open", _("مفتوح")
        CHECKED_OUT = "checked_out", _("مكتمل الدفع")
        CANCELLED = "cancelled", _("ملغى")

    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        on_delete=models.PROTECT,
        related_name="orders",
    )
    table_session = models.ForeignKey(
        TableSession,
        verbose_name=_("جلسة الطاولة"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    order_type = models.CharField(_("نوع الطلب"), max_length=16, choices=OrderType.choices)
    table = models.ForeignKey(
        DiningTable,
        verbose_name=_("الطاولة"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    customer = models.ForeignKey(
        "contacts.Customer",
        verbose_name=_("العميل (ائتمان)"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(_("الحالة"), max_length=20, choices=Status.choices, default=Status.OPEN)
    is_held = models.BooleanField(_("معلّق (Hold)"), default=False)
    order_note = models.TextField(_("ملاحظات الطلب"), blank=True)
    discount_amount = models.DecimalField(_("خصم مبلغ"), max_digits=14, decimal_places=2, default=0)
    discount_percent = models.DecimalField(_("خصم %"), max_digits=6, decimal_places=2, default=0)
    tax_percent_override = models.DecimalField(
        _("ضريبة % (فارغ = الافتراضي)"),
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    service_charge_percent_override = models.DecimalField(
        _("خدمة % (فارغ = الافتراضي)"),
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    kitchen_batch_no = models.PositiveIntegerField(_("دفعة مطبخ حالية"), default=1)

    class Meta:
        verbose_name = _("طلب")
        verbose_name_plural = _("الطلبات")
        ordering = ("-created_at",)

    def __str__(self):
        return f"Order #{self.pk} ({self.get_order_type_display()})"


class OrderLine(TimeStampedModel):
    order = models.ForeignKey(Order, verbose_name=_("الطلب"), related_name="lines", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, verbose_name=_("المنتج"), on_delete=models.PROTECT)
    quantity = models.DecimalField(_("الكمية"), max_digits=14, decimal_places=3, validators=[MinValueValidator(0)])
    unit_price = models.DecimalField(_("سعر الوحدة الأساسي"), max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    extra_unit_price = models.DecimalField(
        _("إضافة سعر من المعدّلات"),
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    line_note = models.CharField(_("ملاحظة السطر"), max_length=255, blank=True)
    modifiers_json = models.JSONField(_("المعدّلات"), default=list, blank=True)
    kitchen_batch_no = models.PositiveIntegerField(_("دفعة مطبخ"), default=1)

    class Meta:
        verbose_name = _("سطر طلب")
        verbose_name_plural = _("أسطر الطلبات")

    def __str__(self):
        return f"{self.order_id} x {self.product_id}"

    @property
    def line_total(self):
        from decimal import Decimal

        unit = self.unit_price + self.extra_unit_price
        return (self.quantity * unit).quantize(Decimal("0.01"))
