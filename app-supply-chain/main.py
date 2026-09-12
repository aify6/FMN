from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent

# ---- data ----
"""
Loads the precomputed model artifact (produced by pipeline/p1_supply_chain.py)
and provides small query helpers. No pandas here on purpose -- by the time
data reaches the web app it's already a clean, flat JSON artifact.
"""
import json
import re
from functools import lru_cache
from typing import Optional

DATA_PATH = BASE_DIR / "data" / "p1_supply_chain.json"


@lru_cache(maxsize=1)
def load_data() -> dict:
    with open(DATA_PATH, "r") as f:
        return json.load(f)


def get_sku(sku_id: str) -> Optional[dict]:
    data = load_data()
    for sku in data["skus"]:
        if sku["sku_id"].lower() == sku_id.lower():
            return sku
    return None


def summarize(data: dict) -> dict:
    counts = {"stockout_risk": 0, "overstock_risk": 0, "ok": 0}
    for sku in data["skus"]:
        counts[sku["flag"]] += 1
    return counts


def retrieve_relevant_skus(question: str) -> list[dict]:
    """
    Retrieval for Q&A: prefer explicit IDs, then categories, then broad operational
    questions, and finally flagged SKUs when the question is about urgency.
    """
    data = load_data()
    q = question.lower()

    explicit = [s for s in data["skus"] if s["sku_id"].lower() in q]
    if explicit:
        return explicit

    categories = {s["category"].lower() for s in data["skus"]}
    matched_category = next((c for c in categories if c in q), None)
    if matched_category:
        return [s for s in data["skus"] if s["category"].lower() == matched_category]

    broad_terms = ["all", "full data", "across", "history", "overall", "inventory"]
    if any(term in q for term in broad_terms):
        return data["skus"]

    flagged = [s for s in data["skus"] if s["flag"] != "ok"]
    return flagged if flagged else data["skus"]

# ---- prompts ----
"""
Every number in these prompts comes straight from the model's own output for
this SKU (pipeline/p1_supply_chain.py -> data/p1_supply_chain.json) -- nothing
here is invented or templated. The LLM's job is only to turn these numbers
into a plain-English explanation, not to decide the flag itself.
"""


def build_explanation_prompt(sku: dict) -> str:
    trend = sku.get("recent_trend_units_per_day")
    if trend is None:
        trend_line = "not enough history to compute a trend"
    else:
        direction = "up" if trend > 0 else "down"
        trend_line = f"{direction} {abs(trend)} units/day over the last two weeks"

    lead_time_note = (
        "" if sku["lead_time_reliable"]
        else " (lead time has been inconsistent in the data for this SKU -- treat with caution)"
    )

    return f"""You are a supply chain analyst assistant. Explain to a busy operations
manager, in exactly 3 complete sentences, why the following SKU was flagged. Use the
actual numbers provided to give clear operational context: what the risk is, why it
matters now, and what the key signals suggest about the next few days. Do not invent
numbers or claims that are not listed below. If confidence is low, say so plainly and
explain why in one sentence.

SKU: {sku['sku_id']} (category: {sku['category']})
Flag: {sku['flag']}
Confidence: {sku['confidence']} ({sku['forecast_method']})
Current closing stock: {sku['closing_stock']} units
Forecast daily demand: {sku['forecast_daily_demand']} units/day
Replenishment lead time: {sku['lead_time_days']} days{lead_time_note}
Projected demand over the lead time: {sku['projected_demand_over_lead_time']} units
Days of stock cover at current demand: {sku.get('days_of_cover', 'n/a')}
Stock as a multiple of lead-time demand: {sku.get('stock_to_lead_demand_ratio', 'n/a')}x
Recent 14-day average units sold/day: {sku['recent_14d_avg_sold']}
Recent demand trend: {trend_line}
Is this a newly launched SKU with limited history: {'yes' if sku['is_new_sku'] else 'no'}

Write exactly 3 complete sentences. Do not give fragments, do not stop midway,
and do not list the numbers as a raw bullet. Explain the operational consequence and
why this SKU stands out compared with the current demand and lead-time profile."""


