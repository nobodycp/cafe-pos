from decimal import Decimal

from django import forms

from apps.contacts.models import Customer
from apps.core.forms import FlexibleDecimalField
from apps.core.payment_methods import get_payment_method_choices


class CustomerForm(forms.ModelForm):
    opening_balance = FlexibleDecimalField(
        required=False,
        label="رصيد افتتاحي",
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
        help_text="موجب = مدين للمحل، سالب = دائن (له على المحل). يُخزَّن كقيد تسوية «رصيد افتتاحي» ويُحدَّث رصيد الحساب من الدفتر.",
    )

    class Meta:
        model = Customer
        fields = ["name_ar", "name_en", "phone", "is_active"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "الاسم بالعربي"}),
            "name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Name in English"}),
            "phone": forms.TextInput(attrs={"class": "form-input", "placeholder": "05xxxxxxxx"}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-gray-300 text-primary"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            from apps.contacts.services import get_customer_opening_balance_sum

            self.fields["opening_balance"].initial = get_customer_opening_balance_sum(self.instance)
        else:
            self.fields["opening_balance"].initial = Decimal("0")


class CustomerPaymentForm(forms.Form):
    amount = forms.DecimalField(
        min_value=0.01,
        label="المبلغ",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )
    method = forms.ChoiceField(
        choices=[],
        label="طريقة الدفع",
        widget=forms.Select(attrs={"class": "form-input"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["method"].choices = get_payment_method_choices()
