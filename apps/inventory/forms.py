from django import forms

from apps.catalog.models import Product, Unit


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
