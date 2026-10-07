# LeakGuard working rules

Full spec: `SPEC.md`. This file is the short version. If they disagree, SPEC.md wins; flag the conflict.

## What it is
Finds hotel OTA virtual card (VCC) revenue that was earned but never collected (Expedia Collect, Payments by Booking.com), helps staff recover it, and shows owners results across a portfolio. Buyers are owners and asset managers, not GMs. Edge: portfolio view, operator scorecard, recovered cash as NOI impact, one-day onboarding.

## Hard rules
- **Demo and real data go through one pipeline.** Demo data is fake raw CSV/XLSX files generated into `data/demo_raw/` (gitignored, about 23 MB, rebuilt from a fixed seed), imported exactly like real files. Never write demo rows straight into the database. The only demo-specific DB writes are simulated recovery actions in `demo/history.py`, and those go through `recovery/workflow.py`.
- **Formats live in YAML, not code.** New PMS or OTA format means a new file in `mappings/`. Do not invent facts about real report formats; mark guesses as `PLACEHOLDER` in the mapping `notes`.
- **Switching modes needs zero code changes.** `data_mode` in `config/settings.yaml` (or env `LEAKGUARD_DATA_MODE`) picks the raw folder and the database file. Only `config.py` knows paths.
- **Card data:** keep last 4 only. Drop full card number columns before anything touches disk or DB and log a warning. The scanner rejects any 13 to 19 digit card pattern in columns not mapped as last4. No CVVs, no expiry tied to a card number.
- **Never commit** `data/raw/`, `data/*.db`, `data/processed/`, `exports/`, `.env`, `.streamlit/secrets.toml`. No real hotel, guest or owner names anywhere in code, tests or commits.
- Guest names: last name only; hash when `hash_guest_names` is true.
- **Matching is deterministic and rule-based. No LLM calls.** A person reviews every exception before money moves. Fuzzy matches always need review.
- One exceptions row per virtual card, keyed on property + `vcc_key` (ota|confirmation|last4). The type can change between runs and is logged; a status a person set is never overwritten. Auto-resolve only when an open item becomes CHARGED_OK. CHARGED_OK and NOT_YET_DUE rows exist with `is_exception = false` for coverage and volume.
- Rule precedence (matching/rules.py): duplicate, no PMS match, cancelled, nothing charged (expired / uncharged / not yet due), undercharged, overcharged, not settled, OK.
- Demo mode uses a fixed as-of date (`demo_as_of_date`); real mode uses today or `as_of_date`.
- **Every number on every page and report comes from `reporting/metrics.py`.**
- Imports are idempotent: file hash stored; same file never duplicates; a corrected file replaces that file's rows.
- Money is integer cents plus currency. Dates ISO. Datetimes UTC; timezone lives on the property.
- Every table carries `portfolio_id` (directly or through property).
- UI copy never overstates. Estimates are labeled estimates. Recovery is never "guaranteed".

## Style
- Python 3.12+, type hints everywhere, small functions, boring readable code, docstrings on anything non-obvious.
- **No em dashes anywhere** (code, comments, UI, docs, commits). Sentence-case headings.
- UI: background `#EDF2F8`, cards `#F8FAFD` with 1px `#BFD0E6` border and 8px radius, text `#0E2240`, secondary `#25406A`, accent `#1A3766`, losses `#8C2B45`, recovered `#1F5A3D`. No gold, teal or grey text. Body 15px+, headings DM Serif Display, body Lato. Theme in `.streamlit/config.toml` plus `app/styles.css`. Use `app/ui.py` helpers (kpis, table, pills, charts), never raw dataframes except the queue grid.
- Charts: plotly via `ui.style()`. One axis. Leaked and recovered on one chart: leaked as bars or circles, recovered as a line with diamond markers (berry and green are hard to tell apart for colorblind readers).
- Numbers: summary cards whole dollars with commas; line items show cents; percentages 1 decimal. Format with `metrics.money`, `metrics.pct`, `metrics.fmt_date`.

## Workflow
- Say what you are about to do and why, briefly, before each change.
- Run `python tasks.py check` (ruff + pytest) after every meaningful change and report results.
- Commit at the end of each phase or meaningful chunk. Push only when asked.
- The dashboard is tested with Streamlit AppTest (`tests/test_app.py`): every page must load with no errors, show the mode badge, and contain no em dashes.
- If the spec is wrong, risky, or there is a simpler way, say so.

## Layout
- `src/leakguard/` package: `config.py`, `db.py`, `models.py`, `schemas.py`, `ingest/`, `matching/`, `recovery/`, `reporting/`, `demo/`
- `app/Home.py` is the entry point and builds navigation with `st.navigation`; pages live in `app/views/` (not `pages/`, which Streamlit would auto-discover and clash with). `app/context.py` holds the password gate, sidebar filters and cached data.
- `config/` settings and portfolios (demo and real portfolios side by side, each with `mode`); `mappings/` source formats; `tests/`; `docs/`
- Env overrides for tests: `LEAKGUARD_DATA_MODE`, `LEAKGUARD_DATA_DIR`, `LEAKGUARD_CONFIG_DIR`, `LEAKGUARD_MAPPINGS_DIR`.
- Real data folder convention: `data/raw/<portfolio_slug>/<property_code>/<source_name>/<files>`

## Commands
- `python -m leakguard demo | import | match | report --portfolio <slug> | reset --mode demo|real`
- `streamlit run app/Home.py`
- `python tasks.py test | lint | format | check | app`
