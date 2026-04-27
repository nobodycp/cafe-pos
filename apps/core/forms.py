from django import forms

from apps.core.models import PosSettings


class CafeInfoForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "cafe_name_ar",
            "cafe_name_en",
            "cafe_phone",
            "cafe_address",
            "cafe_tax_number",
        ]
        widgets = {
            "cafe_name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "مقهى ..."}),
            "cafe_name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Café ..."}),
            "cafe_phone": forms.TextInput(attrs={"class": "form-input", "placeholder": "05xxxxxxxx", "dir": "ltr"}),
            "cafe_address": forms.Textarea(attrs={"class": "form-input", "rows": 2}),
            "cafe_tax_number": forms.TextInput(attrs={"class": "form-input", "dir": "ltr"}),
        }


class CurrencyForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "currency_symbol",
            "currency_code",
            "decimal_places",
        ]
        widgets = {
            "currency_symbol": forms.TextInput(attrs={"class": "form-input", "style": "max-width:120px"}),
            "currency_code": forms.TextInput(attrs={"class": "form-input", "style": "max-width:120px", "dir": "ltr"}),
            "decimal_places": forms.NumberInput(attrs={"class": "form-input", "style": "max-width:120px", "min": 0, "max": 4}),
        }


class TaxServiceForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "default_tax_percent",
            "default_service_charge_percent",
            "tax_included_in_price",
        ]
        widgets = {
            "default_tax_percent": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
            "default_service_charge_percent": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
            "tax_included_in_price": forms.CheckboxInput(attrs={"class": "form-check"}),
        }


class OrderSettingsForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "default_order_type",
            "allow_negative_stock",
            "require_customer_for_credit",
        ]
        widgets = {
            "default_order_type": forms.Select(attrs={"class": "form-input"}),
            "allow_negative_stock": forms.CheckboxInput(attrs={"class": "form-check"}),
            "require_customer_for_credit": forms.CheckboxInput(attrs={"class": "form-check"}),
        }


class PrinterForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "kitchen_auto_print",
            "printer_kitchen_label",
            "printer_receipt_label",
        ]
        widgets = {
            "kitchen_auto_print": forms.CheckboxInput(attrs={"class": "form-check"}),
            "printer_kitchen_label": forms.TextInput(attrs={"class": "form-input"}),
            "printer_receipt_label": forms.TextInput(attrs={"class": "form-input"}),
        }


class ReceiptForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "receipt_header",
            "receipt_footer",
            "receipt_show_tax_number",
        ]
        widgets = {
            "receipt_header": forms.Textarea(attrs={"class": "form-input", "rows": 3, "placeholder": "نص يظهر أعلى الإيصال"}),
            "receipt_footer": forms.Textarea(attrs={"class": "form-input", "rows": 3, "placeholder": "شكراً لزيارتكم ..."}),
            "receipt_show_tax_number": forms.CheckboxInput(attrs={"class": "form-check"}),
        }
