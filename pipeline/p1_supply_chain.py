"""
Project 1 — Supply Chain demand forecasting & flagging pipeline.

Steps:
1. Load + clean raw data (normalize categories, dedupe, repair/interpolate gaps)
2. Feature engineering (lags, rolling stats, day-of-week, category cohort stats)
3. Train a single pooled ExtraTreesRegressor over the one-hot category feature matrix
   across all established SKUs -> generalizes to new SKUs via category cohort behavior
4. Time-based validation vs a naive baseline, report WAPE
5. Compute per-SKU flags (stockout / overstock risk) using the forecast + lead time
6. Export a JSON artifact per SKU for the app + a summary metrics file
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error

HERE = Path(__file__).resolve().parent
RAW_PATH = HERE / "data" / "project1_supply_chain_demand.csv"
OUT_ARTIFACT = HERE.parent / "app-supply-chain" / "data" / "p1_supply_chain.json"
OUT_METRICS = HERE.parent / "docs" / "p1_metrics.json"

NEW_SKUS = {"SKU-2000", "SKU-2001", "SKU-2002"}
HOLDOUT_DAYS = 21  # last 3 weeks for time-based validation on established SKUs

# ---------------------------------------------------------------------------
# 1. Load + clean
# ---------------------------------------------------------------------------
df = pd.read_csv(RAW_PATH, parse_dates=["date"])
n_raw = len(df)

# Normalize category casing
df["category"] = df["category"].str.strip().str.title()

# Drop exact duplicate (date, sku) rows, keep first occurrence
before = len(df)
df = df.drop_duplicates(subset=["date", "sku_id"], keep="first")
n_dupes_dropped = before - len(df)

# Lead time: established SKUs have a single consistent value; new SKUs are noisy.
# Use the mode (most frequent value) per SKU as the "trusted" lead time.
lead_time_map = df.groupby("sku_id")["lead_time_days"].agg(lambda s: s.mode().iloc[0]).to_dict()
lead_time_reliable = {
    sku: (df[df.sku_id == sku]["lead_time_days"].nunique() == 1) for sku in df.sku_id.unique()
}

df = df.sort_values(["sku_id", "date"]).reset_index(drop=True)

cleaned_rows = []
for sku, g in df.groupby("sku_id"):
    g = g.set_index("date").sort_index()
    # Reindex to full daily range in case of missing calendar days (defensive)
    full_idx = pd.date_range(g.index.min(), g.index.max(), freq="D")
    g = g.reindex(full_idx)
    g["sku_id"] = sku
    g["category"] = g["category"].ffill().bfill()
    g["lead_time_days"] = lead_time_map[sku]
    g["units_received"] = g["units_received"].fillna(0)

    # Interpolate missing units_sold (linear, time-based)
    g["units_sold"] = g["units_sold"].interpolate(method="linear").bfill().ffill()

    # Repair missing closing_stock via inventory identity where possible:
    # closing_stock[t] = closing_stock[t-1] + received[t] - sold[t]
    stock = g["closing_stock"].copy()
    for i in range(1, len(stock)):
        if pd.isna(stock.iloc[i]) and not pd.isna(stock.iloc[i - 1]):
            implied = stock.iloc[i - 1] + g["units_received"].iloc[i] - g["units_sold"].iloc[i]
            stock.iloc[i] = max(implied, 0)
    stock = stock.interpolate(method="linear").bfill().ffill()
    g["closing_stock"] = stock

    cleaned_rows.append(g)

clean = pd.concat(cleaned_rows).rename_axis("date").reset_index()

# ---------------------------------------------------------------------------
# 2. Feature engineering
# ---------------------------------------------------------------------------
clean = clean.sort_values(["sku_id", "date"]).reset_index(drop=True)
clean["dow"] = clean["date"].dt.dayofweek

for w in (7, 14, 28):
    clean[f"roll_mean_{w}"] = (
        clean.groupby("sku_id")["units_sold"].transform(lambda s: s.shift(1).rolling(w, min_periods=1).mean())
    )
    clean[f"roll_std_{w}"] = (
        clean.groupby("sku_id")["units_sold"].transform(lambda s: s.shift(1).rolling(w, min_periods=2).std())
    )

clean["lag_1"] = clean.groupby("sku_id")["units_sold"].shift(1)
clean["lag_7"] = clean.groupby("sku_id")["units_sold"].shift(7)

# Category-cohort daily average (helps cold-start new SKUs)
cat_daily = clean.groupby(["category", "date"])["units_sold"].mean().rename("cat_avg_demand")
clean = clean.merge(cat_daily, on=["category", "date"], how="left")
clean["cat_roll_mean_14"] = (
    clean.groupby("category")["cat_avg_demand"].transform(lambda s: s.shift(1).rolling(14, min_periods=1).mean())
)

clean["is_new_sku"] = clean["sku_id"].isin(NEW_SKUS)

FEATURES = [
    "dow", "roll_mean_7", "roll_mean_14", "roll_mean_28",
    "roll_std_7", "roll_std_14", "roll_std_28",
    "lag_1", "lag_7", "cat_roll_mean_14", "lead_time_days",
]
CAT_FEATURES = ["category"]

model_df = clean.dropna(subset=["roll_mean_7", "lag_1"]).copy()
for c in CAT_FEATURES:
    model_df[c] = model_df[c].astype("category")

established = model_df[~model_df.sku_id.isin(NEW_SKUS)]
cutoff = established["date"].max() - pd.Timedelta(days=HOLDOUT_DAYS)
train = established[established["date"] <= cutoff]
test = established[established["date"] > cutoff]

# ---------------------------------------------------------------------------
# 3. Train pooled model using the notebook-selected best regressor
# ---------------------------------------------------------------------------
X_train = pd.get_dummies(train[FEATURES + CAT_FEATURES], columns=CAT_FEATURES, dtype=float)
X_test = pd.get_dummies(test[FEATURES + CAT_FEATURES], columns=CAT_FEATURES, dtype=float)
X_test = X_test.reindex(columns=X_train.columns, fill_value=0)

model = ExtraTreesRegressor(
    n_estimators=140, max_depth=8, min_samples_leaf=2,
    random_state=42,
)
model.fit(X_train, train["units_sold"])

# ---------------------------------------------------------------------------
# 4. Validation vs naive baseline (repeat last 7-day average)
# ---------------------------------------------------------------------------
test = test.copy()
test["pred"] = np.clip(model.predict(X_test), 0, None)
test["naive_pred"] = test["roll_mean_7"]

def wape(y_true, y_pred):
    return np.abs(y_true - y_pred).sum() / np.abs(y_true).sum()

feature_importances = dict(sorted(
    zip(X_train.columns, [float(x) for x in model.feature_importances_]),
    key=lambda x: -x[1]
))

metrics = {
    "n_rows_raw": int(n_raw),
    "n_duplicate_rows_dropped": int(n_dupes_dropped),
    "n_units_sold_imputed": int(df["units_sold"].isna().sum()),
    "n_closing_stock_imputed": int(df["closing_stock"].isna().sum()),
    "holdout_days": HOLDOUT_DAYS,
    "model_wape": round(float(wape(test["units_sold"], test["pred"])), 4),
    "naive_baseline_wape": round(float(wape(test["units_sold"], test["naive_pred"])), 4),
    "model_mae": round(float(mean_absolute_error(test["units_sold"], test["pred"])), 2),
    "naive_mae": round(float(mean_absolute_error(test["units_sold"], test["naive_pred"])), 2),
    "feature_importances": feature_importances,
}
print(json.dumps(metrics, indent=2))
with open(OUT_METRICS, "w") as f:
    json.dump(metrics, f, indent=2)

# ---------------------------------------------------------------------------
# 5. Score every SKU as of the latest date -> stockout / overstock flags
# ---------------------------------------------------------------------------
latest_date = clean["date"].max()
latest_rows = model_df[model_df["date"] == latest_date].copy()

records = []
for _, row in latest_rows.iterrows():
    sku = row["sku_id"]
    is_new = sku in NEW_SKUS
    hist = clean[clean.sku_id == sku].sort_values("date")

    if is_new:
        # Cold start: not enough history for the pooled model to be reliable on its
        # own lags (few lag_7 points) -> lean on category cohort run-rate, flagged
        # as low confidence.
        recent_avg = hist["units_sold"].tail(min(len(hist), 14)).mean()
        forecast_daily = float(row["cat_roll_mean_14"]) if not pd.isna(row["cat_roll_mean_14"]) else recent_avg
        confidence = "low"
        method = "category cohort run-rate (insufficient history for model)"
    else:
        pred_row = row[FEATURES + CAT_FEATURES].to_frame().T
        pred_onehot = pd.get_dummies(pred_row, columns=CAT_FEATURES, dtype=float)
        pred_onehot = pred_onehot.reindex(columns=X_train.columns, fill_value=0)
        forecast_daily = float(np.clip(model.predict(pred_onehot)[0], 0, None))
        confidence = "high" if lead_time_reliable.get(sku, True) else "medium"
        method = "pooled ExtraTrees demand forecast"

    lead_time = int(row["lead_time_days"])
    projected_demand_over_lead_time = forecast_daily * lead_time
    closing_stock = float(row["closing_stock"])
    days_of_cover = closing_stock / forecast_daily if forecast_daily > 0 else float("inf")

    # Overstock heuristic: holding stock worth more than 2x what's needed to cover
    # the replenishment lead time ties up working capital beyond a normal safety
    # buffer (~1-1.5x lead-time demand is typical practice).
    stock_to_lead_demand_ratio = (
        closing_stock / projected_demand_over_lead_time if projected_demand_over_lead_time > 0 else float("inf")
    )
    if closing_stock < projected_demand_over_lead_time:
        flag = "stockout_risk"
    elif stock_to_lead_demand_ratio > 2.0:
        flag = "overstock_risk"
    else:
        flag = "ok"

    recent_trend = hist["units_sold"].tail(14).mean() - hist["units_sold"].tail(28).head(14).mean() \
        if len(hist) >= 28 else np.nan

    records.append({
        "sku_id": sku,
        "category": row["category"],
        "is_new_sku": is_new,
        "confidence": confidence,
        "forecast_method": method,
        "lead_time_days": lead_time,
        "lead_time_reliable": bool(lead_time_reliable.get(sku, True)),
        "closing_stock": round(closing_stock, 1),
        "forecast_daily_demand": round(forecast_daily, 1),
        "projected_demand_over_lead_time": round(projected_demand_over_lead_time, 1),
        "days_of_cover": round(days_of_cover, 1) if np.isfinite(days_of_cover) else None,
        "stock_to_lead_demand_ratio": round(stock_to_lead_demand_ratio, 2) if np.isfinite(stock_to_lead_demand_ratio) else None,
        "flag": flag,
        "recent_14d_avg_sold": round(float(hist["units_sold"].tail(14).mean()), 1),
        "recent_trend_units_per_day": round(float(recent_trend), 2) if not pd.isna(recent_trend) else None,
        "history": [
            {"date": d.strftime("%Y-%m-%d"), "units_sold": round(float(u), 1), "closing_stock": round(float(c), 1)}
            for d, u, c in zip(hist["date"].tail(60), hist["units_sold"].tail(60), hist["closing_stock"].tail(60))
        ],
    })

records.sort(key=lambda r: (r["flag"] != "stockout_risk", r["flag"] != "overstock_risk", r["sku_id"]))

with open(OUT_ARTIFACT, "w") as f:
    json.dump({"as_of_date": latest_date.strftime("%Y-%m-%d"), "skus": records}, f, indent=2)

print(f"\nWrote {len(records)} SKU records to {OUT_ARTIFACT}")
print(f"Flags: {pd.Series([r['flag'] for r in records]).value_counts().to_dict()}")
