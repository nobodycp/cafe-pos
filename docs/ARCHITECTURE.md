# Architecture — cafe-pos (Django)

## Overview

Server-rendered Django 4.2 application for café POS and back-office. Two primary UI surfaces:

| Surface | URL prefix | Templates | Role |
|---------|------------|-----------|------|
| **Shell** | `/app/` | `templates/shell/` | Unified desktop admin: customers, suppliers, products, inventory, accounting, reports |
| **POS** | `/pos/` | `templates/pos/` | Cashier: cart, checkout, tables, session |
| **Session home** | `/` | `templates/core/` | Open/close work session (`apps.core.urls`) |

Business rules live in Django views + `services.py` modules; the browser uses form POST, partial HTML (panels), and JSON autocomplete endpoints. A thin **read-only API** under `/app/api/v1/` uses `JsonResponse` (no DRF).

## Repository layout

```
aboahamd/
├── config/           # settings, root urls.py, wsgi
├── apps/             # Django apps (domain modules)
├── templates/        # shell/, pos/, payroll/, expenses/, …
├── static/           # CSS + JS (shell panels, POS checkout, autocomplete)
├── manage.py
└── docs/             # This file and refactor backlog
```

## URL routing

Root: `config/urls.py`

```text
/admin/          → Django admin
/accounts/       → login / logout
/i18n/           → language
/app/            → apps.core.shell_urls  (namespace: shell)
/pos/            → apps.pos.urls         (namespace: pos)
/                → apps.core.urls        (namespace: core) — session open/close
```

All business routes for lists, forms, panels, and APIs used by Shell are declared in **`apps/core/shell_urls.py`** (plus `shell_url_patterns_accounting.py` for accounting subpaths, and `shell_url_patterns_api.py` for `/app/api/v1/`). Views are imported from domain apps (`billing.views`, `purchasing.views`, …) but URLs are centralized under `shell:*`.

Per-app `apps/*/urls.py` for billing, catalog, purchasing, inventory, accounting, and reports were **removed** (never mounted under `config/urls.py`). All shell routes live in `apps/core/shell_urls.py`.

POS-specific routes (cart, checkout, order, receipt) stay in **`apps/pos/urls.py`**.

## Django apps and responsibilities

| App | Responsibility |
|-----|----------------|
| **core** | Work sessions, POS settings, payment methods, treasury vouchers, shell chrome, pagination/list-filter helpers, sequences, audit |
| **pos** | Cashier UI, cart, checkout, tables, open orders |
| **billing** | Sale invoices, payments, returns, receipt/ESC-POS, invoice edit/resume |
| **catalog** | Products, categories, units, recipes / manufacturing |
| **inventory** | Stock movements, adjustments, stocktake, raw materials |
| **purchasing** | Suppliers, purchase invoices, returns, commission vendors |
| **contacts** | Customers, balances, statements, payments |
| **accounting** | Chart of accounts, journal entries, posting services |
| **expenses** | Expense categories and expenses |
| **payroll** | Employees, advances, payroll runs |
| **reports** | Operational and financial reports (shell pages) |

## Services layer

Domain logic that must stay consistent across views, signals, or commands lives in `services.py` (and related modules) per app, for example:

- `apps/accounting/services.py` — journal posting
- `apps/billing/services.py`, `tab_service.py`, `invoice_resume_service.py`
- `apps/inventory/services.py` — stock adjustments
- `apps/purchasing/services.py` — purchase posting
- `apps/core/services.py` — session lifecycle
- `apps/core/treasury_services.py` — voucher posting
- `apps/billing/sale_return_service.py` — sale return posting
- `apps/purchasing/request_parsers.py` — purchase invoice POST parsing (shared with POS)
- `apps/reports/services.py` — report query/build helpers
- `apps/core/payment_splits.py` — `parse_payment_splits_json`
- `apps/core/api/search_handlers.py` — shared customer/product/supplier search rows

Views should orchestrate HTTP (GET/POST, messages, redirects) and delegate calculations and DB side-effects to services.

### Catalog views package

`apps/catalog/views/` splits the former monolithic `views.py`:

| Module | Responsibility |
|--------|----------------|
| `_helpers.py` | Shared redirects/context, recipe row helpers |
| `products.py` | Product CRUD, recipes, manufacturing |
| `categories_units.py` | Categories and units CRUD |
| `search_api.py` | JSON search/quick-create endpoints |
| `panels.py` | Shell panel AJAX forms |

`apps/catalog/views/__init__.py` re-exports all symbols for `shell_urls`.

