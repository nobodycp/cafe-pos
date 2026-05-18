from django.http import JsonResponse


def json_ok(data, *, status=200):
    return JsonResponse({"ok": True, "data": data}, status=status)


def json_error(message, *, status=400, code=None):
    body = {"ok": False, "error": message}
    if code:
        body["code"] = code
    return JsonResponse(body, status=status)
