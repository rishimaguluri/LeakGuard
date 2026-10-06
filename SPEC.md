# LeakGuard v1 Build Spec

---

## 1. What LeakGuard is

LeakGuard finds hotel revenue that was earned but never collected, then helps staff collect it and shows the owner the results across the whole portfolio.

v1 focuses on OTA virtual cards (VCCs). For prepaid bookings, Expedia (Expedia Collect) and Booking.com (Payments by Booking.com) send the hotel a one-time virtual card. Hotels often:
- never charge it (it expires and the money is hard or impossible to get back)
- charge the wrong amount
- charge it in the PMS but the payment never settles with the card processor

**Who buys it:** hotel owners and asset managers (PE funds, REITs, family offices) with portfolios of hotels run by one or more management companies. Not individual GMs.

**Business model:** free 12-month look-back audit, then 20% of recovered cash (configurable).

**Our edge vs. competitors like x·quic (build with this in mind):**
- Owner-level portfolio view across every hotel and management company
- Operator scorecard ranking management companies by leakage
- Recovered cash shown as NOI impact, the language owners and lenders use
- Fast onboarding: drop in a hotel's exports and get an audit in a day

**Principle:** matching is deterministic, rule-based and auditable. No LLM calls in v1. A person reviews every exception before money moves. Never overstate what the software does in UI copy.

---

## 2. The most important design requirement: demo data now, real data instantly

The app must run fully on realistic demo data today, and switch to real hotel data by dropping files into a folder and running one command. No code changes to switch.

How this works:

1. **One pipeline for both.** Demo data is generated as fake *raw export files* that look like real PMS, OTA and processor exports (CSV and XLSX, messy column names, date formats, extra columns). They go through the exact same import, mapping, validation and matching pipeline as real files. Never write demo data straight into the database.

2. **Mapping files decide formats.** Each source format has a YAML mapping in `mappings/` that maps its columns to our canonical schema. A new PMS or OTA format means a new YAML file, not new code.

3. **Data modes.** `config/settings.yaml` has `data_mode: demo | real`.
   - demo reads from `data/demo_raw/` (committed to git, fake only)
   - real reads from `data/raw/` (gitignored, never committed)
   - Each mode uses its own database file (`data/leakguard_demo.db`, `data/leakguard_real.db`) so they never mix.
   - The UI shows a clear badge on every page: "Demo data" or "Live data: <portfolio name>".

4. **Folder convention for real data:**
   ```
   data/raw/<portfolio_slug>/<property_code>/<source_name>/<any files>
   ```
   Example: `data/raw/acme_hotels/CMH01/expedia_vcc/2025-10_to_2026-09.csv`
   The importer walks this tree, finds the mapping for each `source_name`, and imports every file.

5. **Mapping wizard.** When a real export has a format we have never seen, I can upload it in the app, see auto-suggested column matches (by header name similarity and sample values), fix them, preview the parsed rows, and save a new mapping YAML. Then import runs normally.

6. **Idempotent imports.** Store a hash of every imported file. Re-importing the same file never creates duplicates. Re-importing a corrected file replaces the old rows from that file.

7. **One command each:**
   - `python -m leakguard demo` : generate demo files, import, match, ready to view
   - `python -m leakguard import` : import everything in the current mode's raw folder
   - `python -m leakguard match` : run matching and refresh exceptions
   - `python -m leakguard report --portfolio <slug>` : export the audit report
   - `python -m leakguard reset --mode demo|real` : wipe that mode's database (ask for confirmation)
   - `streamlit run app/Home.py` : open the dashboard

---

## 3. Security and data handling (non-negotiable)

- **Never store full card numbers, CVVs, or full expiry dates tied to a card number.** On ingest, keep only the last 4 digits. If a file contains a full card number column, drop it before anything is written to disk or the database, and log a warning ("Full card numbers found in <file>. Dropped. Ask the hotel to send masked exports.").
- Add a scanner that rejects any value matching a 13 to 19 digit card pattern in any column that is not explicitly mapped as last4.
- `.gitignore` from the first commit: `data/raw/`, `data/*.db`, `data/processed/`, `exports/`, `.env`, `.streamlit/secrets.toml`, `.venv/`.
- No real hotel, guest or owner names in code, tests, or commit messages.
- Guest names: store last name only, needed for fuzzy matching. Add a setting to hash guest names in real mode.
- Simple password gate on the Streamlit app (from `.streamlit/secrets.toml`) so I can share a screen or a deployed link in pilot meetings later.

