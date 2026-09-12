# Supply Watch

Supply Watch is the supply-chain dashboard for identifying the SKUs most likely to experience stockout or overstock pressure, and explaining the operational reason behind each flag.

## What it does

This app reviews the latest supply profile for each SKU and highlights the items where forecast demand is moving away from the current stock position or lead-time profile. It helps a planner quickly see which items need attention and why.

## How to run

```bash
cd FMN\app-supply-chain
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Then open http://127.0.0.1:8000

## API

- `/` renders the dashboard
- `/api/explain` returns a plain-English explanation for the selected SKU
- `/api/ask` answers a question grounded in the current SKU dataset

## Data sources

- `data/p1_supply_chain.json` stores the scored SKU artifact used by the dashboard
- pipeline output is the source of the forecast, stock, and lead-time values shown in the app
