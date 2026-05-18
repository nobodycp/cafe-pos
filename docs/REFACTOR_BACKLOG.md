# Refactor backlog

Tracking safe structural refactors from the May 2026 technical audit. Business logic and user workflows must remain unchanged unless explicitly approved.

## Completed (phases 0–6)

### Phase 0 — Documentation

- [x] `docs/ARCHITECTURE.md` — routing, apps, services, shell vs POS, API section
- [x] `docs/REFACTOR_BACKLOG.md` — this file

### Phase 1 — Safe cleanup

- [x] Remove dead `classic_tpl` argument from shell template helpers
- [x] Shell templates for sale/purchase return forms, commission vendors
- [x] Removed unmounted legacy `apps/{billing,catalog,purchasing,inventory,accounting,reports}/urls.py` (no runtime `billing:`/`catalog:`/`purchasing:` references)
- [x] Fix multiline `{# … #}` comment leaks

### Phase 2 — Payment splits (server)

- [x] `apps/core/payment_splits.py` — `parse_payment_splits_json`
- [x] Refactor callers: POS, sale edit, purchase, expense, treasury
- [x] `apps/core/tests_payment_splits.py`

### Phase 3 — View thinning & service extraction

- [x] `apps/billing/sale_return_service.py` — `create_sale_return`, `parse_sale_return_lines_from_post`
- [x] `apps/purchasing/request_parsers.py` — purchase lines/payments/form state; POS imports decoupled from `purchasing.views`
- [x] `apps/reports/services.py` — dashboard, daily sales, payroll/expense reports, treasury audit helpers
- [x] `apps/catalog/views/` package — `products`, `panels`, `search_api`, `categories_units`, `_helpers`
- [x] `apps/pos/views/` package — `main`, `orders`, `checkout`, `search`, `tables`, `settings`, `_helpers`
- [x] `apps/purchasing/views/` package — `suppliers`, `invoices`, `returns`, `reports`, `panels`, `search_api`, `_helpers`
- [x] `apps/accounting/services.py` — `paginated_account_ledger_context`, `journal_line_delta`
- [x] `apps/payroll/services.py` — `record_employee_advance`, `record_employee_payout`, delete counterparts

### Phase 4 — API-ready layer

- [x] `apps/core/api/` — decorators, responses, search_handlers, views
- [x] `apps/core/shell_url_patterns_api.py` — `/app/api/v1/` read-only search + payment methods
- [x] `GET /app/api/v1/categories/search?q=`, `units/search`, `accounts/search`
- [x] Documented in `docs/ARCHITECTURE.md`

### Phase 5 — Frontend / JS

- [x] `static/js/payment_splits_ui.js` — shared `PaymentSplitsUI.bind`
- [x] `templates/expenses/_expense_payment_scripts.html`
- [x] `templates/purchasing/_purchase_invoice_scripts.html`
- [x] `templates/core/_treasury_voucher_form.html` (inline script uses shared module)
- [x] `templates/pos/main.html` checkout splits
- [x] `static/js/shell_init.js` — panel inject hooks + documentation

### Phase 6 — Error handling & config

- [x] `apps/core/exceptions.py` — `BusinessError`; used by `PaymentSplitsParseError`, `SaleReturnValidationError`
- [x] Production warning comment on `ALLOW_TEST_DATABASE_WIPE` in `config/settings.py`
- [x] `apps/contacts/tests/` discovery — removed conflicting empty `contacts/tests.py`

## Remaining (optional / lower priority)

| Item | Risk | Notes |
|------|------|-------|
| `billing.pos_bridge` for billing ↔ pos imports | Medium | Reduce circular imports |
| DRF or token auth for API | Low | Session JSON sufficient for now |

## Explicitly out of scope (unless requested)

- Changing journal posting rules, stock costing, or commission calculations
- Replacing Shell/POS with SPA in one pass
- Database schema changes for refactor convenience only

## Verification (May 2026)

```bash
python3 manage.py check
python3 manage.py test apps.accounting apps.purchasing apps.inventory apps.billing apps.expenses apps.core apps.payroll apps.catalog apps.contacts apps.pos apps.reports
```

Last run: **59 tests OK**, `manage.py check` clean.
