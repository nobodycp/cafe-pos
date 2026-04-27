from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.core.models import TimeStampedModel, WorkSession


class StockBalance(TimeStampedModel):
    product = models.OneToOneField(
        Product,
        verbose_name=_("المنتج"),
        related_name="stock_balance",
        on_delete=models.CASCADE,
    )
    quantity_on_hand = models.DecimalField(_("الكمية الحالية"), max_digits=18, decimal_places=4, default=0)
    average_cost = models.DecimalField(_("متوسط التكلفة"), max_digits=18, decimal_places=6, default=0)

    class Meta:
        verbose_name = _("رصيد مخزون")
        verbose_name_plural = _("أرصدة المخزون")

    def __str__(self):
        return f"{self.product_id}: {self.quantity_on_hand}"


class StockMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        PURCHASE = "purchase", _("شراء")
        SALE = "sale", _("بيع")
        MANUFACTURING = "manufacturing", _("استهلاك تصنيع")
        ADJUSTMENT = "adjustment", _("تسوية يدوية")
        WASTE = "waste", _("هالك / تلف")

    product = models.ForeignKey(
        Product,
        verbose_name=_("المنتج"),
        related_name="stock_movements",
        on_delete=models.PROTECT,
    )
    movement_type = models.CharField(_("نوع الحركة"), max_length=20, choices=MovementType.choices)
    quantity_delta = models.DecimalField(_("التغيير (+داخل / -خارج)"), max_digits=18, decimal_places=4)
    unit_cost = models.DecimalField(_("تكلفة الوحدة"), max_digits=18, decimal_places=6, null=True, blank=True)
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reference_model = models.CharField(_("مرجع (نموذج)"), max_length=128, blank=True)
    reference_pk = models.CharField(_("مرجع (معرف)"), max_length=64, blank=True)
    note = models.TextField(_("ملاحظة"), blank=True)

    class Meta:
        verbose_name = _("حركة مخزون")
        verbose_name_plural = _("حركات المخزون")
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.product_id} {self.quantity_delta}"


class StockTake(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("مسودة")
        APPROVED = "approved", _("معتمد")

    work_session = models.ForeignKey(
        WorkSession,
        verbose_name=_("الوردية"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(_("الحالة"), max_length=16, choices=Status.choices, default=Status.DRAFT)
    approved_at = models.DateTimeField(_("تاريخ الاعتماد"), null=True, blank=True)
    note = models.TextField(_("ملاحظات"), blank=True)

    class Meta:
        verbose_name = _("جرد")
        verbose_name_plural = _("عمليات الجرد")
        ordering = ("-created_at",)


class StockTakeLine(TimeStampedModel):
    stock_take = models.ForeignKey(
        StockTake,
        verbose_name=_("الجرد"),
        related_name="lines",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        Product,
        verbose_name=_("المنتج"),
        on_delete=models.PROTECT,
    )
    system_quantity = models.DecimalField(_("كمية النظام"), max_digits=18, decimal_places=4, default=0)
    actual_quantity = models.DecimalField(_("الكمية الفعلية"), max_digits=18, decimal_places=4, null=True, blank=True)
    difference = models.DecimalField(_("الفرق"), max_digits=18, decimal_places=4, default=0)

    class Meta:
        verbose_name = _("سطر جرد")
        verbose_name_plural = _("أسطر الجرد")
