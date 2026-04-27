from django.db import transaction

from apps.core.models import IdSequence


@transaction.atomic
def next_int(key: str) -> int:
    seq, _ = IdSequence.objects.select_for_update().get_or_create(key=key, defaults={"value": 0})
    seq.value += 1
    seq.save(update_fields=["value"])
    return seq.value