---

## 4. Stack and project structure

- Python 3.12
- pandas, openpyxl (Excel read/write), SQLAlchemy 2.x with SQLite (structured so switching to Postgres is a connection string change)
- pydantic v2 for row validation
- PyYAML for mappings and settings
- rapidfuzz for header matching in the mapping wizard and fuzzy name matching
- Streamlit (multipage) + plotly for charts
- reportlab for the PDF report (phase 6)
- pytest for tests, ruff for linting
- `requirements.txt`, `Makefile` (or `tasks.py` if on Windows) with shortcuts for the commands above

Use this structure (adjust only if you explain why):

```
leakguard/
  CLAUDE.md
  SPEC.md
  README.md
  requirements.txt
  config/
    settings.yaml            # data_mode, fee_pct, tolerances, expiry windows, timezone
    portfolios.yaml          # portfolios, owners, management companies, properties
  mappings/
    pms_generic.yaml
    pms_opera_demo.yaml
    expedia_vcc_demo.yaml
    booking_payments_demo.yaml
    processor_settlement_demo.yaml
  data/
    demo_raw/                # committed, fake only
    raw/                     # gitignored, real exports
  src/leakguard/
    __main__.py              # CLI entry point
    config.py
    db.py
    models.py                # SQLAlchemy tables
    schemas.py               # pydantic canonical row schemas
    ingest/
      discover.py            # walk data folders, find files and sources
      readers.py             # csv/xlsx readers, encoding and delimiter detection
      mapping.py             # apply YAML mappings, transforms
      normalize.py           # confirmation numbers, dates, money, names
      validate.py            # row validation, card number scanner
      importer.py            # orchestration, file hashing, import reports
    matching/
      rules.py               # classification rules, one function per rule
      engine.py              # joins, fuzzy fallback, writes exceptions
      priority.py            # priority score
    recovery/
      workflow.py            # status changes, assignment, audit log
    reporting/
      metrics.py             # all dashboard numbers come from here
      excel_report.py
      pdf_report.py
    demo/
      generator.py           # fake portfolio and fake raw export files
  app/
    Home.py
    pages/
      1_Portfolio.py
      2_Operator_Scorecard.py
      3_Hotel_Detail.py
      4_Exception_Queue.py
      5_Recovery_Tracker.py
      6_Audit_Report.py
      7_Data_Sources.py
      8_Mapping_Wizard.py
      9_Settings.py
  tests/
  docs/
    DATA_REQUEST.md
    DATA_DICTIONARY.md
```

---

## 5. Canonical data model

All money stored as integer cents with a currency code. All dates as ISO dates; datetimes in UTC with property timezone stored on the property. Every table has `portfolio_id` so multiple owners can live in one database later.

**portfolios:** id, slug, name, owner_name, owner_type (pe_fund / reit / family_office / independent)

**management_companies:** id, portfolio_id, name

**properties:** id, portfolio_id, management_company_id, code, name, brand, city, state, room_count, pms_name, timezone

**source_files:** id, portfolio_id, property_id, source_name, mapping_name, file_name, file_hash, rows_read, rows_imported, rows_rejected, date_min, date_max, imported_at, data_mode

**reservations** (from PMS): id, property_id, pms_confirmation_no, ota_confirmation_no, channel (expedia / booking / direct / other), payment_model (ota_collect / hotel_collect / unknown), guest_last_name, arrival_date, departure_date, nights, status (in_house / checked_out / cancelled / no_show / reserved), room_revenue_cents, tax_cents, folio_total_cents, currency, source_file_id

**pms_payments** (PMS payment postings): id, property_id, pms_confirmation_no, posting_date, payment_type, card_last4, amount_cents, currency, is_reversal, source_file_id

**vcc_records** (from OTA reports): id, property_id, ota, ota_confirmation_no, card_last4, vcc_amount_cents, currency, activation_date, expiry_date, ota_status, source_file_id

**processor_transactions** (from card processor / gateway settlement): id, property_id, transaction_date, settlement_date, card_last4, amount_cents, currency, transaction_type (sale / refund / void / chargeback), auth_code, reference (often the PMS confirmation or folio number), source_file_id

