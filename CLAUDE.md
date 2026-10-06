# LeakGuard working rules

Full spec: `SPEC.md`. This file is the short version. If they disagree, SPEC.md wins; flag the conflict.

## What it is
Finds hotel OTA virtual card (VCC) revenue that was earned but never collected (Expedia Collect, Payments by Booking.com), helps staff recover it, and shows owners results across a portfolio. Buyers are owners and asset managers, not GMs. Edge: portfolio view, operator scorecard, recovered cash as NOI impact, one-day onboarding.

## Hard rules
- **Demo and real data go through one pipeline.** Demo data is fake raw CSV/XLSX files in `data/demo_raw/`, imported exactly like real files. Never write demo rows straight into the database.
- **Formats live in YAML, not code.** New PMS or OTA format means a new file in `mappings/`. Do not invent facts about real report formats; mark guesses as `PLACEHOLDER` in the mapping `notes`.
- **Switching modes needs zero code changes.** `data_mode` in `config/settings.yaml` (or env `LEAKGUARD_DATA_MODE`) picks the raw folder and the database file. Only `config.py` knows paths.
- **Card data:** keep last 4 only. Drop full card number columns before anything touches disk or DB and log a warning. The scanner rejects any 13 to 19 digit card pattern in columns not mapped as last4. No CVVs, no expiry tied to a card number.
- **Never commit** `data/raw/`, `data/*.db`, `data/processed/`, `exports/`, `.env`, `.streamlit/secrets.toml`. No real hotel, guest or owner names anywhere in code, tests or commits.
- Guest names: last name only; hash when `hash_guest_names` is true.
- **Matching is deterministic and rule-based. No LLM calls.** A person reviews every exception before money moves. Fuzzy matches always need review.
- Exceptions are stable across runs (key: property + OTA confirmation + type). Never overwrite a status a person set.
- **Every number on every page and report comes from `reporting/metrics.py`.**
- Imports are idempotent: file hash stored; same file never duplicates; a corrected file replaces that file's rows.
- Money is integer cents plus currency. Dates ISO. Datetimes UTC; timezone lives on the property.
- Every table carries `portfolio_id` (directly or through property).
- UI copy never overstates. Estimates are labeled estimates. Recovery is never "guaranteed".

## Style
- Python 3.12+, type hints everywhere, small functions, boring readable code, docstrings on anything non-obvious.
- **No em dashes anywhere** (code, comments, UI, docs, commits). Sentence-case headings.
- UI colors: background `#EDF2F8`, cards `#F8FAFD`, text `#0E2240`, secondary text `#25406A`, accent `#1A3766`, lines `#BFD0E6`, losses `#8C2B45`. No gold, teal or grey text. Body text 14px minimum.

## Workflow
- Say what you are about to do and why, briefly, before each change.
- Run `python tasks.py check` (ruff + pytest) after every meaningful change and report results.
- Work in phases (SPEC.md section 13). Stop at the end of each phase, show changes and test results, wait for approval.
- Commit at the end of each phase. Never push unless asked.
- If the spec is wrong, risky, or there is a simpler way, say so.

## Layout
- `src/leakguard/` package: `config.py`, `db.py`, `models.py`, `schemas.py`, `ingest/`, `matching/`, `recovery/`, `reporting/`, `demo/`
- `app/` Streamlit multipage dashboard; `config/` settings and portfolios; `mappings/` source formats; `tests/`; `docs/`
- Real data folder convention: `data/raw/<portfolio_slug>/<property_code>/<source_name>/<files>`

## Commands
- `python -m leakguard demo | import | match | report --portfolio <slug> | reset --mode demo|real`
- `streamlit run app/Home.py`
- `python tasks.py test | lint | format | check | app`
