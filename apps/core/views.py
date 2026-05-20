import json
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Q, Sum
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import Resolver404, resolve, reverse
from django.views.decorators.http import require_GET, require_POST

from apps.core.treasury_void import void_unified_treasury_voucher
from apps.core.panel import PanelFormInvalid, handle_panel_form, render_panel

from apps.billing.models import InvoicePayment, SaleInvoice
from apps.contacts.customer_lookup import active_customers_search_qs
from apps.contacts.models import Customer
from apps.core.models import AuditLog, log_audit
from apps.core.forms import TreasuryVoucherForm
from apps.core.treasury_services import (
    recent_treasury_voucher_logs,
    submit_treasury_voucher,
    treasury_voucher_form_initial_from_audit,
    TREASURY_VOUCHER_AUDIT_ACTION,
)
from apps.core.payment_methods import load_payment_method_rows, payment_method_label_map
from apps.core.services import SessionService
from apps.expenses.models import Expense
from apps.payroll.models import Employee
from apps.pos.forms import DiningTableForm
from apps.pos.models import DiningTable, TableSession
from apps.pos.services import open_orders_with_lines_queryset
from apps.pos.table_service import prepare_work_session_for_shift_close
from apps.purchasing.models import Supplier, SupplierPayment

logger = logging.getLogger(__name__)


def _parse_opening_balances_post(request) -> dict[str, str]:
    """قراءة أرصدة افتتاحية لكل طريقة دفع من POST؛ أو الحقل القديم opening_cash فقط."""
    rows = load_payment_method_rows()
    use_split = any(k.startswith("opening_balance_") for k in request.POST)
    out: dict[str, str] = {}
    q = Decimal("0.01")
    if use_split:
        for r in rows:
            code = r["code"]
            raw = (request.POST.get(f"opening_balance_{code}") or "").strip()
            try:
                v = Decimal(str(raw).replace(",", ".")) if raw else Decimal("0")
            except (InvalidOperation, ValueError):
                v = Decimal("0")
            if v < 0:
                v = Decimal("0")
            out[code] = str(v.quantize(q))
        return out
    raw = (request.POST.get("opening_cash") or "0").strip()
    try:
        oc = Decimal(str(raw).replace(",", "."))
    except (InvalidOperation, ValueError):
        oc = Decimal("0")
    if oc < 0:
        oc = Decimal("0")
    for r in rows:
        c = r["code"]
        out[c] = str(oc.quantize(q)) if c == "cash" else "0.00"
    return out


@login_required
def home(request):
    return redirect("pos:main")


def _safe_treasury_redirect_next(request) -> Optional[str]:
    """يسمح بإعادة التوجيه الداخلية فقط (مثلاً الكاشير) بعد تسجيل سند."""
    raw = (request.POST.get("next") or "").strip()
    if not raw or "\n" in raw or "\r" in raw or ".." in raw or raw.startswith("//"):
        return None
    if not raw.startswith("/"):
        return None
    path_only = raw.split("?", 1)[0]
    if not path_only.startswith("/pos"):
        return None
    try:
        resolve(path_only)
    except Resolver404:
        return None
    return raw


def _safe_action_redirect_next(request) -> Optional[str]:
    """إعادة توجيه داخلية بعد إلغاء سند أو إجراء مشابه (كاشير أو مسارات /app/)."""
    raw = (request.POST.get("next") or "").strip()
    if not raw or "\n" in raw or "\r" in raw or ".." in raw or raw.startswith("//"):
        return None
    if not raw.startswith("/"):
        return None
    path_only = raw.split("?", 1)[0]
    if not (path_only.startswith("/pos") or path_only.startswith("/app/")):
        return None
    try:
        resolve(path_only)
    except Resolver404:
        return None
    return raw


def _treasury_replace_context_from_post(request) -> Tuple[Optional[int], str]:
    """يستخرج رقم سجل الاستبدال من الطلب والجلسة مع تسمية الجهة للعرض في النموذج."""
    replace_raw = (request.POST.get("replace_audit_pk") or "").strip()
    stored = request.session.get("treasury_edit_audit_pk")
    if not replace_raw.isdigit() or stored is None or int(replace_raw) != int(stored):
        return None, ""
    try:
        log = AuditLog.objects.get(pk=int(replace_raw), action=TREASURY_VOUCHER_AUDIT_ACTION)
    except AuditLog.DoesNotExist:
        return None, ""
    label = str((log.payload or {}).get("party_label") or "")
    return int(replace_raw), label


