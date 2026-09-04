"""HARP-style point verification engine."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class VerificationResult:
    parameter: str
    model: str
    lead_time: int
    n_cases: int
    n_stations: int
    bias: float
    rmse: float
    mae: float
    stde: float
    correlation: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter": self.parameter,
            "model": self.model,
            "lead_time": self.lead_time,
            "n_cases": self.n_cases,
            "n_stations": self.n_stations,
            "bias": round(self.bias, 4) if not math.isnan(self.bias) else None,
            "rmse": round(self.rmse, 4) if not math.isnan(self.rmse) else None,
            "mae": round(self.mae, 4) if not math.isnan(self.mae) else None,
            "stde": round(self.stde, 4) if not math.isnan(self.stde) else None,
            "correlation": round(self.correlation, 4) if not math.isnan(self.correlation) else None,
        }


def _circular_error(obs_deg: np.ndarray, fcst_deg: np.ndarray) -> np.ndarray:
    diff = fcst_deg - obs_deg
    return ((diff + 180) % 360) - 180


def check_obs_against_fcst(
    df: pd.DataFrame,
    fcst_col: str = "fcst",
    obs_col: str = "obs",
    num_sd: float = 4.0,
    circular: bool = False,
) -> pd.DataFrame:
    from backend.services.obs_qc import drop_obs_sentinels

    if df.empty:
        return df
    df = drop_obs_sentinels(df, col=obs_col)
    if df.empty:
        return df
    err = _circular_error(df[obs_col].values, df[fcst_col].values) if circular else (df[fcst_col] - df[obs_col])
    sd = err.std()
    if sd == 0 or math.isnan(sd):
        return df
    mask = np.abs(err) <= num_sd * sd
    return df.loc[mask].copy()


def common_cases(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    """Inner join across models on station_id + valid_time."""
    if not dfs:
        return pd.DataFrame()
    keys = ["station_id", "valid_time"]
    merged = dfs[0][keys + ["obs"]].drop_duplicates(keys)
    for d in dfs[1:]:
        merged = merged.merge(d[keys + ["fcst"]].drop_duplicates(keys), on=keys, how="inner")
    return merged


def det_verify(
    df: pd.DataFrame,
    parameter: str,
    model: str,
    lead_time: int,
    circular: bool = False,
) -> VerificationResult | None:
    if df.empty or len(df) < 2:
        return None

    fcst = df["fcst"].astype(float).values
    obs = df["obs"].astype(float).values
    err = _circular_error(obs, fcst) if circular else (fcst - obs)

    n = len(err)
    bias = float(np.mean(err))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    mae = float(np.mean(np.abs(err)))
    stde = float(np.std(err, ddof=1)) if n > 1 else 0.0

    if np.std(fcst) > 0 and np.std(obs) > 0:
        correlation = float(np.corrcoef(fcst, obs)[0, 1])
    else:
        correlation = float("nan")

    return VerificationResult(
        parameter=parameter,
        model=model,
        lead_time=lead_time,
        n_cases=n,
        n_stations=int(df["station_id"].nunique()),
        bias=bias,
        rmse=rmse,
        mae=mae,
        stde=stde,
        correlation=correlation,
    )


def compute_ranking(
    results: list[VerificationResult],
    score: str = "rmse",
    lower_is_better: bool = True,
) -> list[dict[str, Any]]:
    """Rank models by HARP det_summary_scores aggregated across parameters and lead times."""
    if not results:
        return []

    df = pd.DataFrame([r.to_dict() for r in results])
    agg = df.groupby("model").agg(
        mean_rmse=("rmse", "mean"),
        mean_mae=("mae", "mean"),
        mean_bias=("bias", "mean"),
        mean_stde=("stde", "mean"),
        mean_correlation=("correlation", "mean"),
        total_cases=("n_cases", "sum"),
        total_stations=("n_stations", "max"),
    ).reset_index()

    col = f"mean_{score}" if score in ("rmse", "mae", "bias", "stde", "correlation") else "mean_rmse"
    if col not in agg.columns:
        col = "mean_rmse"

    ascending = lower_is_better if score != "correlation" else False
    agg = agg.sort_values(col, ascending=ascending).reset_index(drop=True)
    agg["rank"] = range(1, len(agg) + 1)

    return agg.round(4).to_dict(orient="records")


def build_verification_pairs(
    obs_df: pd.DataFrame,
    fcst_df: pd.DataFrame,
    parameter: str,
    circular: bool = False,
) -> pd.DataFrame:
    """Join forecast to observations (HARP join_to_fcst)."""
    from backend.services.obs_qc import drop_obs_sentinels, sanitize_obs_value
    from backend.services.time_utils import normalize_valid_time

    keys = ["station_id", "valid_time"]
    obs_sub = obs_df[obs_df["parameter"] == parameter][keys + ["value"]].copy()
    fcst_sub = fcst_df.copy()
    obs_sub["valid_time"] = obs_sub["valid_time"].map(normalize_valid_time)
    fcst_sub["valid_time"] = fcst_sub["valid_time"].map(normalize_valid_time)
    # Sentinel BMKG (8888/9999) → NaN sebelum join
    obs_sub["value"] = obs_sub["value"].map(sanitize_obs_value)
    obs_sub = obs_sub.dropna(subset=["value"])
    merged = fcst_sub.merge(
        obs_sub.rename(columns={"value": "obs"}),
        on=keys,
        how="inner",
    )
    merged = merged.rename(columns={"value": "fcst"}) if "value" in merged.columns else merged
    if "fcst" not in merged.columns and "forecast" in merged.columns:
        merged = merged.rename(columns={"forecast": "fcst"})

    merged = merged.dropna(subset=["fcst", "obs"])
    merged = drop_obs_sentinels(merged, col="obs")
    merged = check_obs_against_fcst(merged, circular=circular)
    return merged
