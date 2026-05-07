import json
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone as django_timezone


def _normalize_user_decimal_string(value: str) -> str:
    """Accept comma as decimal separator and strip grouping (common Arabic/EU input)."""
    s = (value or "").strip().replace("\u00a0", " ").replace(" ", "")
    if not s:
        return s
    if "," in s and "." not in s:
        return s.replace(",", ".")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            return s.replace(".", "").replace(",", ".")
        return s.replace(",", "")
    return s


class FlexibleDecimalField(forms.DecimalField):
    """Like DecimalField but tolerates `,` as decimal separator before validation."""

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, str):
            value = _normalize_user_decimal_string(value)
        return super().to_python(value)

from apps.contacts.models import Customer
from apps.core.models import PaymentMethod, PosSettings
from apps.core.payment_methods import (
    get_payment_method_codes,
    method_codes_requiring_payer_details,
    resolve_ledger_account_code,
)
from apps.payroll.models import Employee
from apps.purchasing.models import Supplier


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
            "receipt_logo_url",
            "receipt_slogan_ar",
            "receipt_stamp_text",
            "receipt_show_tax_number",
            "allow_sale_invoice_edit",
        ]
        widgets = {
            "receipt_header": forms.Textarea(attrs={"class": "form-input", "rows": 3, "placeholder": "نص يظهر أعلى الإيصال"}),
            "receipt_footer": forms.Textarea(attrs={"class": "form-input", "rows": 3, "placeholder": "شكراً لزيارتكم ..."}),
            "receipt_logo_url": forms.TextInput(
                attrs={"class": "form-input", "dir": "ltr", "placeholder": "https://… أو /static/pos/logo.png"}
            ),
            "receipt_slogan_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "جودة وعروض على طول"}),
            "receipt_stamp_text": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "سطر1; سطر2; سطر3"}
            ),
            "receipt_show_tax_number": forms.CheckboxInput(attrs={"class": "form-check"}),
            "allow_sale_invoice_edit": forms.CheckboxInput(attrs={"class": "form-check"}),
        }