@login_required
def treasury(request):
    """سند موحّد: نوع السند قبض/صرف، وتصنيف الجهة منفصل."""
    if request.method == "GET" and request.GET.get("cancel_edit"):
        if request.session.pop("treasury_edit_audit_pk", None) is not None:
            messages.info(request, "تم إلغاء وضع تعديل السند.")
        return redirect("shell:accounting_treasury")

    ws = SessionService.get_open_session()
    treasury_replace_audit_pk: Optional[int] = None
    treasury_edit_party_label = ""

    voucher_form = TreasuryVoucherForm(prefix="tv")
    if request.method == "GET":
        edit_pk = request.session.get("treasury_edit_audit_pk")
        if edit_pk is not None:
            try:
                log = AuditLog.objects.get(pk=int(edit_pk), action=TREASURY_VOUCHER_AUDIT_ACTION)
                if (log.payload or {}).get("cancelled"):
                    request.session.pop("treasury_edit_audit_pk", None)
                else:
                    initial = treasury_voucher_form_initial_from_audit(audit_log=log)
                    voucher_form = TreasuryVoucherForm(initial=initial, prefix="tv")
                    treasury_replace_audit_pk = int(edit_pk)
                    treasury_edit_party_label = str((log.payload or {}).get("party_label") or "")
            except AuditLog.DoesNotExist:
                request.session.pop("treasury_edit_audit_pk", None)
            except ValueError as e:
                request.session.pop("treasury_edit_audit_pk", None)
                if str(e) == "UNSUPPORTED_EDIT":
                    messages.info(request, "لا يمكن تعديل هذا السند من النموذج — نوعه لم يعد مدعوماً.")

    if request.method == "POST":
        next_url = _safe_treasury_redirect_next(request)
        voucher_form = TreasuryVoucherForm(request.POST, prefix="tv")
        rep_pk, rep_label = _treasury_replace_context_from_post(request)
        treasury_replace_audit_pk = rep_pk
        treasury_edit_party_label = rep_label
        if voucher_form.is_valid():
            vt = voucher_form.cleaned_data["voucher_type"]
            replace_pk = rep_pk
            try:
                if replace_pk is not None:
                    with transaction.atomic():
                        void_unified_treasury_voucher(audit_log_id=replace_pk, user=request.user)
                        submit_treasury_voucher(
                            voucher_type=vt,
                            cleaned=voucher_form.cleaned_data,
                            user=request.user,
                            work_session=ws,
                        )
                    request.session.pop("treasury_edit_audit_pk", None)
                    messages.success(request, "تم استبدال السند بنجاح (إلغاء القديم وتسجيل الجديد).")
                else:
                    submit_treasury_voucher(
                        voucher_type=vt,
                        cleaned=voucher_form.cleaned_data,
                        user=request.user,
                        work_session=ws,
                    )
                    if vt == TreasuryVoucherForm.VT_RECEIPT:
                        messages.success(request, "تم تسجيل سند القبض بنجاح.")
                    else:
                        messages.success(request, "تم تسجيل سند الصرف بنجاح.")
                if next_url:
                    return redirect(next_url)
                return redirect("shell:accounting_treasury")
            except ValueError as e:
                code = str(e)
                if code == "UNKNOWN_VOUCHER_TYPE":
                    messages.error(request, "نوع السند غير معروف.")
                elif code == "INVALID_AMOUNT":
                    messages.error(request, "المبلغ غير صالح.")
                elif code == "PAYMENT_LINES_SUM_MISMATCH":
                    messages.error(request, "مجموع أسطر الدفع لا يطابق المبلغ الإجمالي.")
                elif code == "ALREADY_VOIDED":
                    messages.error(request, "السند المراد استبداله أصبح ملغىً — أعد فتح التعديل من الجدول.")
                    request.session.pop("treasury_edit_audit_pk", None)
                elif code in ("UNKNOWN_TREASURY_VOUCHER", "BAD_PAYLOAD", "INVALID_LEDGER_ENTRY", "ALREADY_REVERSED"):
                    messages.error(request, f"تعذّر إلغاء السند السابق: {code}")
                else:
                    messages.error(request, "المبلغ غير صالح.")
            except Exception as e:
                messages.error(request, f"تعذّر التسجيل: {e}")
        else:
            messages.error(request, "راجع بيانات السند.")
    return render(
        request,
        "shell/treasury.html",
        {
            "voucher_form": voucher_form,
            "work_session": ws,
            "recent_treasury_rows": list(recent_treasury_voucher_logs(limit=10)),
            "treasury_replace_audit_pk": treasury_replace_audit_pk,
            "treasury_edit_party_label": treasury_edit_party_label,
        },
    )


@login_required
@require_POST
def treasury_void_voucher(request, audit_pk):
    """إلغاء سند صندوق موحّد (عكس قيود وأرصدة) من سجل التدقيق."""
    try:
        void_unified_treasury_voucher(audit_log_id=audit_pk, user=request.user)
        messages.success(request, "تم إلغاء السند وعكس الأثر المحاسبي والأرصدة.")
    except AuditLog.DoesNotExist:
        messages.error(request, "سجل السند غير موجود.")
    except ValueError as e:
        code = str(e)
        if code == "ALREADY_VOIDED":
            messages.warning(request, "هذا السند ملغى مسبقاً.")
        elif code == "UNKNOWN_TREASURY_VOUCHER":
            messages.error(request, "نوع السند لا يدعم الإلغاء من هذه الشاشة.")
        elif code == "INVALID_LEDGER_ENTRY":
            messages.error(request, "قيد العميل غير صالح للإلغاء.")
        elif code == "BAD_PAYLOAD":
            messages.error(request, "بيانات السند غير مكتملة.")
        elif code == "ALREADY_REVERSED":
            messages.warning(request, "القيد المحاسبي معكوس مسبقاً.")
        else:
            messages.error(request, f"تعذّر الإلغاء: {code}")
    except Exception as e:
        messages.error(request, f"تعذّر الإلغاء: {e}")
    if request.session.get("treasury_edit_audit_pk") == audit_pk:
        request.session.pop("treasury_edit_audit_pk", None)
    next_url = _safe_action_redirect_next(request)
    if next_url:
        return redirect(next_url)
    return redirect("shell:accounting_treasury")


