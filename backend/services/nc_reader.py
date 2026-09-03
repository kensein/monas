"""NetCDF model forecast reader with grid-to-point interpolation."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import xarray as xr
from scipy.interpolate import RegularGridInterpolator

from backend.config import MAX_LEAD_TIME_HOURS, MODEL_VAR_MAP, NC_CHUNK_THRESHOLD_BYTES
from backend.services.time_utils import normalize_valid_time


def parse_init_time(filename: str) -> datetime | None:
    m = re.search(r"(\d{10})", Path(filename).stem)
    if not m:
        return None
    s = m.group(1)
    return datetime.strptime(s, "%Y%m%d%H")


def _find_var(ds: xr.Dataset, candidates: list[str]) -> str | None:
    lower_map = {k.lower(): k for k in ds.data_vars}
    for c in candidates:
        if c in ds.data_vars:
            return c
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def _get_coords(ds: xr.Dataset) -> tuple[np.ndarray, np.ndarray]:
    lat_names = ["lat", "latitude", "XLAT", "xlat"]
    lon_names = ["lon", "longitude", "XLONG", "xlong"]
    lat_var = lon_var = None
    for n in lat_names:
        if n in ds.coords or n in ds.data_vars:
            lat_var = n
            break
    for n in lon_names:
        if n in ds.coords or n in ds.data_vars:
            lon_var = n
            break
    if lat_var is None or lon_var is None:
        raise ValueError(f"Cannot find lat/lon in dataset. Vars: {list(ds.data_vars)[:20]}")

    lat = ds[lat_var].values
    lon = ds[lon_var].values
    if lat.ndim == 3:
        lat = lat[0]
    if lon.ndim == 3:
        lon = lon[0]
    return lat, lon


def _get_time_dim(ds: xr.Dataset) -> str | None:
    for tname in ["Time", "time", "Times"]:
        if tname in ds.coords or tname in ds.dims:
            return tname
    for dim in ds.dims:
        if "time" in dim.lower():
            return dim
    return None


def _get_times(ds: xr.Dataset, time_dim: str | None, init_time: datetime) -> list[datetime]:
    if not time_dim:
        return [init_time]
    times = ds[time_dim].values
    result = []
    for t in times:
        if isinstance(t, np.datetime64):
            result.append(pd.Timestamp(t).to_pydatetime())
        else:
            try:
                result.append(datetime.utcfromtimestamp(float(t)))
            except Exception:
                parsed = parse_init_time(str(t))
                result.append(parsed or init_time)
    return result


def _get_lead_times(ds: xr.Dataset, init_time: datetime, time_dim: str | None) -> list[int]:
    times = _get_times(ds, time_dim, init_time)
    if times and len(times) > 1:
        return [int((t - init_time).total_seconds() / 3600) for t in times]
    if time_dim and time_dim in ds.dims:
        return list(range(ds.dims[time_dim]))
    return [0]


def _filter_lead_indices(lead_times: list[int]) -> list[int]:
    """Keep only D+0 through D+7 (0–168 h)."""
    return [i for i, lt in enumerate(lead_times) if 0 <= lt <= MAX_LEAD_TIME_HOURS]


def _scalar_or_none(val: Any) -> float | None:
    """Ambil satu float dari skalar atau array 0/1 elemen."""
    arr = np.asarray(val).ravel()
    if arr.size == 0:
        return None
    x = float(arr[0])
    if np.isnan(x):
        return None
    return x


def _ensure_2d_field(field: np.ndarray) -> np.ndarray:
    """Turunkan ke 2D (lat, lon) — ambil level permukaan jika masih 3D+."""
    field = np.squeeze(field)
    while field.ndim > 2:
        field = field[0]
    return field


def _interp_field(
    field: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    station_lats: np.ndarray,
    station_lons: np.ndarray,
) -> np.ndarray:
    if field.ndim == 2:
        lat_1d = lats[:, 0] if lats.ndim == 2 else lats
        lon_1d = lons[0, :] if lons.ndim == 2 else lons
        if lat_1d[0] > lat_1d[-1]:
            lat_1d = lat_1d[::-1]
            field = field[::-1, :]
        interp = RegularGridInterpolator(
            (lat_1d, lon_1d), field, bounds_error=False, fill_value=np.nan
        )
        pts = np.column_stack([station_lats, station_lons])
        return interp(pts)

    if field.ndim == 3:
        # Level vertikal (mis. bottom_top) — gunakan level permukaan
        return _interp_field(field[0], lats, lons, station_lats, station_lons)
    raise ValueError(f"Unsupported field ndim: {field.ndim}")


def _interp_slice(
    da: xr.DataArray,
    time_dim: str | None,
    t_idx: int,
    lats: np.ndarray,
    lons: np.ndarray,
    station_lats: np.ndarray,
    station_lons: np.ndarray,
) -> np.ndarray:
    """Load one time slice and interpolate to stations (memory-safe for large NC)."""
    if time_dim and time_dim in da.dims:
        field = da.isel({time_dim: t_idx})
        if hasattr(field, "load"):
            field = field.load()
        field = _ensure_2d_field(np.asarray(field.values))
    else:
        field = da.values
        if field.ndim == 3:
            field = field[t_idx]
        field = _ensure_2d_field(np.asarray(field))
    return _interp_field(field, lats, lons, station_lats, station_lons)


def _kelvin_to_celsius(val: np.ndarray, var_name: str) -> np.ndarray:
    if var_name.upper().startswith("T") and np.nanmean(val) > 150:
        return val - 273.15
    return val


def _extract_wind_at_times(
    ds: xr.Dataset,
    model: str,
    stations: pd.DataFrame,
    lats: np.ndarray,
    lons: np.ndarray,
    time_dim: str | None,
    lead_indices: list[int],
    lead_times: list[int],
    init_time: datetime,
) -> list[dict[str, Any]]:
    """Extract wind speed/direction per time slice without loading full 3D arrays."""
    u_names = ["U10", "u10", "10u"]
    v_names = ["V10", "v10", "10v"]
    u_var = _find_var(ds, u_names)
    v_var = _find_var(ds, v_names)
    records: list[dict[str, Any]] = []
    st_lats = stations["lat"].values
    st_lons = stations["lon"].values

    if u_var and v_var:
        u_da, v_da = ds[u_var], ds[v_var]
        for li in lead_indices:
            lt = lead_times[li]
            valid_time = init_time + timedelta(hours=lt)
            u_i = _interp_slice(u_da, time_dim, li, lats, lons, st_lats, st_lons)
            v_i = _interp_slice(v_da, time_dim, li, lats, lons, st_lats, st_lons)
            ws = np.sqrt(u_i ** 2 + v_i ** 2)
            wd = (np.degrees(np.arctan2(-u_i, -v_i)) + 360) % 360
            for si in range(len(stations)):
                st = stations.iloc[si]
                wsv = _scalar_or_none(ws[si])
                if wsv is not None:
                    records.append({
                        "model": model, "station_id": st["station_id"],
                        "init_time": init_time.isoformat(), "lead_time": lt,
                        "valid_time": normalize_valid_time(valid_time),
                        "parameter": "wind_speed_ff", "fcst": wsv,
                    })
                wdv = _scalar_or_none(wd[si])
                if wdv is not None:
                    records.append({
                        "model": model, "station_id": st["station_id"],
                        "init_time": init_time.isoformat(), "lead_time": lt,
                        "valid_time": normalize_valid_time(valid_time),
                        "parameter": "wind_dir_deg_dd", "fcst": wdv,
                    })
        return records

    ws_var = _find_var(ds, MODEL_VAR_MAP.get(model, {}).get("wind_speed_ff", ["WS10", "ws10"]))
    wd_var = _find_var(ds, MODEL_VAR_MAP.get(model, {}).get("wind_dir_deg_dd", ["WD10", "wd10"]))
    if not ws_var or not wd_var:
        return records

    ws_da, wd_da = ds[ws_var], ds[wd_var]
    for li in lead_indices:
        lt = lead_times[li]
        valid_time = init_time + timedelta(hours=lt)
        ws_i = _interp_slice(ws_da, time_dim, li, lats, lons, st_lats, st_lons)
        wd_i = _interp_slice(wd_da, time_dim, li, lats, lons, st_lats, st_lons)
        for si in range(len(stations)):
            st = stations.iloc[si]
            wsv = _scalar_or_none(ws_i[si])
            if wsv is not None:
                records.append({
                    "model": model, "station_id": st["station_id"],
                    "init_time": init_time.isoformat(), "lead_time": lt,
                    "valid_time": normalize_valid_time(valid_time),
                    "parameter": "wind_speed_ff", "fcst": wsv,
                })
            wdv = _scalar_or_none(wd_i[si])
            if wdv is not None:
                records.append({
                    "model": model, "station_id": st["station_id"],
                    "init_time": init_time.isoformat(), "lead_time": lt,
                    "valid_time": normalize_valid_time(valid_time),
                    "parameter": "wind_dir_deg_dd", "fcst": wdv,
                })
    return records


def read_point_forecast(
    nc_path: Path,
    model: str,
    stations: pd.DataFrame,
    parameters: list[str],
    init_time: datetime | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> pd.DataFrame:
    """Extract point forecasts from NetCDF for given stations (lazy read for 12GB+)."""
    def report(p: float, msg: str) -> None:
        if progress_cb:
            progress_cb(p, msg)
        else:
            print(f"[nc] [{p:5.1f}%] {msg}", flush=True)

    init_time = init_time or parse_init_time(nc_path.name)
    if init_time is None:
        init_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    use_chunks = nc_path.stat().st_size > NC_CHUNK_THRESHOLD_BYTES
    chunk_kw: dict[str, Any] = {}
    if use_chunks:
        try:
            import dask  # noqa: F401
            chunk_kw["chunks"] = {"Time": 1}
        except ImportError:
            # Tanpa dask: buka tanpa chunk (12GB — butuh RAM cukup atau install dask)
            chunk_kw = {}

    report(42, f"Membuka NetCDF {nc_path.name} ({nc_path.stat().st_size / 1e6:.0f} MB)...")
    try:
        ds = xr.open_dataset(nc_path, engine="netcdf4", **chunk_kw)
    except ImportError:
        # dask terpasang tapi chunk manager tidak — buka tanpa chunk
        ds = xr.open_dataset(nc_path, engine="netcdf4")
    lats, lons = _get_coords(ds)
    time_dim = _get_time_dim(ds)
    lead_times = _get_lead_times(ds, init_time, time_dim)
    lead_indices = _filter_lead_indices(lead_times)
    var_map = MODEL_VAR_MAP.get(model, MODEL_VAR_MAP["InaNWP"])

    records: list[dict[str, Any]] = []
    st_lats = stations["lat"].values
    st_lons = stations["lon"].values

    scalar_params = [p for p in parameters if p not in ("wind_speed_ff", "wind_dir_deg_dd")]
    n_params = max(len(scalar_params), 1)
    report(45, f"Interpolasi {len(stations)} stasiun × {n_params} param × {len(lead_indices)} lead...")

    for pi, param in enumerate(scalar_params):
        report(45 + (pi / n_params) * 30, f"Param {param} ({pi + 1}/{n_params})")
        candidates = var_map.get(param, [])
        rain_total = rainc_name = rainnc_name = None
        if param.startswith("rainfall"):
            rain_total = _find_var(ds, ["rain", "tp", "precip", "apcp"])
            rainc_name = _find_var(ds, ["RAINC", "rainc"])
            rainnc_name = _find_var(ds, ["RAINNC", "rainnc"])

        # InaNWP asim: prefer total rain; else WRF rainc+rainnc
        if rain_total and param.startswith("rainfall"):
            da = ds[rain_total]
            for li in lead_indices:
                lt = lead_times[li]
                valid_time = init_time + timedelta(hours=lt)
                interp = _interp_slice(da, time_dim, li, lats, lons, st_lats, st_lons)
                for si in range(len(stations)):
                    st = stations.iloc[si]
                    val = _scalar_or_none(interp[si])
                    if val is not None:
                        records.append({
                            "model": model,
                            "station_id": st["station_id"],
                            "init_time": init_time.isoformat(),
                            "lead_time": lt,
                            "valid_time": normalize_valid_time(valid_time),
                            "parameter": param,
                            "fcst": val,
                        })
            continue

        if rainc_name and rainnc_name:
            da_c, da_nc = ds[rainc_name], ds[rainnc_name]
            for li in lead_indices:
                lt = lead_times[li]
                valid_time = init_time + timedelta(hours=lt)
                interp = (
                    _interp_slice(da_c, time_dim, li, lats, lons, st_lats, st_lons)
                    + _interp_slice(da_nc, time_dim, li, lats, lons, st_lats, st_lons)
                )
                for si in range(len(stations)):
                    st = stations.iloc[si]
                    val = _scalar_or_none(interp[si])
                    if val is not None:
                        records.append({
                            "model": model,
                            "station_id": st["station_id"],
                            "init_time": init_time.isoformat(),
                            "lead_time": lt,
                            "valid_time": normalize_valid_time(valid_time),
                            "parameter": param,
                            "fcst": val,
                        })
            continue

        # Awan InaNWP: low+mid+high (%) → okta (~ /12.5)
        if param == "cloud_cover_oktas_m":
            c_lo = _find_var(ds, ["clflo"])
            c_mi = _find_var(ds, ["clfmi"])
            c_hi = _find_var(ds, ["clfhi"])
            if c_lo or c_mi or c_hi:
                for li in lead_indices:
                    lt = lead_times[li]
                    valid_time = init_time + timedelta(hours=lt)
                    acc = None
                    for vn in (c_lo, c_mi, c_hi):
                        if not vn:
                            continue
                        part = _interp_slice(ds[vn], time_dim, li, lats, lons, st_lats, st_lons)
                        acc = part if acc is None else np.maximum(acc, part)
                    if acc is None:
                        continue
                    # % cloud → okta (0–8)
                    acc = np.clip(np.asarray(acc, dtype=float) / 12.5, 0.0, 8.0)
                    for si in range(len(stations)):
                        st = stations.iloc[si]
                        val = _scalar_or_none(acc[si])
                        if val is not None:
                            records.append({
                                "model": model,
                                "station_id": st["station_id"],
                                "init_time": init_time.isoformat(),
                                "lead_time": lt,
                                "valid_time": normalize_valid_time(valid_time),
                                "parameter": param,
                                "fcst": val,
                            })
                continue

        var_name = _find_var(ds, candidates)
        if not var_name:
            continue

        da = ds[var_name]
        for li in lead_indices:
            lt = lead_times[li]
            valid_time = init_time + timedelta(hours=lt)
            interp = _interp_slice(da, time_dim, li, lats, lons, st_lats, st_lons)
            interp = _kelvin_to_celsius(interp, var_name)
            for si in range(len(stations)):
                st = stations.iloc[si]
                val = _scalar_or_none(interp[si])
                if val is not None:
                    records.append({
                        "model": model,
                        "station_id": st["station_id"],
                        "init_time": init_time.isoformat(),
                        "lead_time": lt,
                        "valid_time": normalize_valid_time(valid_time),
                        "parameter": param,
                        "fcst": val,
                    })

    report(76, "Ekstrak angin (U/V atau ws10/wd10)...")
    records.extend(_extract_wind_at_times(
        ds, model, stations, lats, lons, time_dim, lead_indices, lead_times, init_time,
    ))

    ds.close()
    report(78, f"Interpolasi selesai — {len(records)} records")
    return pd.DataFrame(records)


def inspect_nc(nc_path: Path) -> dict[str, Any]:
    ds = xr.open_dataset(nc_path, decode_times=False)
    init = parse_init_time(nc_path.name)
    time_dim = _get_time_dim(ds)
    lead_times = _get_lead_times(ds, init or datetime.utcnow(), time_dim) if init else []
    info = {
        "variables": list(ds.data_vars),
        "dims": dict(ds.sizes),
        "coords": list(ds.coords),
        "time_dim": time_dim,
        "init_time": init.isoformat() if init else None,
        "lead_times_hours": lead_times[:20],
        "lead_time_count": len(lead_times),
        "lead_time_max_hours": max(lead_times) if lead_times else 0,
        "size_gb": round(nc_path.stat().st_size / 1e9, 2),
        "chunked_read": nc_path.stat().st_size > NC_CHUNK_THRESHOLD_BYTES,
    }
    ds.close()
    return info