class PaymentMethodForm(forms.ModelForm):
    """إضافة/تعديل طريقة دفع من شاشة الإعدادات."""

    class Meta:
        model = PaymentMethod
        fields = ["code", "label_ar", "label_en", "ledger", "is_active", "sort_order"]
        widgets = {
            "code": forms.TextInput(
                attrs={
                    "class": "form-input font-mono text-sm",
                    "dir": "ltr",
                    "placeholder": "cash",
                    "autocomplete": "off",
                }
            ),
            "label_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "كاش"}),
            "label_en": forms.TextInput(attrs={"class": "form-input", "dir": "ltr", "placeholder": "Cash"}),
            "ledger": forms.Select(attrs={"class": "form-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-input", "style": "max-width:120px", "min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["code"].disabled = True

    def clean(self):
        cd = super().clean()
        if not cd:
            return cd
        is_active = cd.get("is_active", True)
        code = (cd.get("code") or (self.instance.code if self.instance.pk else "") or "").strip().lower()
        if self.instance.pk and code == "cash" and not is_active:
            others = PaymentMethod.objects.filter(is_active=True, code="cash").exclude(pk=self.instance.pk)
            if not others.exists():
                raise ValidationError("يجب بقاء طريقة «كاش» (cash) نشطة واحدة على الأقل.")
        if not self.instance.pk:
            if code != "cash" or not is_active:
                if not PaymentMethod.objects.filter(is_active=True, code="cash").exists():
                    raise ValidationError(
                        "يجب وجود طريقة كاش نشطة (رمز cash). أضفها أولاً أو فعّل الصف الموجود.",
                    )
        return cd

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.instance.pk:
            obj.code = self.instance.code
        if commit:
            obj.save()
        return obj


class TreasuryVoucherForm(forms.Form):
    """سند موحّد: نوع الحركة قبض/صرف، وتصنيف الجهة منفصل."""

    VT_RECEIPT = "receipt"
    VT_DISBURSEMENT = "disbursement"
    PARTY_CUSTOMER = "customer"
    PARTY_SUPPLIER = "supplier"
    PARTY_EMPLOYEE = "employee"
    PARTY_EXPENSE = "expense"

    voucher_type = forms.ChoiceField(
        choices=[
            (VT_RECEIPT, "سند قبض"),
            (VT_DISBURSEMENT, "سند صرف"),
        ],
        label="نوع السند",
        widget=forms.HiddenInput(),
    )
    party_type = forms.ChoiceField(
        choices=[
            (PARTY_CUSTOMER, "عميل"),
            (PARTY_SUPPLIER, "مورد"),
            (PARTY_EMPLOYEE, "موظف"),
            (PARTY_EXPENSE, "مصاريف"),
        ],
        label="الحساب / الجهة",
        widget=forms.HiddenInput(),
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(),
        required=False,
        label="العميل",
        widget=forms.HiddenInput(),
    )
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.none(),
        required=False,
        label="المورد",
        widget=forms.HiddenInput(),
    )
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        required=False,
        label="الموظف",
        widget=forms.HiddenInput(),
    )
    amount = forms.DecimalField(
        min_value=Decimal("0.01"),
        label="المبلغ",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )
    voucher_date = forms.DateField(
        label="تاريخ السند",
        required=True,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-input w-full tabular-nums",
            }
        ),
        help_text="تاريخ احتساب القيد أو المصروف (للتسجيل المتأخر عن يوم العملية).",
    )
    method = forms.CharField(
        max_length=32,
        label="طريقة الدفع / التحصيل",
        widget=forms.HiddenInput(),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(
            attrs={
                "class": "form-input",
                "rows": 2,
                "id": "tv-voucher-note",
            }
        ),
    )
    payer_name = forms.CharField(
        required=False,
        max_length=120,
        label="اسم المحوّل",
        widget=forms.TextInput(
            attrs={
                "class": "form-input",
                "autocomplete": "off",
                "placeholder": "اسم المحوّل",
            }
        ),
    )
    payer_phone = forms.CharField(
        required=False,
        max_length=40,
        label="جوال المحوّل",
        widget=forms.TextInput(
            attrs={
                "type": "tel",
                "class": "form-input tabular-nums",
                "dir": "ltr",
                "inputmode": "tel",
                "placeholder": "جوال",
                "maxlength": "40",
            }
        ),
    )
    payment_splits_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "tv-payment-splits-json"}),
        initial="",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True).order_by("name_ar")
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True).order_by("name_ar")
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True).order_by("name_ar")
        if not self.data:
            self.fields["voucher_type"].initial = self.VT_RECEIPT
            self.fields["party_type"].initial = self.PARTY_CUSTOMER
            self.fields["method"].initial = "cash"
            self.fields["voucher_date"].initial = django_timezone.localdate()

    def clean_method(self):
        m = self.cleaned_data.get("method")
        if m not in get_payment_method_codes():
            raise ValidationError("طريقة دفع غير صالحة.")
        return m

    def clean(self):
        cd = super().clean()
        if not cd:
            return cd
        t = cd.get("voucher_type")
        party_type = cd.get("party_type")
        if t not in (self.VT_RECEIPT, self.VT_DISBURSEMENT):
            raise ValidationError({"voucher_type": "اختر نوع السند."})
        if party_type == self.PARTY_CUSTOMER:
            if not cd.get("customer"):
                raise ValidationError({"customer": "اختر العميل من الاقتراحات بعد البحث."})
        elif party_type == self.PARTY_SUPPLIER:
            if t != self.VT_DISBURSEMENT:
                raise ValidationError({"party_type": "المورد مرتبط بسند صرف."})
            if not cd.get("supplier"):
                raise ValidationError({"supplier": "اختر المورد من الاقتراحات بعد البحث."})
        elif party_type == self.PARTY_EMPLOYEE:
            if t != self.VT_DISBURSEMENT:
                raise ValidationError({"party_type": "الموظف مرتبط بسند صرف."})
            if not cd.get("employee"):
                raise ValidationError({"employee": "اختر الموظف من الاقتراحات بعد البحث."})
        elif party_type == self.PARTY_EXPENSE:
            if t != self.VT_DISBURSEMENT:
                raise ValidationError({"party_type": "المصاريف مرتبطة بسند صرف."})
        else:
            raise ValidationError({"party_type": "اختر الحساب أو الجهة."})

        method = (cd.get("method") or "").strip()
        payment_lines = self._parse_payment_lines_for_clean(cd)
        if payment_lines is not None:
            cd["payment_lines"] = payment_lines
            if payment_lines:
                cd["method"] = payment_lines[0][0]

        need_payer = False
        if t == self.VT_RECEIPT and party_type == self.PARTY_CUSTOMER:
            pls = cd.get("payment_lines")
            if pls:
                need_payer = any(
                    m in method_codes_requiring_payer_details() and a > 0 for m, a in pls
                )
            else:
                need_payer = method in method_codes_requiring_payer_details()
        if need_payer:
            pn = (cd.get("payer_name") or "").strip()
            ph_raw = (cd.get("payer_phone") or "").strip()
            digits = "".join(c for c in ph_raw if c.isdigit())
            if len(pn) < 2:
                raise ValidationError({"payer_name": "أدخل اسم المحوّل (حرفان على الأقل)."})
            if len(digits) < 8:
                raise ValidationError({"payer_phone": "أدخل رقم جوال صالحاً (8 أرقام على الأقل)."})
            cd["payer_name"] = pn[:120]
            cd["payer_phone"] = ph_raw[:40]
        else:
            cd["payer_name"] = ""
            cd["payer_phone"] = ""
        vd = cd.get("voucher_date")
        if vd and vd > django_timezone.localdate():
            raise ValidationError({"voucher_date": "تاريخ السند لا يمكن أن يكون بعد اليوم."})
        return cd

    def _parse_payment_lines_for_clean(self, cd: dict):
        """للعميل (قبض) والمورد (صرف): أسطر دفع متعددة من JSON؛ وإلا None لاستخدام طريقة واحدة."""
        t = cd.get("voucher_type")
        party_type = cd.get("party_type")
        use_splits = (t == self.VT_RECEIPT and party_type == self.PARTY_CUSTOMER) or (
            t == self.VT_DISBURSEMENT and party_type == self.PARTY_SUPPLIER
        )
        if not use_splits:
            return None

        raw = ""
        if self.data:
            raw = (self.data.get(self.add_prefix("payment_splits_json")) or "").strip()
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ValidationError({"payment_splits_json": "بيانات تقسيم الدفع غير صالحة."})

        if not isinstance(data, list) or len(data) > 16:
            raise ValidationError({"payment_splits_json": "عدد أسطر الدفع غير صالح."})

        codes = get_payment_method_codes()
        lines = []
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                code = str(item[0] or "").strip().lower()
                amt_raw = item[1]
            elif isinstance(item, dict):
                code = str(item.get("method") or "").strip().lower()
                amt_raw = item.get("amount")
            else:
                continue
            if not code or code not in codes:
                raise ValidationError({"payment_splits_json": "طريقة دفع غير صالحة في التقسيم."})
            try:
                a = Decimal(str(amt_raw).replace(",", "."))
            except Exception:
                raise ValidationError({"payment_splits_json": "مبلغ غير صالح في التقسيم."})
            if a <= 0:
                continue
            lines.append((code, a.quantize(Decimal("0.01"))))

        if not lines:
            return None

        total = sum(a for _, a in lines).quantize(Decimal("0.01"))
        amt = cd.get("amount")
        if amt is not None and total != amt.quantize(Decimal("0.01")):
            raise ValidationError(
                {"payment_splits_json": "مجموع أسطر الدفع يجب أن يساوي المبلغ الإجمالي."}
            )

        collected = sum(
            a for m, a in lines if resolve_ledger_account_code(m) != "AR"
        ).quantize(Decimal("0.01"))
        if collected <= 0:
            raise ValidationError({"payment_splits_json": "يجب أن يكون هناك مبلغ محصّل (غير آجل)."})

        return lines
