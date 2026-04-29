from django import forms

from apps.contacts.models import Customer
from apps.core.payment_methods import get_payment_method_choices


class CustomerForm(forms.ModelForm):
    opening_balance = forms.DecimalField(
        min_value=0,
        required=False,
        initial=0,
        label="رصيد افتتاحي",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
        help_text="رصيد مسبق على العميل (اختياري — يظهر فقط عند الإنشاء)",
    )

    class Meta:
        model = Customer
        fields = ["name_ar", "name_en", "phone"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "الاسم بالعربي"}),
            "name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Name in English"}),
            "phone": forms.TextInput(attrs={"class": "form-input", "placeholder": "05xxxxxxxx"}),
        }


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
