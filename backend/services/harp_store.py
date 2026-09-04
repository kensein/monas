"""PSIIDN-style HARP artifact store: float32 arrays + JSON index. No SQLite.

Layout (ARTIFACTS_DIR, default data/artifacts):

    obs/<YYYYMM>/index.json          station_ids, params, month, hour axis, source files
    obs/<YYYYMM>/values.f32          float32 [n_hour, n_station, n_param]  (NaN = missing)

    runs/<model>/<YYYYMMDDHH>/meta.json   model, init_time, params, lead_times, station_ids, nc info
    runs/<model>/<YYYYMMDDHH>/fcst.f32    float32 [n_param, n_lead, n_station]
    runs/<model>/<YYYYMMDDHH>/obs.f32     float32 [n_param, n_lead, n_station]  paired obs at valid time
    runs/<model>/<YYYYMMDDHH>/scores.f32  float32 [n_param, n_lead, 7]
                                          (bias, rmse, mae, stde, correlation, n_cases, n_stations)

    manifest.json                     index of all runs + stations + params + exported_at

webpsi copies the whole folder (rsync) and serves it readonly. Readers are
stateless: they re-read manifest.json when its mtime changes.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from backend.config import ARTIFACTS_DIR, MAX_LEAD_TIME_HOURS, MODELS, VERIFY_PARAMETERS

SCORE_METRICS = ("bias", "rmse", "mae", "stde", "correlation", "n_cases", "n_stations")
F32 = np.float32


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def store_root() -> Path:
    root = Path(os.getenv("HARP_STORE_DIR", "") or ARTIFACTS_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def obs_dir(month: str) -> Path:
    return store_root() / "obs" / month


def runs_dir() -> Path:
    return store_root() / "runs"


def run_dir(model: str, init_time: str) -> Path:
    return runs_dir() / model / init_tag(init_time)


def manifest_path() -> Path:
    return store_root() / "manifest.json"


def init_tag(init_time: str) -> str:
    dt = _parse_dt(init_time)
    return dt.strftime("%Y%m%d%H")


def _parse_dt(ts: str | datetime) -> datetime:
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=None)
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1]
    if "+" in s[10:]:
        s = s[: s.index("+", 10)]
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_plain(dt: datetime) -> str:
    """Format init_time seperti di DB lama (tanpa Z) agar frontend tetap cocok."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Low-level f32 IO
# ---------------------------------------------------------------------------

