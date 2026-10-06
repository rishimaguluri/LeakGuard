# Data dictionary

Every canonical field LeakGuard stores, its type, and where it usually comes from.

Conventions:
- **Money** is stored as integer cents (`_cents` columns) plus a 3 letter `currency`. Raw values like `$1,234.56`, `(12.00)` or `12.00-` are parsed on import.
- **Dates** are ISO dates. Raw dates in most common formats are accepted (see `docs/ADDING_A_SOURCE.md`).
- **Datetimes** are UTC. Each property stores its own timezone.
- **Confirmation numbers** are normalized on both sides of every join: uppercase, no spaces or punctuation, known prefixes (EXP, BDC, ...) and leading zeros removed.
- **Cards**: only the last 4 digits are ever stored. Full card numbers are dropped on import with a warning.
- Every table carries `portfolio_id`.

In a mapping YAML, money fields are named without `_cents` (for example `vcc_amount`), and `guest_name` (full name) may be mapped instead of `guest_last_name`.

## portfolios

| Field | Type | Notes |
|---|---|---|
| slug | text | Folder name under `data/raw/`. From `config/portfolios.yaml` |
| name | text | Shown in the app and reports |
| owner_name | text | |
| owner_type | enum | pe_fund, reit, family_office, independent |

## management_companies

| Field | Type | Notes |
|---|---|---|
| name | text | From `config/portfolios.yaml` |

## properties

| Field | Type | Notes |
|---|---|---|
| code | text | Folder name under the portfolio folder, for example `CMH01` |
| name, brand, city, state | text | Property basics |
| segment | enum | select_service, full_service |
| room_count | int | |
| management_company_id | ref | |
| pms_name | text | For reference only. The mapping is chosen by the file's headers |
| timezone | text | IANA name, for example `America/Chicago` |

## source_files

One row per imported file.

| Field | Type | Notes |
|---|---|---|
| source_name | text | Folder name: pms_reservations, pms_payments, expedia_vcc, booking_vcc, processor_settlement |
| mapping_name | text | Mapping YAML used |
| file_name, relative_path | text | |
| file_hash | text | SHA-256 of the file bytes. Same hash means the same file, so it is skipped |
| status | enum | imported, skipped, failed |
| message | text | Why a file was skipped or failed |
| rows_read, rows_imported, rows_rejected | int | |
| date_min, date_max | date | Date range the file covers |
| imported_at | datetime | UTC |
| data_mode | enum | demo, real |

## reservations (PMS reservation export)

| Field | Type | Required | Usual raw source |
|---|---|---|---|
| pms_confirmation_no | confirmation | yes | PMS confirmation number |
| ota_confirmation_no | confirmation | | External or OTA reference column |
| channel | enum | | Source or channel code. expedia, booking, direct, other |
| payment_model | enum | | Rate plan or payment method. ota_collect, hotel_collect, unknown |
| guest_last_name | text | | Guest name. Hashed when `hash_guest_names` is on |
| arrival_date, departure_date | date | yes | |
| nights | int | | Calculated when missing |
| status | enum | yes | in_house, checked_out, cancelled, no_show, reserved |
| room_revenue_cents, tax_cents, folio_total_cents | money | | |
| currency | text | | Defaults to USD |

## pms_payments (PMS payment postings or ledger)

| Field | Type | Required | Usual raw source |
|---|---|---|---|
| pms_confirmation_no | confirmation | yes | Confirmation or folio number |
| posting_date | date | yes | Transaction date |
| payment_type | text | | Payment method or description, for example "Virtual Card - Expedia" |
| card_last4 | last 4 | | Masked card column. Full numbers are cut to last 4 with a warning |
| amount_cents | money | yes | Stored negative for reversals |
| is_reversal | bool | | From a reversal flag, or set when the amount is negative |
| currency | text | | |

## vcc_records (Expedia and Booking.com virtual card reports)

| Field | Type | Required | Usual raw source |
|---|---|---|---|
| ota | enum | yes | Usually set by the mapping default (expedia or booking) |
| ota_confirmation_no | confirmation | yes | Reservation ID or reservation number |
| card_last4 | last 4 | | Card number column |
| vcc_amount_cents | money | yes | Amount loaded on the card |
| activation_date | date | | Card active from date |
| expiry_date | date | yes | Card expiry date |
| ota_status | text | | Card or payment status as shown by the OTA |
| guest_last_name | text | | Guest or booker name. Used by the fuzzy fallback |
| arrival_date | date | | Check-in date. Used by the fuzzy fallback |
| currency | text | | |

## processor_transactions (card processor or gateway settlement)

| Field | Type | Required | Usual raw source |
|---|---|---|---|
| transaction_date | date | yes | |
| settlement_date | date | | Funding or settlement date |
| card_last4 | last 4 | yes | Masked card number |
| amount_cents | money | yes | Sales positive, refunds and chargebacks negative |
| transaction_type | enum | | sale, refund, void, chargeback. Defaults to sale |
| auth_code | text | | |
| reference | confirmation | | Often the PMS confirmation or folio number |
| currency | text | | |

## exceptions (one row per virtual card)

Every card gets a row so coverage and volume can be measured. Rows with `is_exception = false` (CHARGED_OK, NOT_YET_DUE) are not problems.

| Field | Type | Notes |
|---|---|---|
| vcc_key | text | `ota|confirmation|last4`. With property, the stable identity across matching runs |
| exception_type | enum | UNCHARGED, EXPIRED_UNCHARGED, UNDERCHARGED, OVERCHARGED, CHARGED_NOT_SETTLED, NO_PMS_MATCH, CANCELLED_REVIEW, DUPLICATE_VCC, CHARGED_OK, NOT_YET_DUE |
| is_exception | bool | |
| match_method | enum | exact, fuzzy, none |
| match_confirmed | bool | A person confirmed a fuzzy match. Required before recording recovery |
| confidence | enum | high, medium (for example last 4 missing on PMS postings), low |
| rule | text | Plain-language reason the card was flagged |
| amount_at_risk_cents | money | See SPEC section 7 table |
| expected_cents, charged_cents, settled_cents | money | settled is empty when no processor data covers the charge |
| arrival_date, departure_date | date | From the matched reservation |
| due_date | date | When the money became collectible: departure for stays, arrival for cancellations, activation otherwise. Drives monthly trends |
| expiry_date, days_to_expiry | date, int | |
| priority_score | number | See `matching/priority.py` |
| status | enum | open, assigned, in_progress, recovered, written_off, not_an_issue |
| assigned_to, notes | text | |
| recovered_cents, recovered_date | money, date | |
| is_active | bool | False when the card no longer appears in the imported data |
| first_detected_at, last_seen_at | datetime | UTC |

## exception_events (audit log)

| Field | Type | Notes |
|---|---|---|
| event_type | text | detected, type_changed, assigned, status_changed, recovered, note, match_confirmed, auto_resolved |
| old_value, new_value | text | |
| user | text | |
| timestamp | datetime | UTC |
| note | text | |

## import_issues

| Field | Type | Notes |
|---|---|---|
| source_file_id | ref | |
| row_number | int | Row number in the original file, counting the header row as 1 |
| severity | enum | error (row rejected) or warning |
| field | text | Canonical field, when known |
| message | text | |