@login_required
@require_POST
def treasury_purge_cancelled_voucher(request, audit_pk):
    """حذف سجل التدقيق لسند ملغى فقط (الأثر المحاسبي مُعكوس مسبقاً عند الإلغاء)."""
    log = get_object_or_404(AuditLog, pk=audit_pk, action=TREASURY_VOUCHER_AUDIT_ACTION)
    payload = dict(log.payload or {})
    if not payload.get("cancelled"):
        messages.error(
            request,
            "يمكن «حذف السجل» للسندات الملغاة فقط. للسند النشط استخدم «حذف» لإلغاء السند وعكس الأثر.",
        )
    else:
        pk_copy = int(log.pk)
        party_l = str(payload.get("party_label") or "")
        log_audit(
            request.user,
            "treasury.voucher_log_purged",
            "treasury.UnifiedVoucher",
            str(pk_copy),
            {"purged_from_audit_pk": pk_copy, "party_label": party_l[:120]},
        )
        log.delete()
        messages.success(request, "تم حذف سجل السند الملغى من القائمة.")
    if request.session.get("treasury_edit_audit_pk") == audit_pk:
        request.session.pop("treasury_edit_audit_pk", None)
    next_url = _safe_action_redirect_next(request)
    if next_url:
        return redirect(next_url)
    return redirect("shell:accounting_treasury")


@login_required
@require_GET
def treasury_start_edit_voucher(request, audit_pk):
    """يبدأ تعديل سند موحّد: يحفظ معرف السجل في الجلسة ويعيد إلى نموذج السند."""
    log = get_object_or_404(AuditLog, pk=audit_pk, action=TREASURY_VOUCHER_AUDIT_ACTION)
    if (log.payload or {}).get("cancelled"):
        messages.error(request, "لا يمكن تعديل سند ملغى.")
        return redirect("shell:accounting_treasury")
    request.session["treasury_edit_audit_pk"] = int(audit_pk)
    messages.info(
        request,
        "عدّل الحقول ثم اضغط «تسجيل السند» — سيُلغى السند السابق ويُستبدل بالجديد بعد التحقق من صحة البيانات.",
    )
    return redirect("shell:accounting_treasury")


@login_required
def treasury_voucher_panel(request):
    ws = SessionService.get_open_session()
    tpl = "shell/panels/treasury_voucher_panel.html"
    panel_action = reverse("shell:treasury_voucher_panel")

    def build_context():
        voucher_form = TreasuryVoucherForm(prefix="tv")
        if request.method == "POST":
            voucher_form = TreasuryVoucherForm(request.POST, prefix="tv")
        return {
            "voucher_form": voucher_form,
            "work_session": ws,
            "form_action": panel_action,
            "panel_title": "سند جديد",
            "treasury_replace_audit_pk": None,
            "treasury_edit_party_label": "",
        }

    def on_valid():
        voucher_form = TreasuryVoucherForm(request.POST, prefix="tv")
        if not voucher_form.is_valid():
            raise PanelFormInvalid("راجع بيانات السند")
        vt = voucher_form.cleaned_data["voucher_type"]
        try:
            submit_treasury_voucher(
                voucher_type=vt,
                cleaned=voucher_form.cleaned_data,
                user=request.user,
                work_session=ws,
            )
        except ValueError as e:
            code = str(e)
            if code in ("UNKNOWN_VOUCHER_TYPE", "INVALID_AMOUNT", "PAYMENT_LINES_SUM_MISMATCH"):
                raise PanelFormInvalid("راجع بيانات السند") from e
            raise PanelFormInvalid(f"تعذّر التسجيل: {code}") from e
        except Exception as e:
            raise PanelFormInvalid(f"تعذّر التسجيل: {e}") from e

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid, wide=True)


