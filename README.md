# LeakGuard

LeakGuard finds hotel revenue that was earned but never collected on OTA virtual cards (Expedia Collect and Payments by Booking.com), helps property staff collect it, and shows owners the results across the whole portfolio.

It matches every virtual card against the PMS reservation, the PMS payment postings and the card processor's settlements, and flags cards that were never charged, charged the wrong amount, or charged but never settled. Matching is deterministic and rule-based. A person reviews every item before money moves.

Built for hotel owners and asset managers: portfolio view across every hotel and management company, an operator scorecard, and recovered cash shown as NOI impact.

## Quick start

Requires Python 3.12 or newer.

```bash
pip install -r requirements.txt && pip install -e .
python -m leakguard demo
streamlit run app/Home.py
```

`demo` generates a fictional 8-hotel portfolio (Summit Ridge Capital) as raw export files, imports them through the same pipeline real files use, runs matching, and adds six months of simulated recovery work. It takes one to three minutes. The dashboard opens at http://localhost:8501.

To require a password (do this before sharing a screen or deploying):

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then edit app_password
```

Without that file the app runs with the password gate off and says so in the sidebar.

### Windows setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
python -m leakguard demo
streamlit run app/Home.py
```

### Mac / Linux setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python -m leakguard demo
streamlit run app/Home.py
```

## Put it online (Streamlit Community Cloud, free)

GitHub stores the code but cannot run it. To get a link anyone can open:

1. Sign in at https://share.streamlit.io with your GitHub account.
2. Click **Create app**, choose repository `rishimaguluri/LeakGuard`, branch `main`, main file `app/Home.py`. Under **Advanced settings** pick Python 3.12 and paste `app_password = "choose-a-password"` into **Secrets**.
3. Click **Deploy**. The first visit builds the demo portfolio (2 to 4 minutes); after that it loads straight away.

Only demo data goes online this way. Real hotel data stays on your machine in `data/raw/`, which is never committed.

## Commands

| Command | What it does |
|---|---|
| `python -m leakguard demo` | Generate demo files, rebuild the demo database, import, match, add recovery history |
| `python -m leakguard import` | Import every file in the current mode's raw folder. Unchanged files are skipped |
| `python -m leakguard match` | Run matching and refresh exceptions. Never overwrites a status a person set |
| `python -m leakguard report --portfolio <slug>` | Write the Excel workbook and PDF summary to `exports/`. Optional `--start`, `--end`, `--out` |
| `python -m leakguard reset --mode demo` | Delete one mode's database (asks you to confirm) |
| `streamlit run app/Home.py` | Open the dashboard |
| `python tasks.py check` | Lint and run the tests (also `test`, `lint`, `format`, `app`) |

## What each page does

| Page | What you see |
|---|---|
| **Home** | Leaked in the last 12 months, recovered to date, open dollars at risk, cards expiring in 7 days, the operator takeaway, the top 5 actions this week (each opens in the queue), a leaked vs recovered trend, and a short to-do list |
| **Portfolio** | Leakage by property and by exception type, monthly trend, a property table (rooms, OTA-collect revenue, leaked, leak rate, recovered, open, recovery rate) and the NOI impact panel (annualized recovered cash, fee, owner net gain, implied value at your cap rate) |
| **Operator scorecard** | Management companies ranked by leak rate, a plain-language takeaway, ranked cards, side-by-side comparison charts, the full scorecard, and a drill down into each operator's properties |
| **Hotel detail** | One property: KPIs, monthly trend, data coverage per source, and its exceptions |
| **Exception queue** | Filter by property, type, status, OTA, match method, amount and days to expiry. Select a row for the evidence panel: card, matched reservation, PMS postings, processor transactions, the rule that flagged it, match method and confidence, and the audit trail. Assign, change status, record recovery, add notes, confirm fuzzy matches. Bulk status change, bulk assign and CSV export for property staff |
| **Recovery tracker** | Recovered by month, by type and by property, fee earned, open pipeline, recovery rate, median days to recover, latest recoveries |
| **Audit report** | Pick the period, preview the summary, download the branded PDF and the Excel workbook |
| **Data sources** | Every imported file with rows read, imported and rejected, the coverage matrix (properties by required sources, with the reason for each gap), import issues, and a button to re-run import and matching |
| **Mapping wizard** | Upload an unfamiliar export, review suggested column matches with confidence, preview 20 parsed rows, save the mapping and import |
| **Settings** | Fee, charge tolerance, expiry window, settlement window, fuzzy thresholds, guest name hashing, cap rate and data mode, saved to `config/settings.yaml` |

## Switching to real data (3 steps, no code changes)

1. **Describe the portfolio.** Add it to `config/portfolios.yaml` with `mode: real`, its management companies and properties (code, name, rooms, operator).
2. **Drop in the exports.** Put each hotel's files in `data/raw/<portfolio_slug>/<property_code>/<source>/`, where source is one of `pms_reservations`, `pms_payments`, `expedia_vcc`, `booking_vcc`, `processor_settlement`. `docs/DATA_REQUEST.md` is the list to send the hotel's controller.
3. **Switch and run.** Set `data_mode: real` in `config/settings.yaml` (or on the Settings page), then run `python -m leakguard import` and `python -m leakguard match`, or press **Re-run import and matching** on the Data sources page.

If an export's columns are new to LeakGuard, the Data sources page says so and the Mapping wizard builds the mapping. See `docs/ADDING_A_SOURCE.md`.

Demo and real data use separate folders and separate database files (`data/leakguard_demo.db`, `data/leakguard_real.db`), so they never mix. `data/raw/`, all databases and `exports/` are gitignored.

## Data handling

- Only the last 4 digits of card numbers are kept. Full card numbers are dropped on import, before anything is stored, with a warning naming the file. CVV columns are dropped unread.
- Guest names: last name only. Turn on hashing in Settings for real data.
- The dashboard has a password gate (`.streamlit/secrets.toml`).
- No LLM or external service is called. Everything runs locally.

## Screenshots

| | |
|---|---|
| Home | `docs/screenshots/home.png` (placeholder) |
| Operator scorecard | `docs/screenshots/scorecard.png` (placeholder) |
| Exception queue with evidence | `docs/screenshots/queue.png` (placeholder) |
| Audit report PDF | `docs/screenshots/report.png` (placeholder) |

## Project layout

```
config/        settings.yaml, portfolios.yaml
mappings/      one YAML per export format
data/          demo_raw/ (generated), raw/ (your files, gitignored), databases
src/leakguard/ ingest/, matching/, recovery/, reporting/, demo/, CLI
app/           Home.py (entry and navigation), views/ (one file per page), ui.py, styles.css
docs/          DATA_REQUEST.md, DATA_DICTIONARY.md, ADDING_A_SOURCE.md
tests/         pytest suite, including dashboard page tests and a real-mode end-to-end test
```

## Tests

```bash
python tasks.py check
```

The suite covers normalizers, readers, the card scanner, mappings, idempotent imports, every matching rule and its edge cases, exception stability across runs, the recovery workflow, metrics, both reports, every dashboard page, and an end-to-end run of real mode with a new export format.
