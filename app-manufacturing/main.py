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
Loads the precomputed model artifact (produced by pipeline/p2_manufacturing.py)
and provides small query helpers.
"""
import json
import re
from functools import lru_cache
from typing import Optional

DATA_DIR = BASE_DIR / "data"
DATA_PATH = DATA_DIR / "p2_manufacturing.json"
INCIDENTS_PATH = DATA_DIR / "p2_validation_incidents.json"

 
@lru_cache(maxsize=1)
def load_data() -> dict:
    with open(DATA_PATH, "r") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_incidents() -> list[dict]:
    with open(INCIDENTS_PATH, "r") as f:
        return json.load(f)


def get_machine(machine_id: str) -> Optional[dict]:
    data = load_data()
    for m in data["machines"]:
        if m["machine_id"].lower() == machine_id.lower():
            return m
    return None


def get_incident_for(machine_id: str) -> Optional[dict]:
    for inc in load_incidents():
        if inc["machine_id"] == machine_id:
            return inc
    return None


def summarize(data: dict) -> dict:
    counts = {"high_risk": 0, "watch": 0, "monitor_new_asset": 0, "ok": 0}
    for m in data["machines"]:
        counts[m["flag"]] += 1
    return counts


def retrieve_relevant_machines(question: str) -> list[dict]:
    """
    Retrieval for Q&A: prefer explicit IDs, then line matches, then data-history
    questions, and finally flagged machines for operational questions.
    """
    data = load_data()
    q = question.lower()

    explicit = [m for m in data["machines"] if m["machine_id"].lower() in q]
    if explicit:
        return explicit

    lines = {m["line"].lower() for m in data["machines"]}
    matched_line = next((l for l in lines if l in q), None)
    if matched_line:
        return [m for m in data["machines"] if m["line"].lower() == matched_line]

    history_terms = ["failure", "failures", "historical", "ever", "all data", "full data", "across"]
    if any(term in q for term in history_terms):
        relevant = [m for m in data["machines"] if (m.get("total_historical_failures") or 0) > 0]
        return relevant if relevant else data["machines"]

    flagged = [m for m in data["machines"] if m["flag"] != "ok"]
    return flagged if flagged else data["machines"]

# ---- prompts ----
"""
Every number here comes straight from the model's own scoring for this
machine (pipeline/p2_manufacturing.py -> data/p2_manufacturing.json) --
nothing is invented. The LLM turns these numbers into plain English; it
doesn't set the flag or risk score itself.
"""


def _clean_number(value):
    if value is None:
        return "n/a"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format(value, "g")
    return str(value)


def build_explanation_prompt(m: dict, related_incident: dict | None = None) -> str:
    if m["is_new_machine"] or m["confidence"] == "low":
        return f"""You are a plant maintenance assistant. Explain to a floor supervisor,
in exactly 3 complete sentences, why machine {m['machine_id']} ({m['line']}) is only under
basic monitoring instead of a full risk score. Use the actual facts: it has limited
operating history for the trained model baseline, current temperature is {_clean_number(m['current_temperature_c'])}C,
and current vibration is {_clean_number(m['current_vibration_mm_s'])}mm/s. Explain clearly what the operational
impact is without inventing any unreported risk percentage or trend."""

    incident_line = ""
    if related_incident:
        incident_line = (
            f" For context, the last time a comparable pre-failure pattern was seen on "
            f"this line, the model's risk score rose from {_clean_number(related_incident['risk_score_48h_before'])} "
            f"to {_clean_number(related_incident['risk_score_at_failure'])} in the 48 hours before an actual failure."
        )

    return f"""You are a plant maintenance assistant. Explain to a floor supervisor,
in exactly 3 complete sentences, why the following machine was flagged. Use the data
to tell the story: what the risk is, why it matters now, and what the key signals
suggest about the machine's condition. Do not invent numbers or claims that are not
listed below.

