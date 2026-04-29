from django import forms

from apps.core.payment_methods import get_payment_method_choices
from apps.purchasing.models import Supplier


class SupplierForm(forms.ModelForm):
    opening_balance = forms.DecimalField(
        required=False,
        initial=0,
        label="رصيد افتتاحي (مستحق للمورد)",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01", "placeholder": "0.00"}),
    )
    also_customer = forms.BooleanField(
        required=False,
        initial=False,
        label="إنشاء حساب عميل مرتبط",
        help_text="المورد يشتري منا أيضاً (يظهر في قائمة العملاء)",
        widget=forms.CheckboxInput(attrs={"class": "form-check"}),
    )

    class Meta:
        model = Supplier
        fields = ["name_ar", "name_en", "phone", "email"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "الاسم بالعربي"}),
            "name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Name in English"}),
            "phone": forms.TextInput(attrs={"class": "form-input", "placeholder": "05xxxxxxxx"}),
            "email": forms.EmailInput(attrs={"class": "form-input", "placeholder": "email@example.com"}),
        }


class SupplierPaymentForm(forms.Form):
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
