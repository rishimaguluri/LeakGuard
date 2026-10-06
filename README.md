# LeakGuard

Finds hotel OTA virtual card revenue that was earned but never collected, helps staff collect it, and shows owners the results across the portfolio.

Status: phase 0 (skeleton). See `SPEC.md` for the full plan.

## Setup

Requires Python 3.12 or newer.

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
```

**Mac / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Then set a password in `.streamlit/secrets.toml`.

## Commands

| Command | What it does |
|---|---|
| `python -m leakguard demo` | Generate demo files, import, match (phase 1 to 3) |
| `python -m leakguard import` | Import every file in the current mode's raw folder (phase 2) |
| `python -m leakguard match` | Run matching and refresh exceptions (phase 3) |
| `python -m leakguard report --portfolio <slug>` | Export the audit report (phase 5) |
| `python -m leakguard reset --mode demo` | Delete that mode's database (asks first) |
| `streamlit run app/Home.py` | Open the dashboard (phase 4) |
| `python tasks.py check` | Lint and run tests |

## Switching to real data (3 steps)

1. Add the portfolio and its properties to `config/portfolios.yaml` with `mode: real`.
2. Drop the hotel's exports into `data/raw/<portfolio_slug>/<property_code>/<source_name>/`.
3. Set `data_mode: real` in `config/settings.yaml`, then run `python -m leakguard import` and `python -m leakguard match`.

`data/raw/` and all database files are gitignored. Demo and real data use separate database files and never mix.