@login_required
def treasury_voucher_edit_panel(request, audit_pk):
    log = get_object_or_404(AuditLog, pk=audit_pk, action=TREASURY_VOUCHER_AUDIT_ACTION)
    if (log.payload or {}).get("cancelled"):
        return render_panel(
            request,
            "shell/panels/treasury_voucher_edit_panel.html",
            {"panel_form_errors": "لا يمكن تعديل سند ملغى.", "panel_title": "تعديل السند"},
            wide=True,
        )
    ws = SessionService.get_open_session()
    tpl = "shell/panels/treasury_voucher_edit_panel.html"
    panel_action = reverse("shell:treasury_voucher_edit_panel", args=[audit_pk])
    party_label = str((log.payload or {}).get("party_label") or "")

    def build_context():
        try:
            initial = treasury_voucher_form_initial_from_audit(audit_log=log)
        except ValueError as e:
            if str(e) == "UNSUPPORTED_EDIT":
                return {
                    "panel_form_errors": "لا يمكن تعديل هذا السند من النموذج.",
                    "panel_title": "تعديل السند",
                    "voucher_form": TreasuryVoucherForm(prefix="tv"),
                    "form_action": panel_action,
                }
            raise
        if request.method == "POST":
            voucher_form = TreasuryVoucherForm(request.POST, prefix="tv")
        else:
            voucher_form = TreasuryVoucherForm(initial=initial, prefix="tv")
        return {
            "voucher_form": voucher_form,
            "work_session": ws,
            "form_action": panel_action,
            "panel_title": "تعديل السند",
            "treasury_replace_audit_pk": int(audit_pk),
            "treasury_edit_party_label": party_label,
        }

    def on_valid():
        voucher_form = TreasuryVoucherForm(request.POST, prefix="tv")
        if not voucher_form.is_valid():
            raise PanelFormInvalid("راجع بيانات السند")
        vt = voucher_form.cleaned_data["voucher_type"]
        try:
            with transaction.atomic():
                void_unified_treasury_voucher(audit_log_id=int(audit_pk), user=request.user)
                submit_treasury_voucher(
                    voucher_type=vt,
                    cleaned=voucher_form.cleaned_data,
                    user=request.user,
                    work_session=ws,
                )
        except ValueError as e:
            raise PanelFormInvalid(f"تعذّر الاستبدال: {e}") from e
        except Exception as e:
            raise PanelFormInvalid(f"تعذّر الاستبدال: {e}") from e

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid, wide=True)


@login_required
@require_GET
def treasury_voucher_view_panel(request, audit_pk):
    log = get_object_or_404(AuditLog, pk=audit_pk, action=TREASURY_VOUCHER_AUDIT_ACTION)
    return render_panel(
        request,
        "shell/panels/treasury_voucher_view_panel.html",
        {
            "audit_log": log,
            "payload": log.payload or {},
            "panel_title": "عرض السند",
        },
        wide=True,
    )


def _supplier_net_balance_for_party(s: Supplier) -> Decimal:
    """رصيد المورد بعد مسحوبات العميل المرتبط (رصيد المورد − رصيد العميل المرتبط) كما في قائمة الموردين."""
    cust = Decimal("0")
    if s.linked_customer_id:
        cust = (s.linked_customer.balance or Decimal("0")).quantize(Decimal("0.01"))
    return (s.balance - cust).quantize(Decimal("0.01"))


@login_required
@require_GET
def treasury_party_search(request):
    """اقتراحات عميل / مورد / موظف لحقل «اسم صاحب السند» في سند الصندوق — مع الرصيد النهائي."""
    q = (request.GET.get("q") or "").strip()
    party_type = (request.GET.get("party_type") or "").strip()
    if len(q) < 1 or party_type not in (
        TreasuryVoucherForm.PARTY_CUSTOMER,
        TreasuryVoucherForm.PARTY_SUPPLIER,
        TreasuryVoucherForm.PARTY_EMPLOYEE,
    ):
        return JsonResponse({"results": []})

    limit = 24
    results = []
    if party_type == TreasuryVoucherForm.PARTY_CUSTOMER:
        for c in active_customers_search_qs(q, limit=limit):
            bal = (c.balance or Decimal("0")).quantize(Decimal("0.01"))
            results.append({"id": c.pk, "label": c.name_ar, "balance": str(bal)})
    elif party_type == TreasuryVoucherForm.PARTY_SUPPLIER:
        qs = (
            Supplier.objects.filter(is_active=True)
            .select_related("linked_customer")
            .filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(phone__icontains=q))
            .order_by("name_ar")[:limit]
        )
        for s in qs:
            net_b = _supplier_net_balance_for_party(s)
            results.append({"id": s.pk, "label": s.name_ar, "balance": str(net_b)})
    elif party_type == TreasuryVoucherForm.PARTY_EMPLOYEE:
        qs = (
            Employee.objects.filter(is_active=True)
            .filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q))
            .order_by("name_ar")[:limit]
        )
        for e in qs:
            nb = (e.net_balance or Decimal("0")).quantize(Decimal("0.01"))
            results.append({"id": e.pk, "label": e.name_ar, "balance": str(nb)})
    return JsonResponse({"results": results})


@login_required
@require_POST
def treasury_customer_quick_create(request):
    """إنشاء عميل سريع من سند الصندوق (JSON) — دون اشتراط وردية POS."""
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:200]
    if len(name_ar) < 2:
        return JsonResponse({"error": "أدخل اسم عميل بحرفين على الأقل"}, status=400)
    phone = (body.get("phone") or "").strip()[:32]
    existing = Customer.objects.filter(name_ar__iexact=name_ar, is_active=True).first()
    if existing:
        bal = (existing.balance or Decimal("0")).quantize(Decimal("0.01"))
        return JsonResponse({"id": existing.pk, "name_ar": existing.name_ar, "balance": str(bal), "reused": True})
    c = Customer.objects.create(name_ar=name_ar, name_en="", phone=phone)
    log_audit(request.user, "contacts.customer.quick_create_treasury", "contacts.Customer", c.pk, {})
    return JsonResponse({"id": c.pk, "name_ar": c.name_ar, "balance": "0.00", "reused": False})


