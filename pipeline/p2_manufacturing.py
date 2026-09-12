"""
Project 2 — Manufacturing failure-risk pipeline.

Key data reality: only 17 failure events across 15 established machines over
120 days (~0.04% positive rate at the row level) -> a plain per-row classifier
would be useless. Instead:

1. Clean (dedupe exact duplicate timestamp rows, interpolate short sensor gaps)
2. Feature engineer machine-relative signals (rolling mean/std, z-score vs that
   machine's own baseline, rate of change) - baselines differ a lot by machine
   (mean temp ranges 57-70C across machines), so global thresholds don't work
3. Relabel as "will this machine fail in the next 24h" -> turns 17 point events
   into ~17*24 positive-labeled hours, still rare but workable with class
   weighting. Time-based split to avoid leakage.
4. Train a LogisticRegression classifier inside a StandardScaler pipeline,
   evaluate with PR-AUC (appropriate for rare positive class) vs a random baseline
5. Score latest snapshot per machine -> risk score + concrete drivers (actual
   z-scores / trend, not just the model's opaque importances) for LLM grounding
6. Export per-machine JSON artifact including a 14-day risk trend for the
   "track how risk is trending" requirement
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
RAW_PATH = HERE / "data" / "project2_manufacturing_sensors.csv"
OUT_ARTIFACT = HERE.parent / "app-manufacturing" / "data" / "p2_manufacturing.json"
OUT_METRICS = HERE.parent / "docs" / "p2_metrics.json"
OUT_INCIDENTS = HERE.parent / "app-manufacturing" / "data" / "p2_validation_incidents.json"

NEW_MACHINES = {"MCH-300", "MCH-301"}
LOOKAHEAD_HOURS = 24
HOLDOUT_DAYS = 20

# ---------------------------------------------------------------------------
# 1. Load + clean
# ---------------------------------------------------------------------------
df = pd.read_csv(RAW_PATH, parse_dates=["timestamp"])
n_raw = len(df)

before = len(df)
df = df.drop_duplicates(subset=["timestamp", "machine_id"], keep="first")
n_dupes_dropped = before - len(df)

df = df.sort_values(["machine_id", "timestamp"]).reset_index(drop=True)

cleaned = []
for mid, g in df.groupby("machine_id"):
    g = g.set_index("timestamp").sort_index()
    full_idx = pd.date_range(g.index.min(), g.index.max(), freq="h")
    g = g.reindex(full_idx)
    g["machine_id"] = mid
    g["line"] = g["line"].ffill().bfill()
    g["failure_event"] = g["failure_event"].fillna(0)
    g["run_hours_since_maintenance"] = g["run_hours_since_maintenance"].interpolate().bfill().ffill()
    g["temperature_c"] = g["temperature_c"].interpolate(limit=6).bfill().ffill()
    g["vibration_mm_s"] = g["vibration_mm_s"].interpolate(limit=6).bfill().ffill()
    cleaned.append(g)

clean = pd.concat(cleaned).rename_axis("timestamp").reset_index()

# ---------------------------------------------------------------------------
# 2. Feature engineering (machine-relative)
# ---------------------------------------------------------------------------
clean = clean.sort_values(["machine_id", "timestamp"]).reset_index(drop=True)

# Long-run per-machine baseline, computed on established machines' full history
# (acceptable for a 96h build; a production version would exclude pre-failure
# windows from the baseline calc to avoid the baseline drifting toward "risky").
baseline = clean.groupby("machine_id").agg(
    temp_base_mean=("temperature_c", "mean"), temp_base_std=("temperature_c", "std"),
    vib_base_mean=("vibration_mm_s", "mean"), vib_base_std=("vibration_mm_s", "std"),
).reset_index()
clean = clean.merge(baseline, on="machine_id", how="left")

for w in (6, 24):
    clean[f"temp_roll_mean_{w}"] = clean.groupby("machine_id")["temperature_c"].transform(
        lambda s: s.rolling(w, min_periods=1).mean())
    clean[f"vib_roll_mean_{w}"] = clean.groupby("machine_id")["vibration_mm_s"].transform(
        lambda s: s.rolling(w, min_periods=1).mean())

clean["temp_zscore"] = (clean["temp_roll_mean_6"] - clean["temp_base_mean"]) / clean["temp_base_std"]
clean["vib_zscore"] = (clean["vib_roll_mean_6"] - clean["vib_base_mean"]) / clean["vib_base_std"]

clean["temp_trend_6h"] = clean.groupby("machine_id")["temperature_c"].transform(
    lambda s: s.diff(6))
clean["vib_trend_6h"] = clean.groupby("machine_id")["vibration_mm_s"].transform(
    lambda s: s.diff(6))

# ---------------------------------------------------------------------------
# 3. Label: failure within next LOOKAHEAD_HOURS
# ---------------------------------------------------------------------------
def label_lookahead(g):
    fail_times = g.loc[g.failure_event == 1, "timestamp"]
    y = pd.Series(0, index=g.index)
    for ft in fail_times:
        window = (g["timestamp"] < ft) & (g["timestamp"] >= ft - pd.Timedelta(hours=LOOKAHEAD_HOURS))
        y[window] = 1
    return y

clean["label_at_risk_24h"] = clean.groupby("machine_id", group_keys=False).apply(
    lambda g: label_lookahead(g))

FEATURES = [
    "temp_zscore", "vib_zscore", "temp_trend_6h", "vib_trend_6h",
    "temp_roll_mean_6", "vib_roll_mean_6", "temp_roll_mean_24", "vib_roll_mean_24",
    "run_hours_since_maintenance",
]

model_df = clean.dropna(subset=FEATURES).copy()
established = model_df[~model_df.machine_id.isin(NEW_MACHINES)]
cutoff = established["timestamp"].max() - pd.Timedelta(days=HOLDOUT_DAYS)
train = established[established["timestamp"] <= cutoff]
test = established[established["timestamp"] > cutoff]

# ---------------------------------------------------------------------------
# 4. Train + validate using the notebook-selected best classifier
# ---------------------------------------------------------------------------
pos_rate = train["label_at_risk_24h"].mean()
model = Pipeline([
    ("scaler", StandardScaler()),
    ("model", LogisticRegression(class_weight="balanced", max_iter=500, random_state=42)),
])
model.fit(train[FEATURES], train["label_at_risk_24h"])

test = test.copy()
test["risk_score"] = model.predict_proba(test[FEATURES])[:, 1]

pr_auc = average_precision_score(test["label_at_risk_24h"], test["risk_score"])
random_baseline = test["label_at_risk_24h"].mean()

perm_importance = dict(zip(FEATURES, [None] * len(FEATURES)))
try:
    from sklearn.inspection import permutation_importance
    pi = permutation_importance(model, test[FEATURES], test["label_at_risk_24h"], n_repeats=5, random_state=42, scoring="average_precision")
    perm_importance = dict(sorted(zip(FEATURES, [float(x) for x in pi.importances_mean]), key=lambda x: -x[1]))
except Exception as e:
    print("permutation importance skipped:", e)

metrics = {
    "n_rows_raw": int(n_raw),
    "n_duplicate_rows_dropped": int(n_dupes_dropped),
    "n_temp_imputed": int(df["temperature_c"].isna().sum()),
    "n_vib_imputed": int(df["vibration_mm_s"].isna().sum()),
    "total_failure_events": int(df["failure_event"].sum()),
    "lookahead_hours": LOOKAHEAD_HOURS,
    "train_positive_rate": round(float(pos_rate), 5),
    "holdout_days": HOLDOUT_DAYS,
    "pr_auc": round(float(pr_auc), 4),
    "random_baseline_pr_auc": round(float(random_baseline), 5),
    "feature_importance_permutation": perm_importance,
}
print(json.dumps(metrics, indent=2))
with open(OUT_METRICS, "w") as f:
    json.dump(metrics, f, indent=2)

# ---------------------------------------------------------------------------
# 5. Score latest snapshot per machine + build risk trend + drivers
# ---------------------------------------------------------------------------
latest_ts = clean["timestamp"].max()
records = []
for mid, g in clean.groupby("machine_id"):
    g = g.sort_values("timestamp")
    is_new = mid in NEW_MACHINES
    last_row = g.iloc[-1]

    if is_new or pd.isna(last_row[FEATURES]).any():
        # Cold start: not enough hours for a stable rolling baseline / z-score.
        risk_score = None
        confidence = "low"
        method = "insufficient history for model (using raw thresholds)"
        flag = "monitor_new_asset"
    else:
        row_feats = last_row[FEATURES].to_frame().T.astype(float)
        risk_score = float(model.predict_proba(row_feats)[:, 1][0])
        confidence = "high"
        method = "24h-lookahead LogisticRegression classifier"
        if risk_score >= 0.5:
            flag = "high_risk"
        elif risk_score >= 0.2:
            flag = "watch"
        else:
            flag = "ok"

    # 14-day risk trend (daily max risk score) for established machines
    trend = []
    if not is_new:
        g_recent = g[g["timestamp"] >= latest_ts - pd.Timedelta(days=14)].dropna(subset=FEATURES)
        if len(g_recent) > 0:
            feats = g_recent[FEATURES].astype(float)
            g_recent = g_recent.assign(risk=model.predict_proba(feats)[:, 1])
            daily = g_recent.set_index("timestamp")["risk"].resample("D").max()
            trend = [{"date": d.strftime("%Y-%m-%d"), "risk_score": round(float(v), 3)} for d, v in daily.items()]

    records.append({
        "machine_id": mid,
        "line": last_row["line"],
        "is_new_machine": is_new,
        "confidence": confidence,
        "method": method,
        "flag": flag,
        "risk_score": round(risk_score, 3) if risk_score is not None else None,
        "as_of": latest_ts.strftime("%Y-%m-%d %H:%M"),
        "current_temperature_c": round(float(last_row["temperature_c"]), 1),
        "current_vibration_mm_s": round(float(last_row["vibration_mm_s"]), 3),
        "run_hours_since_maintenance": int(last_row["run_hours_since_maintenance"]),
        "temp_baseline_mean_c": round(float(last_row["temp_base_mean"]), 1) if not is_new else None,
        "vib_baseline_mean": round(float(last_row["vib_base_mean"]), 3) if not is_new else None,
        "temp_zscore": round(float(last_row["temp_zscore"]), 2) if not pd.isna(last_row["temp_zscore"]) else None,
        "vib_zscore": round(float(last_row["vib_zscore"]), 2) if not pd.isna(last_row["vib_zscore"]) else None,
        "temp_change_6h_c": round(float(last_row["temp_trend_6h"]), 2) if not pd.isna(last_row["temp_trend_6h"]) else None,
        "vib_change_6h": round(float(last_row["vib_trend_6h"]), 3) if not pd.isna(last_row["vib_trend_6h"]) else None,
        "total_historical_failures": int(g["failure_event"].sum()),
        "risk_trend_14d": trend,
        "sensor_history_72h": [
            {"timestamp": t.strftime("%Y-%m-%d %H:%M"), "temperature_c": round(float(tc), 1), "vibration_mm_s": round(float(v), 3)}
            for t, tc, v in zip(
                g["timestamp"].tail(72), g["temperature_c"].tail(72), g["vibration_mm_s"].tail(72)
            )
        ],
    })

flag_rank = {"high_risk": 0, "watch": 1, "monitor_new_asset": 2, "ok": 3}
records.sort(key=lambda r: flag_rank.get(r["flag"], 9))

# ---------------------------------------------------------------------------
# 6. Historical incident examples: show the model's risk score in the 48h
#    before each real past failure -> validation evidence + demo material.
# ---------------------------------------------------------------------------
incidents = []
for mid, g in clean.groupby("machine_id"):
    g = g.sort_values("timestamp").dropna(subset=FEATURES).reset_index(drop=True)
    if len(g) == 0:
        continue
    feats = g[FEATURES].astype(float)
    g = g.assign(risk=model.predict_proba(feats)[:, 1])
    fail_times = clean.loc[(clean.machine_id == mid) & (clean.failure_event == 1), "timestamp"]
    for ft in fail_times:
        window = g[(g["timestamp"] >= ft - pd.Timedelta(hours=48)) & (g["timestamp"] <= ft)]
        if len(window) == 0:
            continue
        incidents.append({
            "machine_id": mid,
            "failure_time": ft.strftime("%Y-%m-%d %H:%M"),
            "risk_score_48h_before": round(float(window["risk"].iloc[0]), 3),
            "risk_score_at_failure": round(float(window["risk"].iloc[-1]), 3),
            "hourly_trend": [
                {"hours_before_failure": int((ft - t).total_seconds() // 3600), "risk_score": round(float(r), 3)}
                for t, r in zip(window["timestamp"], window["risk"])
            ][::6],  # every 6th hour to keep payload small
        })

with open(OUT_INCIDENTS, "w") as f:
    json.dump(incidents, f, indent=2)
print(f"Wrote {len(incidents)} historical incident examples for validation")

with open(OUT_ARTIFACT, "w") as f:
    json.dump({"as_of": latest_ts.strftime("%Y-%m-%d %H:%M"), "machines": records}, f, indent=2)

print(f"\nWrote {len(records)} machine records to {OUT_ARTIFACT}")
print(f"Flags: {pd.Series([r['flag'] for r in records]).value_counts().to_dict()}")
