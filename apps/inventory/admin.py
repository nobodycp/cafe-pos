from django.contrib import admin

from apps.inventory.models import ManufacturingBatch, StockBalance, StockMovement


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity_on_hand", "average_cost")


@admin.register(ManufacturingBatch)
class ManufacturingBatchAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "work_session", "created_at")
    list_filter = ("created_at",)
    search_fields = ("product__name_ar", "note")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "movement_type", "quantity_delta", "work_session", "created_at")
    list_filter = ("movement_type",)
