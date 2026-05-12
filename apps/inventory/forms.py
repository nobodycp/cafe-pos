from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product, Unit


class ManualStockMovementForm(forms.Form):
    KIND_PURCHASE = "purchase"
    KIND_ADJUSTMENT = "adjustment"
    KIND_WASTE = "waste"

    product = forms.ModelChoiceField(
        label=_("المنتج"),
        queryset=Product.objects.none(),
    )
    kind = forms.ChoiceField(
        label=_("نوع الحركة"),
        choices=[
            (KIND_PURCHASE, _("إضافة شراء (كمية وتكلفة)")),
            (KIND_ADJUSTMENT, _("تسوية (كمية موجبة أو سالبة)")),
            (KIND_WASTE, _("هالك / تلف (كمية موجبة)")),
        ],
    )
    quantity = forms.DecimalField(label=_("الكمية"), max_digits=18, decimal_places=4)
    unit_cost = forms.DecimalField(
        label=_("تكلفة الوحدة"),
        max_digits=18,
        decimal_places=6,
        required=False,
        initial=Decimal("0"),
    )
    note = forms.CharField(label=_("ملاحظة"), required=False, widget=forms.TextInput(attrs={"class": "ds-input w-full"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = (
            Product.objects.filter(is_active=True, is_stock_tracked=True).select_related("unit").order_by("name_ar")
        )
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "ds-input w-full")

    def clean(self):
        cleaned = super().clean()
        if not cleaned:
            return cleaned
        kind = cleaned.get("kind")
        q = cleaned.get("quantity")
        if q is None:
            return cleaned
        if kind == self.KIND_ADJUSTMENT:
            if q == 0:
                raise forms.ValidationError(_("كمية التسوية يجب أن تكون غير صفرية."))
        elif kind in (self.KIND_PURCHASE, self.KIND_WASTE):
            if q <= 0:
                raise forms.ValidationError(_("الكمية يجب أن تكون أكبر من صفر."))
        return cleaned


class RawMaterialForm(forms.ModelForm):
    """Simplified form for raw materials — no selling price, no category."""

    class Meta:
        model = Product
        fields = ["name_ar", "name_en", "unit", "min_stock_level"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = Unit.objects.all()
        self.fields["unit"].required = True
        self.fields["name_en"].required = False
        self.fields["min_stock_level"].required = False
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-input")

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.product_type = Product.ProductType.RAW
        instance.is_stock_tracked = True
        instance.selling_price = 0
        instance.is_active = True
        if commit:
            instance.save()
        return instance
