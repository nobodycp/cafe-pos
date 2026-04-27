from django.contrib import admin

from apps.pos.models import DiningTable, Order, OrderLine, TableSession


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(TableSession)
class TableSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "work_session", "dining_table", "customer", "status", "created_at")
    list_filter = ("status",)


@admin.register(DiningTable)
class DiningTableAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "sort_order", "is_active", "is_cancelled")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "work_session", "order_type", "table", "table_session", "is_held", "status", "created_at")
    list_filter = ("status", "order_type")
    inlines = [OrderLineInline]


@admin.register(OrderLine)
class OrderLineAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "quantity", "unit_price")