@login_required
@require_POST
def treasury_employee_quick_create(request):
    """إنشاء موظف سريع من سند الصندوق (JSON) — حقول افتراضية للأجور."""
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:200]
    if len(name_ar) < 2:
        return JsonResponse({"error": "أدخل اسم موظف بحرفين على الأقل"}, status=400)
    existing = Employee.objects.filter(name_ar__iexact=name_ar, is_active=True).first()
    if existing:
        nb = (existing.net_balance or Decimal("0")).quantize(Decimal("0.01"))
        return JsonResponse({"id": existing.pk, "name_ar": existing.name_ar, "balance": str(nb), "reused": True})
    emp = Employee.objects.create(name_ar=name_ar, name_en="")
    log_audit(request.user, "payroll.employee.quick_create_treasury", "payroll.Employee", emp.pk, {})
    return JsonResponse({"id": emp.pk, "name_ar": emp.name_ar, "balance": "0.00", "reused": False})


@login_required
@require_POST
def open_session_view(request):
    balances = _parse_opening_balances_post(request)
    try:
        opening = Decimal(balances.get("cash", "0"))
    except (InvalidOperation, ValueError):
        opening = Decimal("0")
    try:
        SessionService.open_session(
            request.user,
            opening,
            request.POST.get("notes", ""),
            opening_balances=balances,
        )
    except ValueError as e:
        if str(e) == "SESSION_ALREADY_OPEN":
            request.session["flash_error"] = "يوجد وردية مفتوحة بالفعل."
        else:
            request.session["flash_error"] = str(e)
    return redirect("pos:main")


@login_required
@require_POST
def close_session_view(request):
    raw = request.POST.get("closing_cash", "")
    closing = None
    if raw != "":
        try:
            closing = Decimal(str(raw).replace(",", "."))
        except (InvalidOperation, ValueError):
            closing = None
    ws = SessionService.get_open_session()
    if ws:
        prepare_work_session_for_shift_close(ws)
        if open_orders_with_lines_queryset(ws).exists():
            request.session["flash_error"] = (
                "لا يمكن إغلاق الوردية: يوجد طلبات مفتوحة أو طاولات لم تُسوَّ بعد. "
                "أكمل الدفع أو ألغِ الطلب من ملخص نهاية اليوم أو من الكاشير."
            )
            return redirect("pos:main")
        if TableSession.objects.filter(work_session=ws, status=TableSession.Status.OPEN).exists():
            request.session["flash_error"] = (
                "لا يمكن إغلاق الوردية: جلسات طاولات مفتوحة. راجع الطاولات من الكاشير."
            )
            return redirect("pos:main")
    try:
        SessionService.close_session(request.user, closing, request.POST.get("notes", ""))
        request.session["flash_ok"] = "تم إغلاق الوردية."
    except ValueError as e:
        request.session["flash_error"] = str(e)
    request.session.pop("active_pos_order_id", None)
    return redirect("pos:main")


@login_required
def tables_list(request):
    tables = DiningTable.objects.order_by("sort_order", "name_ar")
    return render(request, "core/tables_list.html", {"tables": tables})


@login_required
def table_create(request):
    if request.method == "POST":
        form = DiningTableForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة الطاولة بنجاح.")
            return redirect("shell:tables_list")
    else:
        form = DiningTableForm()
    return render(request, "core/table_form.html", {"form": form, "edit": False})


@login_required
def table_edit(request, pk):
    table = get_object_or_404(DiningTable, pk=pk)
    if request.method == "POST":
        form = DiningTableForm(request.POST, instance=table)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل الطاولة بنجاح.")
            return redirect("shell:tables_list")
    else:
        form = DiningTableForm(instance=table)
    return render(request, "core/table_form.html", {"form": form, "edit": True})


@login_required
@require_POST
def table_toggle(request, pk):
    table = get_object_or_404(DiningTable, pk=pk)
    table.is_active = not table.is_active
    table.save(update_fields=["is_active", "updated_at"])
    status = "تفعيل" if table.is_active else "إلغاء تفعيل"
    messages.success(request, f"تم {status} الطاولة «{table.name_ar}».")
    return redirect("shell:tables_list")


def _reconcile_method_q_expense(code: str) -> Q:
    if code == "cash":
        return Q(payment_method="cash") | Q(payment_method="") | Q(payment_method__isnull=True)
    return Q(payment_method=code)


def _reconcile_method_q_supplier(code: str) -> Q:
    if code == "cash":
        return Q(method="cash") | Q(method="")
    return Q(method=code)


