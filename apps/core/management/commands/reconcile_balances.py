"""
Management command to reconcile stored balances with computed (ledger-based) balances
for both customers and suppliers.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.contacts.models import Customer
from apps.purchasing.models import Supplier


class Command(BaseCommand):
    help = "Reconcile stored .balance fields with computed ledger balances"

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true", help="Actually update divergent balances")

    def handle(self, *args, **options):
        fix = options["fix"]
        divergent = 0

        self.stdout.write("\n=== العملاء ===")
        for c in Customer.objects.all():
            computed = c.computed_balance
            stored = Decimal(str(c.balance)).quantize(Decimal("0.01"))
            if computed != stored:
                divergent += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  [{c.pk}] {c.name_ar}: stored={stored}, computed={computed}, diff={stored - computed}"
                    )
                )
                if fix:
                    c.balance = computed
                    c.save(update_fields=["balance", "updated_at"])
                    self.stdout.write(self.style.SUCCESS(f"    -> Fixed to {computed}"))

        self.stdout.write("\n=== الموردون ===")
        for s in Supplier.objects.all():
            computed = s.computed_balance
            stored = Decimal(str(s.balance)).quantize(Decimal("0.01"))
            if computed != stored:
                divergent += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  [{s.pk}] {s.name_ar}: stored={stored}, computed={computed}, diff={stored - computed}"
                    )
                )
                if fix:
                    s.balance = computed
                    s.save(update_fields=["balance", "updated_at"])
                    self.stdout.write(self.style.SUCCESS(f"    -> Fixed to {computed}"))

        if divergent == 0:
            self.stdout.write(self.style.SUCCESS("\nAll balances are in sync."))
        else:
            msg = f"\n{divergent} divergent balance(s) found."
            if fix:
                self.stdout.write(self.style.SUCCESS(msg + " All fixed."))
            else:
                self.stdout.write(self.style.WARNING(msg + " Run with --fix to correct them."))