def write_f32(path: Path, arr: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    np.ascontiguousarray(arr, dtype=F32).tofile(tmp)
    os.replace(tmp, path)


def read_f32(path: Path, shape: tuple[int, ...]) -> np.ndarray:
    n = int(np.prod(shape))
    arr = np.fromfile(path, dtype=F32, count=n)
    if arr.size != n:
        raise ValueError(f"{path}: expected {n} float32, got {arr.size}")
    return arr.reshape(shape)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Station axis (stable ordering from catalog)
# ---------------------------------------------------------------------------

def station_frame() -> pd.DataFrame:
    from backend.services.station_catalog import catalog_to_dataframe

    df = catalog_to_dataframe()
    if df.empty:
        return pd.DataFrame(columns=["station_id", "name", "lat", "lon", "region"])
    df = df.dropna(subset=["lat", "lon"]).copy()
    df["station_id"] = df["station_id"].astype(str)
    return df.sort_values("station_id").reset_index(drop=True)


def station_ids() -> list[str]:
    return station_frame()["station_id"].tolist()


# ---------------------------------------------------------------------------
# Observations: monthly hourly cubes from sinoptik JSON (filtered params)
# ---------------------------------------------------------------------------

def _month_hours(month: str) -> int:
    start = datetime.strptime(month, "%Y%m")
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return int((nxt - start).total_seconds() // 3600)


def _hour_index(dt: datetime, month_start: datetime) -> int:
    return int((dt.replace(minute=0, second=0, microsecond=0) - month_start).total_seconds() // 3600)


def _json_month_span(name: str) -> tuple[str, str] | None:
    import re

    m = re.match(r"sinoptik_(\d{8})_(\d{8})", name)
    if not m:
        return None
    return m.group(1)[:6], m.group(2)[:6]


def obs_json_files(directory: Path, months: Iterable[str]) -> dict[str, list[Path]]:
    """Map month → JSON files whose date span overlaps that month."""
    wanted = set(months)
    out: dict[str, list[Path]] = {m: [] for m in wanted}
    for p in sorted(Path(directory).glob("sinoptik_*.json")):
        span = _json_month_span(p.name)
        if not span:
            continue
        a, b = span
        for m in wanted:
            if a <= m <= b:
                out[m].append(p)
    return out


def _iter_obs_rows(path: Path, params: list[str]):
    """Yield (station_id, datetime, {param: value}) using only requested params."""
    from backend.services.obs_format import _station_id_from_row, flatten_sinoptik_records
    from backend.services.time_utils import normalize_valid_time

    raw = json.loads(path.read_text(encoding="utf-8"))
    records = flatten_sinoptik_records(raw)
    for rec in records:
        if not isinstance(rec, dict):
            continue
        sid = str(
            rec.get("station_wmo_id") or rec.get("wmo_id") or rec.get("station_id")
            or rec.get("stationWmoId") or rec.get("wmo") or ""
        )
        if not sid and rec.get("station_name"):
            sid = _station_id_from_row(rec)
        if not sid:
            continue
        vt = rec.get("data_timestamp") or rec.get("valid_time") or rec.get("timestamp")
        vt = normalize_valid_time(vt)
        if not vt:
            continue
        vals: dict[str, float] = {}
        for p in params:
            v = rec.get(p)
            if v is None or v == "" or v == "-":
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            from backend.services.obs_qc import is_obs_sentinel
            if is_obs_sentinel(fv):
                continue
            vals[p] = fv
        if vals:
            yield sid, _parse_dt(vt), vals


def obs_month_is_fresh(month: str, files: list[Path]) -> bool:
    idx = obs_dir(month) / "index.json"
    val = obs_dir(month) / "values.f32"
    if not idx.is_file() or not val.is_file():
        return False
    try:
        meta = read_json(idx)
    except Exception:
        return False
    src = meta.get("source_files") or {}
    if set(src.keys()) != {p.name for p in files}:
        return False
    for p in files:
        if int(p.stat().st_mtime) > int(src.get(p.name, {}).get("mtime", 0)):
            return False
    if meta.get("params") != list(VERIFY_PARAMETERS.keys()):
        return False
    return True


def build_obs_month(
    month: str,
    files: list[Path],
    log=None,
) -> dict[str, Any]:
    """Parse JSON files → hourly f32 cube for one month (only VERIFY_PARAMETERS)."""
    log = log or (lambda *_: None)
    params = list(VERIFY_PARAMETERS.keys())
    sids = station_ids()
    sidx = {s: i for i, s in enumerate(sids)}
    pidx = {p: i for i, p in enumerate(params)}
    month_start = datetime.strptime(month, "%Y%m")
    n_hour = _month_hours(month)
    cube = np.full((n_hour, len(sids), len(params)), np.nan, dtype=F32)

    n_rows = 0
    n_skip_station = 0
    for i, f in enumerate(files, 1):
        log(f"[obs {month}] ({i}/{len(files)}) {f.name} ({f.stat().st_size / 1e6:.0f} MB)")
        for sid, dt, vals in _iter_obs_rows(f, params):
            if dt < month_start or dt.strftime("%Y%m") != month:
                continue
            si = sidx.get(sid)
            if si is None:
                n_skip_station += 1
                continue
            hi = _hour_index(dt, month_start)
            if hi < 0 or hi >= n_hour:
                continue
            for p, v in vals.items():
                cube[hi, si, pidx[p]] = v
            n_rows += 1

    write_f32(obs_dir(month) / "values.f32", cube)
    meta = {
        "month": month,
        "month_start": iso_z(month_start),
        "n_hour": n_hour,
        "station_ids": sids,
        "params": params,
        "source_files": {p.name: {"mtime": int(p.stat().st_mtime), "size": p.stat().st_size} for p in files},
        "rows": n_rows,
        "skipped_unknown_station": n_skip_station,
        "built_at": iso_z(datetime.utcnow()),
    }
    write_json(obs_dir(month) / "index.json", meta)
    log(f"[obs {month}] rows={n_rows} unknown_station={n_skip_station} → {obs_dir(month)}")
    return meta


def ensure_obs_months(
    obs_json_dir: Path,
    months: Iterable[str],
    force: bool = False,
    log=None,
) -> dict[str, Any]:
    log = log or (lambda *_: None)
    months = sorted(set(months))
    files_by_month = obs_json_files(obs_json_dir, months)
    result: dict[str, Any] = {}
    for m in months:
        files = files_by_month.get(m, [])
        if not files:
            log(f"[obs {m}] tidak ada sinoptik JSON yang mencakup bulan ini di {obs_json_dir}")
            result[m] = {"status": "missing"}
            continue
        if not force and obs_month_is_fresh(m, files):
            log(f"[obs {m}] cache f32 masih fresh — skip parse JSON")
            result[m] = {"status": "cached"}
            continue
        meta = build_obs_month(m, files, log=log)
        result[m] = {"status": "built", "rows": meta["rows"]}
    return result


class ObsCube:
    """Lazy reader across months: value(station_idx, param_idx, datetime)."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[dict[str, Any], np.ndarray] | None] = {}
        self._sids = station_ids()
        self._sidx = {s: i for i, s in enumerate(self._sids)}
        self._params = list(VERIFY_PARAMETERS.keys())
        self._pidx = {p: i for i, p in enumerate(self._params)}

    @property
    def station_ids(self) -> list[str]:
        return self._sids

    @property
    def params(self) -> list[str]:
        return self._params

    def _load(self, month: str):
        if month in self._cache:
            return self._cache[month]
        idx = obs_dir(month) / "index.json"
        val = obs_dir(month) / "values.f32"
        if not idx.is_file() or not val.is_file():
            self._cache[month] = None
            return None
        meta = read_json(idx)
        cube = read_f32(val, (meta["n_hour"], len(meta["station_ids"]), len(meta["params"])))
        # Remap if station/param axes differ from current catalog
        if meta["station_ids"] != self._sids or meta["params"] != self._params:
            remap = np.full((meta["n_hour"], len(self._sids), len(self._params)), np.nan, dtype=F32)
            s_old = {s: i for i, s in enumerate(meta["station_ids"])}
            p_old = {p: i for i, p in enumerate(meta["params"])}
            for s, i_new in self._sidx.items():
                i_old = s_old.get(s)
                if i_old is None:
                    continue
                for p, j_new in self._pidx.items():
                    j_old = p_old.get(p)
                    if j_old is not None:
                        remap[:, i_new, j_new] = cube[:, i_old, j_old]
            cube = remap
        self._cache[month] = (meta, cube)
        return self._cache[month]

    def slice_at(self, when: datetime, param: str) -> np.ndarray:
        """Obs for all stations at hour `when` → float32 [n_station] (NaN missing)."""
        from backend.services.obs_qc import mask_obs_sentinels

        month = when.strftime("%Y%m")
        loaded = self._load(month)
        out = np.full(len(self._sids), np.nan, dtype=F32)
        if loaded is None:
            return out
        meta, cube = loaded
        hi = _hour_index(when, _parse_dt(meta["month_start"]))
        if hi < 0 or hi >= meta["n_hour"]:
            return out
        raw = cube[hi, :, self._pidx[param]]
        return mask_obs_sentinels(raw).astype(F32)

    def series(self, station_id: str, param: str, start: datetime, end: datetime) -> list[tuple[str, float]]:
        from backend.services.obs_qc import is_obs_sentinel

        si = self._sidx.get(str(station_id))
        if si is None:
            return []
        pi = self._pidx.get(param)
        if pi is None:
            return []
        out: list[tuple[str, float]] = []
        cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cur <= end:
            month = cur.strftime("%Y%m")
            loaded = self._load(month)
            if loaded is not None:
                meta, cube = loaded
                ms = _parse_dt(meta["month_start"])
                col = cube[:, si, pi]
                idx = np.where(~np.isnan(col))[0]
                for hi in idx:
                    t = ms + timedelta(hours=int(hi))
                    if start <= t <= end:
                        v = float(col[hi])
                        if is_obs_sentinel(v):
                            continue
                        out.append((iso_z(t), v))
            cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        return out


# ---------------------------------------------------------------------------
# Runs: write/read
# ---------------------------------------------------------------------------

def write_run(
    model: str,
    init_time: str,
    params: list[str],
    lead_times: list[int],
    sids: list[str],
    fcst: np.ndarray,
    obs: np.ndarray,
    scores: np.ndarray,
    nc_info: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    d = run_dir(model, init_time)
    d.mkdir(parents=True, exist_ok=True)
    write_f32(d / "fcst.f32", fcst)
    write_f32(d / "obs.f32", obs)
    write_f32(d / "scores.f32", scores)
    init_dt = _parse_dt(init_time)
    meta = {
        "model": model,
        "init_time": iso_plain(init_dt),
        "init_tag": init_tag(init_time),
        "params": params,
        "lead_times": [int(x) for x in lead_times],
        "station_ids": sids,
        "n_param": len(params),
        "n_lead": len(lead_times),
        "n_station": len(sids),
        "score_metrics": list(SCORE_METRICS),
        "nc": nc_info or {},
        "computed_at": iso_z(datetime.utcnow()),
        "status": "done",
    }
    if extra:
        meta.update(extra)
    write_json(d / "meta.json", meta)
    return d


def run_exists_for_nc(model: str, init_time: str, nc_path: Path | None) -> bool:
    d = run_dir(model, init_time)
    meta_p = d / "meta.json"
    if not meta_p.is_file():
        return False
    try:
        meta = read_json(meta_p)
    except Exception:
        return False
    if meta.get("status") != "done":
        return False
    if nc_path is not None and nc_path.exists():
        nc = meta.get("nc") or {}
        if int(nc.get("size", -1)) != nc_path.stat().st_size:
            return False
    return True


class RunData:
    def __init__(self, d: Path):
        self.dir = d
        self.meta = read_json(d / "meta.json")
        self._fcst: np.ndarray | None = None
        self._obs: np.ndarray | None = None
        self._scores: np.ndarray | None = None

    @property
    def shape3(self) -> tuple[int, int, int]:
        return (self.meta["n_param"], self.meta["n_lead"], self.meta["n_station"])

    @property
    def fcst(self) -> np.ndarray:
        if self._fcst is None:
            self._fcst = read_f32(self.dir / "fcst.f32", self.shape3)
        return self._fcst

    @property
    def obs(self) -> np.ndarray:
        if self._obs is None:
            self._obs = read_f32(self.dir / "obs.f32", self.shape3)
        return self._obs

    @property
    def scores(self) -> np.ndarray:
        if self._scores is None:
            self._scores = read_f32(
                self.dir / "scores.f32", (self.meta["n_param"], self.meta["n_lead"], len(SCORE_METRICS))
            )
        return self._scores

    def param_index(self, param: str) -> int | None:
        try:
            return self.meta["params"].index(param)
        except ValueError:
            return None

    def lead_index(self, lead: int) -> int | None:
        try:
            return self.meta["lead_times"].index(int(lead))
        except ValueError:
            return None

    @property
    def init_dt(self) -> datetime:
        return _parse_dt(self.meta["init_time"])


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def rebuild_manifest() -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for meta_p in sorted(runs_dir().glob("*/*/meta.json")):
        try:
            m = read_json(meta_p)
        except Exception:
            continue
        runs.append({
            "model": m.get("model"),
            "init_time": m.get("init_time"),
            "init_tag": m.get("init_tag"),
            "status": m.get("status", "done"),
            "nc_filename": (m.get("nc") or {}).get("filename"),
            "file_size": (m.get("nc") or {}).get("size"),
            "processed_at": m.get("computed_at"),
            "n_lead": m.get("n_lead"),
            "n_pairs": m.get("n_pairs"),
            "path": str(meta_p.parent.relative_to(store_root())),
        })
    runs.sort(key=lambda r: (r["init_time"] or "", r["model"] or ""), reverse=True)

    obs_months = []
    for idx in sorted((store_root() / "obs").glob("*/index.json")):
        try:
            mm = read_json(idx)
            obs_months.append({"month": mm["month"], "rows": mm.get("rows"), "built_at": mm.get("built_at")})
        except Exception:
            continue

    st = station_frame()
    manifest = {
        "format": "monas-harp-f32/1",
        "exported_at": iso_z(datetime.utcnow()),
        "models": MODELS,
        "params": list(VERIFY_PARAMETERS.keys()),
        "max_lead_time_hours": MAX_LEAD_TIME_HOURS,
        "runs": runs,
        "obs_months": obs_months,
        "stations": st[["station_id", "name", "lat", "lon", "region"]].to_dict(orient="records"),
        "n_stations": int(len(st)),
    }
    write_json(manifest_path(), manifest)
    return manifest


_manifest_lock = threading.Lock()
_manifest_cache: dict[str, Any] = {"mtime": None, "data": None, "runs": {}}


def load_manifest() -> dict[str, Any]:
    p = manifest_path()
    if not p.is_file():
        return {"runs": [], "stations": [], "params": list(VERIFY_PARAMETERS.keys()), "models": MODELS}
    mtime = p.stat().st_mtime
    with _manifest_lock:
        if _manifest_cache["mtime"] != mtime:
            _manifest_cache["data"] = read_json(p)
            _manifest_cache["mtime"] = mtime
            _manifest_cache["runs"] = {}
        return _manifest_cache["data"]


def load_run(model: str, init_time: str) -> RunData | None:
    key = f"{model}|{init_tag(init_time)}"
    with _manifest_lock:
        cached = _manifest_cache["runs"].get(key)
    if cached is not None:
        return cached
    d = run_dir(model, init_time)
    if not (d / "meta.json").is_file():
        return None
    rd = RunData(d)
    with _manifest_lock:
        _manifest_cache["runs"][key] = rd
    return rd


def list_runs(models: list[str] | None = None, init_time: str | None = None) -> list[dict[str, Any]]:
    runs = load_manifest().get("runs", [])
    if models is not None:
        runs = [r for r in runs if r["model"] in models]
    if init_time:
        tag = init_tag(init_time)
        runs = [r for r in runs if r.get("init_tag") == tag]
    return runs


# ---------------------------------------------------------------------------
# Readers used by API
# ---------------------------------------------------------------------------

def scores_frame(
    models: list[str] | None = None,
    parameter: str | None = None,
    init_time: str | None = None,
    lead_time: int | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for r in list_runs(models, init_time):
        rd = load_run(r["model"], r["init_time"])
        if rd is None:
            continue
        params = [parameter] if parameter else rd.meta["params"]
        for p in params:
            pi = rd.param_index(p)
            if pi is None:
                continue
            for li, lt in enumerate(rd.meta["lead_times"]):
                if lead_time is not None and int(lt) != int(lead_time):
                    continue
                if int(lt) > MAX_LEAD_TIME_HOURS:
                    continue
                s = rd.scores[pi, li]
                if np.isnan(s[1]) and np.isnan(s[0]):
                    continue
                rows.append({
                    "model": rd.meta["model"],
                    "parameter": p,
                    "init_time": rd.meta["init_time"],
                    "lead_time": int(lt),
                    "bias": _f(s[0]), "rmse": _f(s[1]), "mae": _f(s[2]),
                    "stde": _f(s[3]), "correlation": _f(s[4]),
                    "n_cases": int(s[5]) if not np.isnan(s[5]) else 0,
                    "n_stations": int(s[6]) if not np.isnan(s[6]) else 0,
                    "computed_at": rd.meta.get("computed_at"),
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["lead_time", "model"]).reset_index(drop=True)


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else v


def aggregate_scores_over_inits(df: pd.DataFrame) -> pd.DataFrame:
    """Jika init_time tidak dipilih: rata-rata per model/param/lead (bobot n_cases)."""
    if df.empty or df["init_time"].nunique() <= 1:
        return df
    w = df["n_cases"].clip(lower=1)
    g = df.assign(
        _bias=df["bias"] * w, _rmse2=(df["rmse"] ** 2) * w, _mae=df["mae"] * w,
        _stde=df["stde"] * w, _corr=df["correlation"] * w, _w=w,
    ).groupby(["model", "parameter", "lead_time"], as_index=False).agg(
        _bias=("_bias", "sum"), _rmse2=("_rmse2", "sum"), _mae=("_mae", "sum"),
        _stde=("_stde", "sum"), _corr=("_corr", "sum"), _w=("_w", "sum"),
        n_cases=("n_cases", "sum"), n_stations=("n_stations", "max"),
        n_inits=("init_time", "nunique"),
    )
    out = pd.DataFrame({
        "model": g["model"], "parameter": g["parameter"], "lead_time": g["lead_time"],
        "init_time": None,
        "bias": g["_bias"] / g["_w"], "rmse": np.sqrt(g["_rmse2"] / g["_w"]),
        "mae": g["_mae"] / g["_w"], "stde": g["_stde"] / g["_w"],
        "correlation": g["_corr"] / g["_w"],
        "n_cases": g["n_cases"], "n_stations": g["n_stations"], "n_inits": g["n_inits"],
    })
    return out.sort_values(["lead_time", "model"]).reset_index(drop=True)


def ranking_payload(models: list[str], init_time: str | None, score: str = "rmse") -> dict[str, Any]:
    from backend.services.verification import VerificationResult, compute_ranking

    df = scores_frame(models=models, init_time=init_time)
    if df.empty:
        return {}
    results = [
        VerificationResult(
            parameter=r["parameter"], model=r["model"], lead_time=int(r["lead_time"]),
            n_cases=int(r["n_cases"]), n_stations=int(r["n_stations"]),
            bias=_nan(r["bias"]), rmse=_nan(r["rmse"]), mae=_nan(r["mae"]),
            stde=_nan(r["stde"]), correlation=_nan(r["correlation"]),
        )
        for _, r in df.iterrows()
    ]
    return {"score_metric": score, "init_time": init_time, "ranking": compute_ranking(results, score=score)}


def _nan(x: Any) -> float:
    return float("nan") if x is None else float(x)


def map_bulk(model: str, parameter: str, init_time: str | None = None) -> dict[str, Any]:
    """Per-stasiun error untuk semua lead — init spesifik atau init terbaru model."""
    runs = list_runs([model], init_time)
    if not runs:
        return {"available_lead_times": [], "records": []}
    if init_time is None:
        runs = runs[:1]
    rd = load_run(runs[0]["model"], runs[0]["init_time"])
    if rd is None:
        return {"available_lead_times": [], "records": []}
    pi = rd.param_index(parameter)
    if pi is None:
        return {"available_lead_times": [], "records": []}
    st = station_frame().set_index("station_id")
    circular = VERIFY_PARAMETERS.get(parameter, {}).get("category") == "circular"
    sids = rd.meta["station_ids"]
    records: list[dict[str, Any]] = []
    leads: list[int] = []
    from backend.services.obs_qc import OBS_SENTINEL_ABS_MIN

    for li, lt in enumerate(rd.meta["lead_times"]):
        f = rd.fcst[pi, li]
        o = rd.obs[pi, li]
        # Skip NaN + sentinel 8888/9999 already baked into older run cubes
        ok = ~np.isnan(f) & ~np.isnan(o) & (np.abs(o) < OBS_SENTINEL_ABS_MIN)
        if not ok.any():
            continue
        leads.append(int(lt))
        err = f - o
        if circular:
            err = ((err + 180) % 360) - 180
        for si in np.where(ok)[0]:
            sid = sids[si]
            meta = st.loc[sid] if sid in st.index else None
            e = float(err[si])
            records.append({
                "lead_time": int(lt),
                "station_id": sid,
                "name": None if meta is None else meta.get("name"),
                "lat": None if meta is None else _f(meta.get("lat")),
                "lon": None if meta is None else _f(meta.get("lon")),
                "bias": e, "rmse": abs(e), "mae": abs(e), "n_cases": 1,
                "obs_mean": float(o[si]), "fcst_mean": float(f[si]),
            })
    return {"available_lead_times": leads, "records": records, "init_time": rd.meta["init_time"]}


def station_series(
    station_id: str,
    parameter: str,
    models: list[str],
    lead_time: int,
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    """Kalender: obs (hourly dari cube) ∪ forecast lead L dari setiap init."""
    start = _parse_dt(date_from)
    end = _parse_dt(date_to)
    cube = ObsCube()
    entries: dict[str, dict[str, Any]] = {}
    for vt, val in cube.series(station_id, parameter, start, end):
        entries[vt] = {"valid_time": vt, "lead_time": int(lead_time), "obs": val}

    for r in list_runs(models):
        rd = load_run(r["model"], r["init_time"])
        if rd is None:
            continue
        pi = rd.param_index(parameter)
        li = rd.lead_index(lead_time)
        if pi is None or li is None:
            continue
        try:
            si = rd.meta["station_ids"].index(str(station_id))
        except ValueError:
            continue
        v = rd.fcst[pi, li, si]
        if np.isnan(v):
            continue
        vt_dt = rd.init_dt + timedelta(hours=int(lead_time))
        if vt_dt < start or vt_dt > end:
            continue
        vt = iso_z(vt_dt)
        e = entries.setdefault(vt, {"valid_time": vt, "lead_time": int(lead_time)})
        # beberapa init untuk valid_time sama → init terbaru menang (runs sudah terurut desc)
        e.setdefault(rd.meta["model"], float(v))
        from backend.services.obs_qc import is_obs_sentinel
        ob = rd.obs[pi, li, si]
        if "obs" not in e and not np.isnan(ob) and not is_obs_sentinel(ob):
            e["obs"] = float(ob)
    return [entries[k] for k in sorted(entries)]


def station_series_by_init(
    station_id: str,
    parameter: str,
    models: list[str],
    date_from: str,
    date_to: str,
    init_filter: str | None = None,
) -> dict[str, Any]:
    """Per-init forecast curves (semua lead → valid_time) + obs kalender.

    Satu entry per init cycle = garis time series D+0…D+7 sehingga init berbeda
    (mis. 1 Sep vs 2 Sep) bisa dibandingkan pola prakiraannya.
    """
    start = _parse_dt(date_from)
    end = _parse_dt(date_to)
    cube = ObsCube()
    obs_points: list[dict[str, Any]] = []
    for vt, val in cube.series(station_id, parameter, start, end):
        obs_points.append({"valid_time": vt, "obs": val})

    init_runs: list[dict[str, Any]] = []
    for r in list_runs(models):
        if init_filter and r["init_time"] != init_filter:
            continue
        rd = load_run(r["model"], r["init_time"])
        if rd is None:
            continue
        pi = rd.param_index(parameter)
        if pi is None:
            continue
        try:
            si = rd.meta["station_ids"].index(str(station_id))
        except ValueError:
            continue
        points: list[dict[str, Any]] = []
        for li, lt in enumerate(rd.meta["lead_times"]):
            v = rd.fcst[pi, li, si]
            if np.isnan(v):
                continue
            vt_dt = rd.init_dt + timedelta(hours=int(lt))
            if vt_dt < start or vt_dt > end:
                continue
            ob = rd.obs[pi, li, si]
            pt: dict[str, Any] = {
                "valid_time": iso_z(vt_dt),
                "lead_time": int(lt),
                "fcst": float(v),
            }
            from backend.services.obs_qc import is_obs_sentinel
            if not np.isnan(ob) and not is_obs_sentinel(ob):
                pt["obs"] = float(ob)
            points.append(pt)
        if points:
            init_runs.append({
                "model": r["model"],
                "init_time": rd.meta["init_time"],
                "init_tag": rd.meta.get("init_tag") or init_tag(r["init_time"]),
                "nc_filename": r.get("nc_filename"),
                "points": sorted(points, key=lambda p: p["valid_time"]),
            })
    return {"obs": obs_points, "inits": init_runs}


def cycles() -> list[dict[str, Any]]:
    return [
        {
            "model": r["model"], "init_time": r["init_time"], "nc_filename": r.get("nc_filename"),
            "status": r.get("status", "done"), "processed_at": r.get("processed_at"),
        }
        for r in load_manifest().get("runs", [])
    ]


def pipeline_status() -> dict[str, Any]:
    from backend.config import LOCAL_NC_PATH, MODEL_LOCAL_PATHS

    m = load_manifest()
    runs = m.get("runs", [])
    n_scores = 0
    for r in runs[:50]:
        rd = load_run(r["model"], r["init_time"])
        if rd is not None:
            n_scores += int(np.sum(~np.isnan(rd.scores[:, :, 1])))
    return {
        "model_runs": [
            {
                "model": r["model"], "init_time": r["init_time"], "status": r.get("status", "done"),
                "processed_at": r.get("processed_at"), "nc_filename": r.get("nc_filename"),
                "file_size": r.get("file_size"), "error_message": None,
            }
            for r in runs[:20]
        ],
        "verification_scores_count": n_scores,
        "last_pipeline": {"finished_at": m.get("exported_at"), "status": "ok"} if runs else None,
        "server_paths": {k: c["path"] for k, c in MODEL_LOCAL_PATHS.items()},
        "local_nc_path": LOCAL_NC_PATH or None,
        "max_lead_time_hours": MAX_LEAD_TIME_HOURS,
        "store": str(store_root()),
        "format": m.get("format"),
        "model_sources": {mm: ("real" if any(r["model"] == mm for r in runs) else "none") for mm in MODELS},
    }


def store_size_bytes() -> int:
    total = 0
    for p in store_root().rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def sync_items() -> list[Path]:
    """Yang perlu di-rsync ke webpsi: manifest.json + runs/ + obs/ (tanpa salinan `latest`)."""
    root = store_root()
    return [p for p in (root / "manifest.json", root / "runs", root / "obs") if p.exists()]


def prune_runs(keep_per_model: int) -> int:
    """Hapus run terlama di luar N terbaru per model (0 = simpan semua)."""
    if keep_per_model <= 0:
        return 0
    removed = 0
    for model_dir in runs_dir().glob("*"):
        if not model_dir.is_dir():
            continue
        tags = sorted((d for d in model_dir.glob("*") if d.is_dir()), key=lambda d: d.name, reverse=True)
        for old in tags[keep_per_model:]:
            shutil.rmtree(old, ignore_errors=True)
            removed += 1
    return removed
