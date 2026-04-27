"""Validation helpers for accounting integrity."""
from decimal import Decimal

from django.db.models import Sum

from apps.accounting.models import JournalEntry, JournalLine


def validate_all_entries_balanced() -> list:
    """Return list of unbalanced journal entries (should be empty)."""
    unbalanced = []
    for je in JournalEntry.objects.all():
        agg = je.lines.aggregate(d=Sum("debit"), c=Sum("credit"))
        d = agg["d"] or Decimal("0")
        c = agg["c"] or Decimal("0")
        if abs(d - c) >= Decimal("0.005"):
            unbalanced.append({
                "entry_number": je.entry_number,
                "pk": je.pk,
                "total_debit": d,
                "total_credit": c,
                "difference": (d - c).quantize(Decimal("0.01")),
            })
    return unbalanced


def validate_trial_balance() -> Decimal:
    """Return the difference between total debits and credits across all lines. Should be 0."""
    agg = JournalLine.objects.aggregate(d=Sum("debit"), c=Sum("credit"))
    d = agg["d"] or Decimal("0")
    c = agg["c"] or Decimal("0")
    return (d - c).quantize(Decimal("0.01"))
