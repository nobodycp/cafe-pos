from django import forms

from apps.pos.models import DiningTable


class DiningTableForm(forms.ModelForm):
    class Meta:
        model = DiningTable
        fields = ["name_ar", "name_en", "sort_order", "is_active"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input"}),
            "name_en": forms.TextInput(attrs={"class": "form-input"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check"}),
        }
