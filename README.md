# FMN — Sponsor Ops Tools

This repository contains two operational monitoring dashboards built for a sponsor brief: one for inventory risk and one for manufacturing reliability. Both are designed to answer a simple operational question quickly: what needs attention now, and why?

## Problem understanding

The sponsor asked for a tool that turns raw operational data into a clear operational signal. In practice, that means surfacing the items or machines most likely to cause disruption before the issue becomes visible in a normal report.

- For the supply-chain app, the business problem is inventory risk: which SKUs are likely to stock out, which are overstocked, and why the risk is building.
- For the manufacturing app, the problem is machine reliability: which machines are showing a failure pattern, what is driving it, and which assets need attention first.

Both apps are built to translate model output into plain English, grounded in real numbers from the underlying data, so a stakeholder does not need to read a notebook or raw report to understand the issue.

## Approach

The project follows a train-once, serve-artifacts pattern:

1. Explore and compare models in notebook experiments.
2. Select the best-performing model for each domain.
3. Export the model output into a clean JSON artifact.
4. Serve the artifact through FastAPI dashboards.
5. Use Gemini at runtime to explain the flag and support natural-language Q&A.

This keeps the app fast, transparent, and easy to reason about.

### The two apps

- Supply chain app: inventory watchlist + demand-risk explanations
- Manufacturing app: machine watchlist + failure-risk explanations

Each app includes:
- a filtered watchlist of priority items
- a drill-down detail modal
- charts showing the historical pattern
- an explain feature grounded in the underlying risk data
- a Q&A panel for operational questions over the current dataset

## Repository structure

```text
FMN/
├── app-supply-chain/
│   ├── data/
│   ├── static/
│   ├── templates/
│   ├── main.py
│   ├── requirements.txt
│   └── README.md
├── app-manufacturing/
│   ├── data/
│   ├── static/
│   ├── templates/
│   ├── main.py
│   ├── requirements.txt
│   └── README.md
├── pipeline/
│   ├── p1_supply_chain.py
│   └── p2_manufacturing.py
├── docs/
│   ├── screenshots/
│   └── p1_metrics.json
│   └── p2_metrics.json
├── README.md
└── .venv/
```

## How to run

You need Python 3.10+ and a Gemini API key.

### 1. Create the environment

```bash
cd FMN
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

For each app:

```bash
cd FMN\app-supply-chain
pip install -r requirements.txt
```

```bash
cd FMN\app-manufacturing
pip install -r requirements.txt
```

### 3. Configure the Gemini key

Create a `.env` file in each app using the provided `.env.example` template, and add your API key.

### 4. Run the apps

Supply chain:

```bash
cd FMN\app-supply-chain
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Manufacturing:

```bash
cd FMN\app-manufacturing
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

Then open:
- http://127.0.0.1:8000 for Supply Watch
- http://127.0.0.1:8001 for Machine Watch

### Deployment note

These are standard FastAPI apps and can be deployed to a Python host or container service without changing the application logic. The required runtime secret is the Gemini API key, passed in as an environment variable.

## Limitations and next steps

This is a strong working prototype, but there are still gaps:

- The model logic is static and based on the supplied artifacts; it is not a real-time streaming system.
- The Q&A retrieval is rule-based and works well for the current dataset size, but it is not a full vector-search or semantic-retrieval engine.
- The cold-start or low-history cases are treated conservatively and flagged with lower confidence rather than treated as fully trusted predictions.
- The current design is intentionally tailored to the sponsor brief and not yet generalized into a multi-tenant product.

With more time, the next upgrades would be:
- expose a production-grade deployment layer with auth and persistence
- add stronger retrieval and prompt validation for LLM responses
- improve model quality and feature coverage with more operational history
- add export/reporting flows for recurring PM and inventory reviews