### POS views package

`apps/pos/views/` splits the former monolithic `views.py`:

| Module | Responsibility |
|--------|----------------|
| `_helpers.py` | Session order lookup, money parsing, receipt helpers, AJAX redirects |
| `main.py` | `pos_main`, cart/floor fragments, receipts, last-invoice panels |
| `orders.py` | Order lines, kitchen, hold/cancel, customer/discount |
| `checkout.py` | `order_checkout`, checkout payment parsing |
| `search.py` | Customers, products, payer hints |
| `tables.py` | Table open / quick-create |
| `settings.py` | Redirect POS settings to shell |

`apps/pos/views/__init__.py` re-exports for `pos/urls.py` and `shell_urls`.

### Purchasing views package

`apps/purchasing/views/` splits the former monolithic `views.py`:

| Module | Responsibility |
|--------|----------------|
| `_helpers.py` | Context/redirect helpers, opening balance, invoice detail helpers |
| `suppliers.py` | Supplier CRUD, payments, link customer |
| `invoices.py` | Purchase invoice create/list/detail/delete |
| `returns.py` | Purchase returns |
| `reports.py` | Statements, balances, commission vendors |
| `panels.py` | Shell panel AJAX forms |
| `search_api.py` | Product/unit/supplier search and quick-create JSON |

`apps/purchasing/views/__init__.py` re-exports for `shell_urls`. POST parsing uses `apps/purchasing/request_parsers.py`.

## Templates: Shell vs POS

- **Shell** extends `shell/base.html`, uses `pos-shell-page` layout, shared toolbar (`_shell_toolbar.html`), list filters (`_shell_list_filters.html` + `shell/list_filters/_*.html`), and AJAX panels (`shell/panels/*` + `static/js/shell_panel_modal.js`).
- **POS** extends `pos/main.html` (single large page with cart fragment refresh). Printing/preview uses in-page iframe overlay, not new tabs.
- Some partials remain outside `shell/` (e.g. `contacts/_customers_list_body.html`, `payroll/*.html`) when embedded in shell pages.

## Cross-app coupling (intentional today)

- **billing ↔ pos** — orders, tabs, checkout, invoice resume.
- **core** — treasury touches contacts, purchasing, expenses, payroll.
- **pos → purchasing** — shared payment-method rows / purchase form state (candidate for a small shared module later).

## HTTP API (`/app/api/v1/`)

Mounted from `shell_urls` → `shell_url_patterns_api.py`. Session auth via `@api_login_required` (`apps/core/api/decorators.py`); responses use `json_ok` / `json_error` (`apps/core/api/responses.py`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/app/api/v1/customers/search?q=` | Active customers (same rows as `pos:customers_search`) |
| GET | `/app/api/v1/products/search?q=` | Sale products (same query as `pos:products_search`) |
| GET | `/app/api/v1/suppliers/search?q=` | Active suppliers (same as `shell:purchase_suppliers_search`) |
| GET | `/app/api/v1/categories/search?q=` | Active categories (`shell:category_search`) |
| GET | `/app/api/v1/units/search?q=` | Units by Arabic name (`shell:purchase_units_search`) |
| GET | `/app/api/v1/accounts/search?q=` | Chart accounts by code/name (journal forms) |
| GET | `/app/api/v1/payment-methods/` | `load_payment_method_rows()` JSON |

Example: `GET /app/api/v1/customers/search?q=أحمد` → `{"ok": true, "data": {"results": [...]}}`.

Unauthenticated requests return `401` with `{"ok": false, "error": "...", "code": "AUTH_REQUIRED"}`.

## Client JS (shared)

| File | Role |
|------|------|
| `static/js/payment_splits_ui.js` | Split-pay UI: `PaymentSplitsUI.bind` — expenses, purchase invoice, treasury voucher, POS checkout |
| `static/js/shell_init.js` | Post-panel-inject hooks; documents panel/pagination scripts in `base.html` |
| `static/js/shell_panel_modal.js` | Shell AJAX panels |
| `static/js/shell_pagination_spa.js` | List pagination without full reload |

## Future API expansion

1. Add write endpoints only behind existing services (checkout, purchase post, etc.).
2. Introduce stable **DTOs** per domain where responses grow.
3. Optional token auth alongside session for desktop clients.

## Tests

After business-logic changes, run:

```bash
python3 manage.py test apps.accounting apps.purchasing apps.inventory apps.billing apps.expenses apps.core apps.payroll apps.catalog apps.contacts apps.pos apps.reports
```