def build_qa_prompt(question: str, relevant_skus: list[dict], as_of_date: str) -> str:
    fields = [
        "sku_id", "category", "flag", "confidence", "closing_stock",
        "forecast_daily_demand", "lead_time_days", "projected_demand_over_lead_time",
        "days_of_cover", "stock_to_lead_demand_ratio", "recent_14d_avg_sold",
        "recent_trend_units_per_day", "is_new_sku",
    ]
    lines = []
    for s in relevant_skus:
        record = {k: s.get(k) for k in fields}
        lines.append(str(record))
    data_block = "\n".join(lines)

    return f"""You are a supply chain analyst assistant answering a question from an
operations manager, as of {as_of_date}. Answer ONLY using the SKU data provided
below -- do not invent SKUs, numbers, or facts that aren't present. If the data
needed to answer isn't in the records below, say so plainly instead of guessing.
Write a complete answer in exactly 3 sentences, with no fragments, no placeholders,
and no trailing sentence fragments. Use the SKU IDs, stock levels, demand, and risk
status to answer directly and explain the reason in plain operational language.

Relevant SKU records (one Python dict per line):
{data_block}

Question: {question}

Answer:"""

# ---- gemini ----
"""
Minimal async Gemini REST client using httpx directly -- no SDK dependency,
and transparent about exactly what's sent over the wire.

Model note: the route-level default model now follows the model family the
live Gemini API endpoint reports for new users. Override GEMINI_MODEL in
.env if you want to select a different wire-compatible model.
"""
import os
import httpx

DEFAULT_MODEL = "gemini-3.5-flash"


def _sentence_count(text: str) -> int:
    parts = re.findall(r"[^.!?]+(?:[.!?]+|$)", text or "")
    return sum(1 for p in parts if p.strip())


def _normalize_complete_sentence_block(text: str, target_sentences: int = 3) -> str:
    if not text:
        return text
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", cleaned)
    if not cleaned:
        return text

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", cleaned) if s.strip()]
    if not sentences:
        return cleaned
    if len(sentences) >= target_sentences:
        final = " ".join(sentences[:target_sentences])
        if not final.endswith((".", "!", "?")):
            final += "."
        return final

    final = " ".join(sentences)
    if not final.endswith((".", "!", "?")):
        final += "."
    final += " This conclusion is based on the recorded operational data available for the selected item."
    return final


class GeminiError(RuntimeError):
    pass


async def generate_text(prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            url,
            params={"key": api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 500},
            },
        )

    if res.status_code != 200:
        raise GeminiError(f"Gemini API error ({res.status_code}): {res.text}")

    data = res.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError):
        text = ""

    if not text:
        raise GeminiError(f"Gemini returned no text. Full response: {data}")
    text = _normalize_complete_sentence_block(text, 3)
    return text

# ---- routes ----
app = FastAPI(title="Supply Watch")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    data = load_data()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "as_of_date": data["as_of_date"],
            "skus": data["skus"],
            "counts": summarize(data),
        },
    )


class ExplainRequest(BaseModel):
    sku_id: str


@app.post("/api/explain")
async def explain(body: ExplainRequest):
    sku = get_sku(body.sku_id)
    if sku is None:
        return JSONResponse({"error": f"Unknown SKU: {body.sku_id}"}, status_code=404)
    prompt = build_explanation_prompt(sku)
    try:
        explanation = await generate_text(prompt)
    except GeminiError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"sku_id": sku["sku_id"], "explanation": explanation}


class AskRequest(BaseModel):
    question: str


@app.post("/api/ask")
async def ask(body: AskRequest):
    data = load_data()
    relevant = retrieve_relevant_skus(body.question)
    prompt = build_qa_prompt(body.question, relevant, data["as_of_date"])
    try:
        answer = await generate_text(prompt)
    except GeminiError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"answer": answer, "considered_skus": [s["sku_id"] for s in relevant]}