**exceptions:** id, portfolio_id, property_id, ota, ota_confirmation_no, pms_confirmation_no, exception_type, match_method (exact / fuzzy / none), amount_at_risk_cents, expected_cents, charged_cents, settled_cents, expiry_date, days_to_expiry, priority_score, status (open / assigned / in_progress / recovered / written_off / not_an_issue), assigned_to, recovered_cents, recovered_date, notes, first_detected_at, last_seen_at

**exception_events** (audit log): id, exception_id, event_type, old_value, new_value, user, timestamp, note

**import_issues:** id, source_file_id, row_number, severity (error / warning), field, message

Write `docs/DATA_DICTIONARY.md` describing every canonical field, its type, and which raw sources usually supply it.

---

## 6. Ingest pipeline

**Mapping YAML format** (design it cleanly; this is a sketch):

```yaml
name: expedia_vcc_demo
source_name: expedia_vcc
target: vcc_records
file_types: [csv, xlsx]
header_row: auto            # detect the header row if there are title rows above it
columns:
  ota_confirmation_no: ["Reservation ID", "Itinerary Number"]
  vcc_amount: ["Card Amount", "Amount to Charge"]
  currency: ["Currency"]
  activation_date: ["Activation Date", "Card Active From"]
  expiry_date: ["Card Expiry", "Expiration Date"]
  card_last4: ["Card Last 4", "Card Number"]   # if full number, keep last 4 and drop the rest
defaults:
  ota: expedia
transforms:
  vcc_amount: money
  activation_date: date
  expiry_date: date
  ota_confirmation_no: confirmation
notes: "PLACEHOLDER mapping built for demo files. Confirm against a real export."
```

Requirements:
- Each canonical field can list several possible header names. Match case-insensitively and ignore spaces and punctuation.
- Transforms: money (handles $, commas, parentheses for negatives, trailing minus), date (tries common formats, warns on ambiguous day/month), confirmation (uppercase, strip spaces, dashes, common prefixes, leading zeros), status (maps raw status words to our enum via a lookup table in the YAML), last4.
- Readers handle CSV with unknown delimiter and encoding, XLSX with multiple sheets (mapping can name the sheet), and title rows above the header.
- Every rejected row goes to `import_issues` with the row number and reason.
- After import, print and store an import report: rows read, imported, rejected, date range covered, top 5 issues.
- PDFs are out of scope for v1. If a hotel only has PDFs, the Data Sources page should say so and suggest asking for CSV/XLSX.

---

## 7. Matching engine

Run per property. All tolerances and windows come from `config/settings.yaml`.

**Step 1: link VCCs to reservations**
1. Exact: normalized `ota_confirmation_no` on both sides + property.
2. Fuzzy fallback: same property, guest last name similarity of 90 or higher, arrival date within 1 day, and amount within 10%. Mark `match_method = fuzzy`; fuzzy matches always need human review before recovery.
3. No match: `NO_PMS_MATCH`.

**Step 2: link charges**
- Charged amount = sum of PMS payment postings on that reservation where card_last4 equals the VCC last4 (minus reversals). If last4 is missing, use payments with payment_type that looks like a virtual card and flag lower confidence.
- Settled amount = matching processor transactions by last4 + amount + date window (plus reference if available).

**Step 3: classify each VCC**

| exception_type | Rule | amount_at_risk |
|---|---|---|
| CHARGED_OK | charged and settled within tolerance ($1.00 default) of the VCC amount | 0 (not an exception, but stored for coverage stats) |
| UNCHARGED | guest stayed (checked_out or in_house past arrival), VCC not expired, nothing charged | VCC amount |
| EXPIRED_UNCHARGED | VCC past expiry, nothing charged | VCC amount (recovery needs an OTA claim, lower probability) |
| UNDERCHARGED | charged less than VCC amount minus tolerance | the difference |
| OVERCHARGED | charged more than VCC amount plus tolerance | the overage (chargeback risk) |
| CHARGED_NOT_SETTLED | PMS shows the charge but no matching processor settlement | charged amount |
| NO_PMS_MATCH | VCC with no reservation found | VCC amount (needs investigation) |
| CANCELLED_REVIEW | reservation cancelled or no-show but VCC exists and may cover a fee | VCC amount, flagged for review |
| DUPLICATE_VCC | more than one VCC for the same reservation | review |

Also produce an `UPCOMING_EXPIRY` alert list: VCCs not yet charged that expire within N days (default 7), even if the guest has not checked out yet. Prevention is part of the product.