def _session_reconcile_detail_payload(ws, code: str, kind: str) -> dict:
    """تفاصيل حركات مطابقة الصناديق — نفس فلاتر التجميع في session_summary."""
    q = Decimal("0.01")
    lines: list[dict] = []
    total = Decimal("0")

    if kind == "expenses":
        for exp in (
            Expense.objects.filter(work_session=ws)
            .filter(_reconcile_method_q_expense(code))
            .select_related("category")
            .order_by("-expense_date", "-pk")
        ):
            amt = (exp.amount or Decimal("0")).quantize(q)
            desc = (exp.category.name_ar if exp.category_id else "") or "—"
            if (exp.notes or "").strip():
                desc = f"{desc} — {exp.notes.strip()}" if desc != "—" else exp.notes.strip()
            lines.append(
                {
                    "line_type": "expense",
                    "line_type_label": "مصروف",
                    "date": exp.expense_date,
                    "description": desc,
                    "amount": amt,
                    "reference": f"#{exp.pk}",
                }
            )
            total += amt
        for sp in (
            SupplierPayment.objects.filter(work_session=ws)
            .filter(_reconcile_method_q_supplier(code))
            .select_related("supplier")
            .order_by("-created_at", "-pk")
        ):
            amt = (sp.amount or Decimal("0")).quantize(q)
            ref = f"سند #{sp.pk}"
            if (sp.note or "").strip():
                ref = f"{ref} — {sp.note.strip()}"
            lines.append(
                {
                    "line_type": "supplier_payment",
                    "line_type_label": "سند صرف",
                    "date": sp.created_at.date() if sp.created_at else None,
                    "description": (sp.supplier.name_ar if sp.supplier_id else "") or "—",
                    "amount": amt,
                    "reference": ref,
                }
            )
            total += amt
        lines.sort(key=lambda r: (r["date"] or date.min, r["line_type"]), reverse=True)
    else:
        for pay in (
            InvoicePayment.objects.filter(
                invoice__work_session=ws,
                invoice__is_cancelled=False,
                method=code,
            )
            .select_related("invoice", "invoice__customer")
            .order_by("-created_at", "-pk")
        ):
            amt = (pay.amount or Decimal("0")).quantize(q)
            inv = pay.invoice
            cust = ""
            if inv.customer_id:
                cust = inv.customer.name_ar or ""
            lines.append(
                {
                    "line_type": "sale",
                    "line_type_label": "تحصيل",
                    "date": pay.created_at.date() if pay.created_at else None,
                    "description": inv.invoice_number if inv else "—",
                    "customer": cust or "—",
                    "amount": amt,
                    "reference": inv.invoice_number if inv else "—",
                }
            )
            total += amt

    total = total.quantize(q)
    labels = payment_method_label_map()
    return {
        "kind": kind,
        "kind_label": "مبيعات" if kind == "sales" else "مصروفات",
        "payment_method_code": code,
        "payment_method_label": labels.get(code, code),
        "lines": lines,
        "total": total,
    }


@login_required
@require_GET
def session_reconcile_detail(request):
    ws = SessionService.get_open_session()
    if not ws:
        return render(
            request,
            "core/_session_reconcile_detail.html",
            {"error": "لا توجد وردية مفتوحة.", "lines": [], "total": Decimal("0")},
            status=404,
        )

    kind = (request.GET.get("kind") or "").strip().lower()
    if kind not in ("sales", "expenses"):
        return render(
            request,
            "core/_session_reconcile_detail.html",
            {"error": "نوع غير صالح.", "lines": [], "total": Decimal("0")},
            status=400,
        )

    code = (request.GET.get("payment_method") or "").strip().lower()
    if not code:
        return render(
            request,
            "core/_session_reconcile_detail.html",
            {"error": "طريقة الدفع مطلوبة.", "lines": [], "total": Decimal("0")},
            status=400,
        )

    ctx = _session_reconcile_detail_payload(ws, code, kind)
    ctx["error"] = None
    return render(request, "core/_session_reconcile_detail.html", ctx)


