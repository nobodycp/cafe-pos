#!/usr/bin/env bash
# ──────────────────────────────────────────────────
#  نظام نقاط البيع — تثبيت بأمر واحد
#  bash <(curl -sL URL_HERE)   أو   bash install.sh
# ──────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
fail()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

REPO="https://github.com/nobodycp/cafe-pos.git"
DIR="cafe-pos"
PORT="${PORT:-8000}"

# ── 1. Prerequisites ──
info "التحقق من المتطلبات..."
command -v python3 >/dev/null 2>&1 || fail "Python 3 غير مثبّت. ثبّته أولاً."
command -v git     >/dev/null 2>&1 || fail "Git غير مثبّت. ثبّته أولاً."

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "Python $PY_VER"

# ── 2. Clone / Pull ──
if [ -d "$DIR" ]; then
    info "المجلد موجود — جاري التحديث..."
    cd "$DIR"
    git pull --ff-only || { warn "تعارض — جاري إعادة التحميل..."; cd ..; rm -rf "$DIR"; git clone "$REPO" "$DIR"; cd "$DIR"; }
else
    info "جاري تحميل المشروع..."
    git clone "$REPO" "$DIR"
    cd "$DIR"
fi
ok "الكود جاهز"

# ── 3. Virtual environment ──
if [ ! -d ".venv" ]; then
    info "إنشاء بيئة افتراضية..."
    python3 -m venv .venv
fi
source .venv/bin/activate
ok "البيئة الافتراضية مفعّلة"

# ── 4. Dependencies ──
info "تثبيت المكتبات..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "المكتبات مثبّتة"

# ── 5. Environment file ──
if [ ! -f ".env" ]; then
    info "إنشاء ملف الإعدادات..."
    SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')
    cat > .env <<EOF
DEBUG=True
SECRET_KEY=$SECRET
CAFE_NAME_AR=مقهى النموذج
CAFE_NAME_EN=Demo Café
EOF
    ok "تم إنشاء .env — عدّله حسب بياناتك"
else
    ok "ملف .env موجود"
fi

# ── 6. Database ──
info "تطبيق قاعدة البيانات..."
python3 manage.py migrate --run-syncdb -v 0
ok "قاعدة البيانات جاهزة"

# ── 7. Demo data ──
info "تحميل البيانات التجريبية..."
python3 manage.py seed_demo
ok "البيانات التجريبية جاهزة"

# ── 8. Collect static ──
info "تجميع الملفات الثابتة..."
python3 manage.py collectstatic --noinput -v 0 2>/dev/null || true
ok "الملفات الثابتة جاهزة"

# ── 9. Done ──
echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓ التثبيت اكتمل بنجاح!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}تشغيل السيرفر:${NC}"
echo -e "    cd $DIR && source .venv/bin/activate"
echo -e "    python3 manage.py runserver 0.0.0.0:$PORT"
echo ""
echo -e "  ${CYAN}الرابط:${NC}        http://localhost:$PORT"
echo -e "  ${CYAN}اسم المستخدم:${NC}  admin"
echo -e "  ${CYAN}كلمة المرور:${NC}   admin123"
echo ""
echo -e "  ${YELLOW}ملاحظة: غيّر كلمة المرور في الإنتاج!${NC}"
echo ""
