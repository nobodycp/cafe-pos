from django.contrib import admin

from apps.inventory.models import StockBalance, StockMovement


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity_on_hand", "average_cost")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "movement_type", "quantity_delta", "work_session", "created_at")
    list_filter = ("movement_type",)