**Step 4: priority score**
One function in `priority.py`. Higher amount and fewer days to expiry means higher priority. Expired items rank by amount but below active ones of similar value. Document the formula in a docstring and test it.

**Step 5: keep exceptions stable across runs**
Re-running matching must update existing exceptions (same property + OTA confirmation + type), not create new ones, and must never overwrite a status a person set (recovered, written off, not an issue). If an exception disappears because the card was later charged, mark it `recovered` automatically with `recovered_cents` and an audit event "auto-resolved: charge detected".

**Tests:** every rule gets tests for the normal case and edge cases: partial charges across multiple postings, reversals, multi-currency, cancelled then rebooked, duplicate confirmations, missing last4, expiry today, dates in different formats, fuzzy match that should NOT match.

---

## 8. Demo data generator

`src/leakguard/demo/generator.py` creates a realistic fake portfolio and writes fake *raw files* into `data/demo_raw/` using the same folder convention as real data.

- 1 portfolio ("Summit Ridge Capital", a fictional PE fund), 2 management companies ("Lakeview Hospitality", "Northpoint Hotel Group"), 8 properties (mix of select-service and full-service, 90 to 350 rooms, made-up brand names, US cities).
- 12 months of reservations with realistic patterns: seasonality, weekday/weekend mix, OTA share of 25 to 45%, Expedia vs Booking split around 55/45, average daily rates by property type.
- VCCs for OTA-collect bookings with activation on or near check-in and expiry 30 to 180 days later.
- Leak injection, with one management company leaking noticeably more than the other so the scorecard tells a story:
  - about 2 to 4% of VCCs uncharged (some expired)
  - about 1% undercharged
  - about 0.3% overcharged
  - about 0.5% charged in PMS but not settled
  - a few no-match and duplicate cases
- Messy realism: different column names per source, a title row above the header in one file, mixed date formats, a few malformed rows that should be rejected, one file with a full card number column (to prove the scanner drops it).
- Deterministic with a seed so tests are stable.
- Also writes a "fake real hotel" folder in a separate test fixture to prove that dropping files into `data/raw/` works end to end.

---

## 9. Dashboard (Streamlit)

**Look and feel (match our one-pager):** page background pale blue `#EDF2F8`, cards `#F8FAFD`, text deep navy `#0E2240`, secondary text `#25406A`, accent navy `#1A3766`, lines `#BFD0E6`, losses in deep berry `#8C2B45`. No gold, no teal, no grey text. Clean, readable, minimum 14px body text. Put the theme in `.streamlit/config.toml` plus a small CSS file. Sentence-case headings, plain wording, no em dashes.

Global sidebar: data mode badge, portfolio selector, date range, management company filter, property filter.

1. **Home:** what LeakGuard found in one screen: total leaked, recovered to date, open dollars at risk, cards expiring in 7 days, and a short "what to do next" list.
2. **Portfolio:** leakage by property and by exception type, trend by month, recovery rate, annualized NOI impact (recovered cash annualized), our fee, owner net gain.
3. **Operator scorecard:** management companies ranked by leak rate (leaked dollars / OTA-collect revenue), dollars at risk, average days to resolve, share of expired cards. This page is our differentiator; make it sharp.
4. **Hotel detail:** one property: KPIs, exception list, monthly trend, data coverage.
5. **Exception queue:** filterable, sortable by priority. Each row expands to show the evidence: VCC details, matched reservation, PMS postings, processor transactions, and why it was flagged. Actions: assign, change status, record recovered amount, add note. Every action writes to the audit log. Bulk export to CSV for property staff.
6. **Recovery tracker:** recovered over time, by type and property, fee earned, open pipeline.
7. **Audit report:** choose portfolio and period, preview, export Excel (phase 5) and PDF (phase 6).
8. **Data sources:** every imported file with rows, rejects, date range, coverage gaps by property and source (for example "CMH01 has PMS data through Sept but Expedia data only through July"), import issues, and a checklist of which required sources are missing per property.
9. **Mapping wizard:** upload a file, auto-suggest mappings, edit, preview 20 parsed rows, save YAML, import.
10. **Settings:** fee percentage, tolerance, upcoming-expiry window, fuzzy thresholds. Saved to `config/settings.yaml`.

All numbers on every page come from `reporting/metrics.py` so the dashboard, Excel and PDF always agree. Add tests for metrics.

---

## 10. Audit report (this is what I hand owners)