Machine: {m['machine_id']} ({m['line']})
Flag: {m['flag']}
Risk score (probability of failure in next 24h): {_clean_number(m['risk_score'])}
Current temperature: {_clean_number(m['current_temperature_c'])}C (this machine's normal baseline: {_clean_number(m['temp_baseline_mean_c'])}C, z-score: {_clean_number(m['temp_zscore'])})
Current vibration: {_clean_number(m['current_vibration_mm_s'])}mm/s (this machine's normal baseline: {_clean_number(m['vib_baseline_mean'])}mm/s, z-score: {_clean_number(m['vib_zscore'])})
Temperature change over last 6 hours: {_clean_number(m['temp_change_6h_c'])}C
Vibration change over last 6 hours: {_clean_number(m['vib_change_6h'])}mm/s
Hours run since last maintenance: {_clean_number(m['run_hours_since_maintenance'])}
Total historical failures on this machine: {_clean_number(m['total_historical_failures'])}
{incident_line}

Write exactly 3 complete sentences. Do not give fragments, do not stop half-way,
and do not list the numbers as a raw bullet. Explain the operational consequence and
why this machine stands out compared with its normal baseline."""


def build_qa_prompt(question: str, relevant_machines: list[dict], as_of: str) -> str:
    fields = [
        "machine_id", "line", "flag", "risk_score", "confidence",
        "current_temperature_c", "current_vibration_mm_s", "temp_zscore",
        "vib_zscore", "run_hours_since_maintenance", "total_historical_failures",
    ]
    lines = []
    for m in relevant_machines:
        record = {k: m.get(k) for k in fields}
        record["risk_trend_14d_last_7"] = m.get("risk_trend_14d", [])[-7:]
        lines.append(str(record))
    data_block = "\n".join(lines)

    return f"""You are a plant maintenance assistant answering a question from a
floor supervisor, as of {as_of}. Answer ONLY using the machine data provided
below -- do not invent machines, numbers, or facts not present. If the data
needed isn't in the records below, say so plainly instead of guessing. Write a
complete answer in exactly 3 sentences, with no fragments, no placeholders, and no
trailing sentence fragments. Use the machine IDs, historical failure counts, and flag
status to answer directly, and explain the reason in plain operational language.

Relevant machine records (one Python dict per line):
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
FALLBACK_MODELS = ["gemini-3.5-flash-lite"]


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

    requested_model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    candidate_models = []
    normalized = requested_model.strip().lower().replace(" ", "-").replace(",", ".")
    if normalized:
        candidate_models.append(normalized)
    for fallback in FALLBACK_MODELS:
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    last_error = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        for model in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                res = await client.post(
                    url,
                    params={"key": api_key},
                    json={
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 500},
                    },
                )
            except httpx.HTTPError as exc:
                last_error = exc
                continue

            if res.status_code == 200:
                data = res.json()
                try:
                    parts = data["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts).strip()
                except (KeyError, IndexError):
                    text = ""

                if not text:
                    last_error = GeminiError(f"Gemini returned no text. Full response: {data}")
                    continue

                return _normalize_complete_sentence_block(text, 3)

            last_error = GeminiError(f"Gemini API error ({res.status_code}): {res.text}")
            if res.status_code != 429:
                break

    if last_error is None:
        raise GeminiError("Gemini request failed without a response.")
    raise last_error

# ---- routes ----
app = FastAPI(title="Machine Watch")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    data = load_data()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "as_of": data["as_of"],
            "machines": data["machines"],
            "counts": summarize(data),
        },
    )


class ExplainRequest(BaseModel):
    machine_id: str


@app.post("/api/explain")
async def explain(body: ExplainRequest):
    machine = get_machine(body.machine_id)
    if machine is None:
        return JSONResponse({"error": f"Unknown machine: {body.machine_id}"}, status_code=404)
    incident = get_incident_for(machine["machine_id"])
    prompt = build_explanation_prompt(machine, incident)
    try:
        explanation = await generate_text(prompt)
    except GeminiError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"machine_id": machine["machine_id"], "explanation": explanation}


class AskRequest(BaseModel):
    question: str


@app.post("/api/ask")
async def ask(body: AskRequest):
    data = load_data()
    relevant = retrieve_relevant_machines(body.question)
    prompt = build_qa_prompt(body.question, relevant, data["as_of"])
    try:
        answer = await generate_text(prompt)
    except GeminiError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"answer": answer, "considered_machines": [m["machine_id"] for m in relevant]}
