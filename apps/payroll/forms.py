from django import forms

from apps.payroll.models import Employee


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["name_ar", "name_en", "daily_wage", "is_active"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "الاسم بالعربي"}),
            "name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Name in English"}),
            "daily_wage": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check"}),
        }


class EmployeeCreateForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["name_ar", "name_en", "daily_wage"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "الاسم بالعربي"}),
            "name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Name in English"}),
            "daily_wage": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
        }


class EmployeeAdvanceForm(forms.Form):
    amount = forms.DecimalField(
        min_value=0.01,
        label="مبلغ السلفة",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )


class EmployeePayoutForm(forms.Form):
    days_count = forms.DecimalField(
        min_value=0.01,
        label="عدد الأيام",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.5"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )


class EmployeeWorkDaysForm(forms.Form):
    days_count = forms.DecimalField(
        min_value=0.5,
        label="عدد الأيام",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.5"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )


class EmployeeCafePurchaseForm(forms.Form):
    amount = forms.DecimalField(
        min_value=0.01,
        label="المبلغ",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )
