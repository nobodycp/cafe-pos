from django.contrib import admin

from apps.billing.models import InvoicePayment, OrderPayment, SaleInvoice, SaleInvoiceLine


class SaleInvoiceLineInline(admin.TabularInline):
    model = SaleInvoiceLine
    extra = 0


class InvoicePaymentInline(admin.TabularInline):
    model = InvoicePayment
    extra = 0


@admin.register(SaleInvoice)
class SaleInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "work_session", "total", "total_profit", "payment_status", "created_at")
    list_filter = ("payment_status", "is_cancelled")
    inlines = [SaleInvoiceLineInline, InvoicePaymentInline]


@admin.register(SaleInvoiceLine)
class SaleInvoiceLineAdmin(admin.ModelAdmin):
    list_display = ("invoice", "product", "quantity", "line_subtotal", "line_profit")


@admin.register(InvoicePayment)
class InvoicePaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "method", "amount", "created_at")


@admin.register(OrderPayment)
class OrderPaymentAdmin(admin.ModelAdmin):
    list_display = ("order", "method", "amount", "sale_invoice", "created_at")
    list_filter = ("method",)