@login_required
def session_summary(request):
    ws = SessionService.get_open_session()
    if not ws:
        return redirect("pos:main")

    if request.method == "POST":
        raw = request.POST.get("closing_cash", "")
        closing = None
        if raw != "":
            try:
                closing = Decimal(str(raw).replace(",", "."))
            except (InvalidOperation, ValueError):
                closing = None
        prepare_work_session_for_shift_close(ws)
        if open_orders_with_lines_queryset(ws).exists():
            messages.error(
                request,
                "لا يمكن إغلاق الوردية: يوجد طلبات مفتوحة. أكمل الدفع أو ألغِ الطلب من الجدول أعلاه.",
            )
            return redirect("core:session_summary")
        if TableSession.objects.filter(work_session=ws, status=TableSession.Status.OPEN).exists():
            messages.error(
                request,
                "لا يمكن إغلاق الوردية: جلسات طاولات مفتوحة. راجع الطاولات من الكاشير.",
            )
            return redirect("core:session_summary")
        try:
            SessionService.close_session(request.user, closing, request.POST.get("notes", ""))
            messages.success(request, "تم إغلاق الوردية بنجاح.")
        except ValueError as e:
            messages.error(request, str(e))
        request.session.pop("active_pos_order_id", None)
        return redirect("pos:main")

    invoices = SaleInvoice.objects.filter(work_session=ws, is_cancelled=False)
    totals = invoices.aggregate(
        revenue=Sum("total"),
        profit=Sum("total_profit"),
        cost=Sum("total_cost"),
    )
    revenue = totals["revenue"] or Decimal("0")
    profit = totals["profit"] or Decimal("0")
    invoice_count = invoices.count()

    pay_qs = (
        InvoicePayment.objects.filter(invoice__work_session=ws, invoice__is_cancelled=False)
        .values("method")
        .annotate(s=Sum("amount"))
    )
    pay_map: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for p in pay_qs:
        pay_map[p["method"]] = p["s"] or Decimal("0")
    pay_map = dict(pay_map)

    pm_rows = load_payment_method_rows()
    for r in pm_rows:
        pay_map.setdefault(r["code"], Decimal("0"))
    labels = payment_method_label_map()
    payment_channel_totals = []
    seen_codes = set()
    for r in pm_rows:
        code = r["code"]
        seen_codes.add(code)
        payment_channel_totals.append(
            {
                "code": code,
                "label": r["label_ar"] or labels.get(code, code),
                "ledger": r["ledger"],
                "amount": pay_map.get(code, Decimal("0")),
            }
        )
    for method, amt in sorted(pay_map.items(), key=lambda x: x[0]):
        if method not in seen_codes:
            payment_channel_totals.append(
                {
                    "code": method,
                    "label": labels.get(method, method),
                    "ledger": "",
                    "amount": amt,
                }
            )

    expenses_qs = Expense.objects.filter(work_session=ws)
    total_expenses = expenses_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    exp_by_method: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in expenses_qs.values("payment_method").annotate(s=Sum("amount")):
        m = row["payment_method"] or "cash"
        exp_by_method[m] = row["s"] or Decimal("0")
    # سند صرف لمورد (سداد مورد) يُسجَّل كـ SupplierPayment وليس Expense — يُخصم من الصندوق/البنك.
    for row in SupplierPayment.objects.filter(work_session=ws).values("method").annotate(s=Sum("amount")):
        m = row["method"] or "cash"
        exp_by_method[m] = (exp_by_method.get(m, Decimal("0")) + (row["s"] or Decimal("0"))).quantize(
            Decimal("0.01")
        )
    exp_by_method = dict(exp_by_method)
    cash_expenses = exp_by_method.get("cash", Decimal("0"))

    opening_json = ws.opening_balances_json or {}
    if not opening_json and (ws.opening_cash is not None):
        opening_json = {"cash": str((ws.opening_cash or Decimal("0")).quantize(Decimal("0.01")))}
    q = Decimal("0.01")

    def _opening_for(code: str) -> Decimal:
        raw = opening_json.get(code)
        if raw is None or raw == "":
            if code == "cash" and ws.opening_cash is not None:
                return (ws.opening_cash or Decimal("0")).quantize(q)
            return Decimal("0")
        try:
            return Decimal(str(raw)).quantize(q)
        except (InvalidOperation, ValueError):
            return Decimal("0")

    desk_reconcile_rows = []
    for r in pm_rows:
        if r["ledger"] not in ("cash", "bank"):
            continue
        code = r["code"]
        op = _opening_for(code)
        sales = pay_map.get(code, Decimal("0"))
        expm = exp_by_method.get(code, Decimal("0"))
        desk_reconcile_rows.append(
            {
                "code": code,
                "label": r["label_ar"] or labels.get(code, code),
                "opening": op,
                "sales": sales,
                "expenses": expm,
                "expected": op + sales - expm,
            }
        )

    opening_cash = _opening_for("cash")
    expected_cash = opening_cash + pay_map.get("cash", Decimal("0")) - cash_expenses

    open_orders = open_orders_with_lines_queryset(ws).select_related("table", "customer")

    net_profit = profit - total_expenses

    return render(request, "core/session_summary.html", {
        "session": ws,
        "revenue": revenue,
        "profit": profit,
        "invoice_count": invoice_count,
        "payment_channel_totals": payment_channel_totals,
        "desk_reconcile_rows": desk_reconcile_rows,
        "total_expenses": total_expenses,
        "expected_cash": expected_cash,
        "net_profit": net_profit,
        "open_orders": open_orders,
    })


