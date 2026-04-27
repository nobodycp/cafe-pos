#!/usr/bin/env bash
# ══════════════════════════════════════════════════
#  تحديث النظام من GitHub + إعادة تشغيل الخدمة
#  bash update.sh
# ══════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
fail()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

[ -f "manage.py" ] || fail "شغّل هذا السكريبت من داخل مجلد المشروع: cd cafe-pos && bash update.sh"

info "جاري سحب آخر التحديثات..."
git pull --ff-only || fail "فشل التحديث — تحقق من وجود تعديلات محلية"
ok "الكود محدّث"

source .venv/bin/activate 2>/dev/null || fail "البيئة الافتراضية غير موجودة — أعد التثبيت"

info "تحديث المكتبات..."
pip install -r requirements.txt -q
ok "المكتبات محدّثة"

info "تطبيق تغييرات قاعدة البيانات..."
python3 manage.py migrate -v 0
ok "قاعدة البيانات محدّثة"

info "تحديث الملفات الثابتة..."
python3 manage.py collectstatic --noinput -v 0 2>/dev/null || true
ok "الملفات الثابتة محدّثة"

# إعادة تشغيل الخدمة إن كانت موجودة
SERVICE_NAME="cafe-pos"
if command -v systemctl >/dev/null 2>&1 && systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1; then
    info "إعادة تشغيل الخدمة..."
    if [ "$(id -u)" = "0" ]; then
        systemctl restart "$SERVICE_NAME"
    else
        sudo systemctl restart "$SERVICE_NAME" 2>/dev/null || warn "لم يتم إعادة التشغيل — شغّل: sudo systemctl restart $SERVICE_NAME"
    fi
    ok "الخدمة أُعيد تشغيلها"
else
    warn "الخدمة غير نشطة — أعد التشغيل يدوياً"
fi

echo ""
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓ التحديث اكتمل بنجاح!                    ${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo ""
