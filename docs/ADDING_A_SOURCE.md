# Adding a new PMS, OTA or processor format

LeakGuard never hard-codes a report layout. Each format is described by a YAML file in `mappings/`. A new format means a new YAML file, never a code change.

## How a file finds its mapping

Files live in `data/raw/<portfolio>/<property>/<source>/`. The source folder name says what the file is:

| Source folder | What goes in it | Feeds table |
|---|---|---|
| `pms_reservations` | PMS reservation export | reservations |
| `pms_payments` | PMS payment postings or ledger | pms_payments |
| `expedia_vcc` | Expedia virtual card report | vcc_records |
| `booking_vcc` | Booking.com virtual card or payments report | vcc_records |
| `processor_settlement` | Card processor or gateway settlement | processor_transactions |

On import, LeakGuard reads the file's header and tries every mapping with the same `source_name`. The one that finds all required fields and matches the most columns wins. So two PMS formats can sit side by side, and each file picks the right one. If none fits, the file shows as failed on the Data sources page with its column names, and you add a mapping.

## Option 1: the mapping wizard (no YAML editing)

1. Open the dashboard, go to **Mapping wizard**.
2. Upload one export and say what kind of file it is.
3. Check the suggested column for each field. Suggestions use header names and sample values; the percentage is the confidence. Required fields are marked.
4. Look at the 20 parsed rows. Amounts, dates and card last 4 should look right. Fix any column that is wrong.
5. Name the mapping and click **Save, import and match**. The YAML is written to `mappings/`, the file is copied into the right raw folder, and matching runs.

Every later file in the same format is picked up automatically by `python -m leakguard import`.

## Option 2: write the YAML by hand

```yaml
name: acme_pms_reservations          # unique, also the file name
source_name: pms_reservations        # one of the source folders above
target: reservations                 # table it feeds (must match the source)
file_types: [csv, xlsx]
header_row: auto                     # or a row number if there are title rows
sheet: Reservations                  # optional, for workbooks with several sheets
date_order: mdy                      # optional: mdy or dmy, only if every date is ambiguous
columns:                             # canonical field: [possible header names]
  pms_confirmation_no: ["Conf #", "Confirmation Number"]
  ota_confirmation_no: ["External Ref"]
  channel: ["Source"]
  payment_model: ["Rate Plan"]
  guest_name: ["Guest"]              # full name; the last name is kept
  arrival_date: ["Arrival"]
  departure_date: ["Departure"]
  status: ["Status"]
  room_revenue: ["Room Revenue"]
  tax: ["Tax"]
  folio_total: ["Total"]
lookups:                             # optional extra words for enum fields
  payment_model:
    "EXP PREPAY": ota_collect
  status:
    "DEP": checked_out
fallbacks:                           # optional: value used when a word is unknown
  channel: other
defaults:                            # values for fields the file does not have
  currency: USD
confirmation_prefixes: [EXP, BDC]    # optional, stripped before matching
notes: "Built from a real export dated 2026-09. Confirmed with the hotel controller."
```

Header matching ignores case, spaces and punctuation, so `Conf #`, `conf#` and `CONF-#` are the same.

Canonical fields, their types and which are required are listed in `docs/DATA_DICTIONARY.md`. Money fields are named without `_cents` in mappings (`room_revenue`, `vcc_amount`, `amount`).

### Transforms

The field type decides how values are cleaned. You only need `transforms:` to override it.

| Type | Handles |
|---|---|
| money | `$1,234.56`, `(12.00)`, `12.00-`, `USD 5`, `1.234,56` |
| date | ISO, `03/04/2026`, `3/4/26`, `05-OCT-25`, `Oct 5, 2025`, `20251005`, Excel dates. Month or day first is inferred from the column; ambiguous columns get a warning |
| confirmation | Uppercase, strip spaces and punctuation, known prefixes and leading zeros |
| last4 | Keeps the last 4 digits of any masked or full number |
| enum (status, channel, payment model, OTA, transaction type) | Built-in synonyms plus your `lookups` |

### Card data rules

These run before anything is stored and cannot be turned off:

- A column whose header looks like a CVV or security code is dropped.
- A column mapped as `card_last4` keeps only the last 4 digits. If it held full numbers you get the warning "Full card numbers found in <file>. Dropped. Ask the hotel to send masked exports."
- An unmapped column holding full card numbers is dropped with the same warning.
- Any other mapped column holding a card number rejects that row.

## Check it worked

```
python -m leakguard import
```

The report lists each file with the mapping used, rows read, imported and rejected, the date range, and the top issues. The Data sources page shows the same, plus coverage gaps.

Mark a mapping's `notes` as PLACEHOLDER until it has been checked against a real export.