@login_required
@require_POST
def settings_database_wipe(request):
    """تفريغ بيانات التشغيل للاختبار — سوبر يوزر فقط، ومعطّل إلا ببيئة اختبار."""
    from django.conf import settings as dj_settings

    from apps.core.database_wipe import PRESERVE_LABELS_AR, wipe_runtime_tables
    from apps.core.models import PosSettings

    redirect_url = reverse("shell:settings") + "?tab=test-data"
    if not getattr(dj_settings, "ALLOW_TEST_DATABASE_WIPE", dj_settings.DEBUG):
        messages.error(
            request,
            "تفريغ قاعدة البيانات من الواجهة معطّل. فعّل DEBUG أو ضع ALLOW_TEST_DATABASE_WIPE=1 في ملف البيئة.",
        )
        return redirect(redirect_url)
    if not request.user.is_superuser:
        messages.error(request, "يتطلب هذا الإجراء حساب مدير نظام (سوبر يوزر).")
        return redirect(redirect_url)
    if request.POST.get("accept_risk") != "1":
        messages.error(request, "فعّل مربع «أفهم أن هذا الإجراء لا يُلغى».")
        return redirect(redirect_url)
    phrase = (request.POST.get("confirm_phrase") or "").strip()
    if phrase != "تفريغ قاعدة البيانات":
        messages.error(request, "اكتب عبارة التأكيد بالضبط كما في المربع الرمادي.")
        return redirect(redirect_url)
    preserve_keys = [
        k
        for k in PRESERVE_LABELS_AR
        if (request.POST.get(f"preserve_{k}") or "").strip() in ("1", "on", "true", "yes")
    ]
    try:
        result = wipe_runtime_tables(preserve_keys=preserve_keys)
    except NotImplementedError as e:
        messages.error(request, str(e))
        return redirect(redirect_url)
    except Exception:
        logger.exception("settings_database_wipe failed")
        messages.error(request, "حدث خطأ أثناء المسح. راجع سجلات الخادم.")
        return redirect(redirect_url)

    PosSettings.objects.get_or_create(pk=1)
    logger.warning(
        "database wipe completed user_id=%s tables_cleared=%s vendor=%s preserve=%s",
        getattr(request.user, "pk", None),
        result.get("tables_cleared"),
        result.get("vendor"),
        result.get("preserve_keys"),
    )
    kept = result.get("preserve_keys") or []
    kept_ar = "، ".join(PRESERVE_LABELS_AR[k] for k in kept) if kept else "لا شيء (تفريغ كامل للجداول التشغيلية)"
    messages.success(
        request,
        "تم تفريغ بيانات النظام التشغيلية. ما زال موجوداً: المستخدمون، إعدادات المقهى/النظام من هذه الصفحة، "
        "ومخطط Django (الهجرات، أنواع المحتوى، الصلاحيات). "
        f"عدد الجداول التي أُفرغت: {result['tables_cleared']}. "
        f"ما أُبقي من البيانات التشغيلية (حسب اختيارك): {kept_ar}. "
        "لإعادة بيانات تجربة شغّل من الطرفية: python manage.py seed_demo",
    )
    return redirect(redirect_url)


def _user_is_staff(user) -> bool:
    return user.is_authenticated and user.is_staff


@login_required
@user_passes_test(_user_is_staff)
@require_GET
def settings_database_export(request):
    """تنزيل نسخة من ملف SQLite الحالي."""
    from apps.core.database_backup import DatabaseBackupError, open_export_file

    redirect_url = reverse("shell:settings") + "?tab=database-backup"
    try:
        fh, filename = open_export_file()
    except NotImplementedError as e:
        messages.error(request, str(e))
        return redirect(redirect_url)
    except DatabaseBackupError as e:
        messages.error(request, str(e))
        return redirect(redirect_url)
    response = FileResponse(fh, as_attachment=True, filename=filename)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    logger.warning("sqlite database export user_id=%s", getattr(request.user, "pk", None))
    return response


@login_required
@user_passes_test(_user_is_staff)
@require_POST
def settings_database_import(request):
    """استبدال ملف SQLite بملف مرفوع بعد نسخ احتياطي."""
    from apps.core.database_backup import DatabaseBackupError, import_sqlite_database

    redirect_url = reverse("shell:settings") + "?tab=database-backup"
    if request.POST.get("accept_replace") != "1":
        messages.error(request, "فعّل مربع «أفهم أن البيانات الحالية ستُستبدَل».")
        return redirect(redirect_url)
    uploaded = request.FILES.get("database_file")
    if not uploaded:
        messages.error(request, "اختر ملف قاعدة البيانات (.sqlite3 أو .db).")
        return redirect(redirect_url)
    try:
        result = import_sqlite_database(uploaded)
    except NotImplementedError as e:
        messages.error(request, str(e))
        return redirect(redirect_url)
    except DatabaseBackupError as e:
        messages.error(request, str(e))
        return redirect(redirect_url)
    except Exception:
        logger.exception("settings_database_import failed")
        messages.error(request, "حدث خطأ أثناء الاستيراد. راجع سجلات الخادم.")
        return redirect(redirect_url)

    backup_note = ""
    if result.get("backup_created"):
        backup_note = f" تم حفظ نسخة من الملف السابق باسم: {result.get('backup_filename', '')}."
    messages.success(
        request,
        "تم استبدال قاعدة البيانات. أعد تحميل الصفحة أو سجّل الدخول من جديد إن لزم."
        + backup_note,
    )
    logger.warning(
        "sqlite database import user_id=%s backup=%s",
        getattr(request.user, "pk", None),
        result.get("backup_filename"),
    )
    return redirect(redirect_url)