Excel workbook:
- **Summary:** portfolio, period, total VCC volume reviewed, dollars leaked by type, recoverable estimate, our fee at 20%, owner net, NOI impact
- **By property** and **by management company**
- **Line items:** every exception with evidence fields, sorted by priority
- **Method:** plain-language explanation of what was checked and the limits (data coverage, fuzzy matches need review, expired cards may not be recoverable)

PDF: a 2 to 3 page branded version of the summary using the same colors as the dashboard.

Never claim recovery is guaranteed. Label estimates as estimates.

---

## 11. Docs to write

- `README.md`: setup on Mac and Windows, every command, how to switch to real data in 3 steps.
- `docs/DATA_REQUEST.md`: the list of files I need from a pilot hotel (see section 12), written so I can send it directly to a hotel controller.
- `docs/DATA_DICTIONARY.md`: canonical fields.
- `docs/ADDING_A_SOURCE.md`: how to add a new PMS or OTA format with the mapping wizard or by hand.

---

## 12. Data I will request from pilot hotels (build the pipeline to accept these)

For each property, 12 months (13 to 18 preferred), CSV or Excel, with card numbers masked:

1. **PMS reservation export:** PMS confirmation #, OTA confirmation #, channel/source, rate plan or payment type (OTA collect vs hotel collect), guest last name, arrival, departure, status (checked out / cancelled / no-show), room revenue, tax, folio total.
2. **PMS payment postings / ledger:** confirmation or folio #, posting date, payment type, amount, card last 4, reversals or adjustments.
3. **Expedia virtual card report** (Expedia Collect bookings): reservation ID, card amount, activation date, expiry date, card last 4, charge status if shown.
4. **Booking.com virtual card / payments report** (Payments by Booking.com): reservation number, card amount, activation date, expiry date, card last 4, status.
5. **Card processor or gateway settlement report:** transaction date, settlement date, amount, card last 4, transaction type, auth code, reference or folio #.
6. **Property basics:** name, brand, room count, management company, PMS name and version, which OTAs are used, card processor name.

Later phases (not v1, but keep the model ready): OTA commission statements/invoices, chargeback reports, cancellation and no-show policies by rate plan.

Report names differ by PMS and by OTA extranet. The mapping system must not assume exact names.

---

## 13. Build phases and definition of done

**Phase 0: plan and skeleton.** CLAUDE.md, repo structure, requirements, .gitignore, settings and portfolios YAML, empty modules, `pytest` and `ruff` running. Done when: tests run green and README explains setup.

**Phase 1: data model + demo generator.** Tables, schemas, generator writing raw demo files. Done when: `python -m leakguard demo` creates files in `data/demo_raw/` with the planned leak rates.

**Phase 2: ingest.** Readers, mappings, normalizers, validation, card scanner, file hashing, import reports. Done when: demo files import with expected reject counts, the full card number column is dropped with a warning, and re-import creates no duplicates.

**Phase 3: matching.** Rules, engine, priority, stable exceptions, upcoming expiry. Done when: injected leaks in demo data are found at the expected counts (assert in tests) and re-running match does not change human-set statuses.

**Phase 4: dashboard.** All pages except PDF, theme applied, password gate. Done when: I can click through every page on demo data and the numbers agree with `metrics.py` tests.

**Phase 5: recovery workflow + Excel audit report.** Done when: I can work an exception from open to recovered, the audit log shows each step, and the Excel report opens cleanly.

**Phase 6: real data readiness.** Mapping wizard, Data Sources coverage checks, `docs/DATA_REQUEST.md`, the end-to-end test that drops "fake real" files into `data/raw/`, switches mode, and produces a report. Done when: switching to real mode with new files needs zero code changes.

**Phase 7: PDF report and polish.**

---

## 14. How to work with me

- Before each change, say what you are about to do and why, in plain language. I am technical (Finance + CS) but want short explanations.
- Keep functions small with clear names. Prefer boring, readable code over clever abstractions.
- Type hints everywhere. Docstrings on anything non-obvious.
- Run `pytest` and `ruff` after every meaningful change and report results.
- Commit at the end of each phase with a clear message. Do not push unless I ask.
- If something in this spec is wrong, risky, or there is a simpler way, say so and propose it instead of following blindly.
- Do not invent facts about specific PMS or OTA report formats. Where you are unsure, build it as a configurable mapping and mark it as a placeholder.
- No em dashes anywhere: code comments, UI text, docs, commit messages.
