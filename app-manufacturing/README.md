# Machine Watch

Machine Watch is the manufacturing dashboard for spotting machine risk before it becomes a failure event, and for explaining the reason behind each alert in operational terms.

## What it does

This app monitors machine health using precomputed risk scores and supporting sensor data. It highlights machines that stand out from their normal baseline in temperature, vibration, maintenance pattern, and historical failure behavior.

## How to run

```bash
cd FMN\app-manufacturing
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

Then open http://127.0.0.1:8001

## API

- `/` renders the dashboard
- `/api/explain` returns a plain-English explanation for the selected machine
- `/api/ask` answers a question grounded in the current machine dataset

## Data sources

- `data/p2_manufacturing.json` stores the scored machine artifact
- `data/p2_validation_incidents.json` provides historical validation context for failure trends
