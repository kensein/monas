"""NetCDF model forecast reader with grid-to-point interpolation."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr
from scipy.interpolate import RegularGridInterpolator

from backend.config import MODEL_VAR_MAP


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


def _get_times(ds: xr.Dataset) -> list[datetime]:
    for tname in ["Time", "time", "Times"]:
        if tname in ds.coords or tname in ds.dims:
            times = ds[tname].values
            result = []
            for t in times:
                if isinstance(t, np.datetime64):
                    result.append(pd.Timestamp(t).to_pydatetime())
                else:
                    try:
                        result.append(datetime.utcfromtimestamp(float(t)))
                    except Exception:
                        result.append(parse_init_time(str(t)) or datetime.utcnow())
            return result
    return []


def _get_lead_times(ds: xr.Dataset, init_time: datetime) -> list[int]:
    times = _get_times(ds)
    if times:
        return [int((t - init_time).total_seconds() / 3600) for t in times]
    for dim in ds.dims:
        if "time" in dim.lower() or dim == "Time":
            return list(range(ds.dims[dim]))
    return [0]


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
        results = []
        for t in range(field.shape[0]):
            results.append(_interp_field(field[t], lats, lons, station_lats, station_lons))
        return np.array(results)
    raise ValueError(f"Unsupported field ndim: {field.ndim}")


def _extract_wind(ds: xr.Dataset, model: str, stations: pd.DataFrame) -> tuple[np.ndarray | None, np.ndarray | None]:
    u_names = ["U10", "u10", "10u"]
    v_names = ["V10", "v10", "10v"]
    u_var = _find_var(ds, u_names)
    v_var = _find_var(ds, v_names)
    if not u_var or not v_var:
        ws_var = _find_var(ds, MODEL_VAR_MAP.get(model, {}).get("wind_speed_ff", ["WS10", "ws10"]))
        wd_var = _find_var(ds, MODEL_VAR_MAP.get(model, {}).get("wind_dir_deg_dd", ["WD10", "wd10"]))
        if ws_var and wd_var:
            lats, lons = _get_coords(ds)
            ws = _interp_field(ds[ws_var].values, lats, lons, stations["lat"].values, stations["lon"].values)
            wd = _interp_field(ds[wd_var].values, lats, lons, stations["lat"].values, stations["lon"].values)
            return ws, wd
        return None, None

    lats, lons = _get_coords(ds)
    u = ds[u_var].values
    v = ds[v_var].values
    u_i = _interp_field(u, lats, lons, stations["lat"].values, stations["lon"].values)
    v_i = _interp_field(v, lats, lons, stations["lat"].values, stations["lon"].values)
    ws = np.sqrt(u_i ** 2 + v_i ** 2)
    wd = (np.degrees(np.arctan2(-u_i, -v_i)) + 360) % 360
    return ws, wd


def _kelvin_to_celsius(val: np.ndarray, var_name: str) -> np.ndarray:
    if var_name.upper().startswith("T") and np.nanmean(val) > 150:
        return val - 273.15
    return val


def read_point_forecast(
    nc_path: Path,
    model: str,
    stations: pd.DataFrame,
    parameters: list[str],
    init_time: datetime | None = None,
) -> pd.DataFrame:
    """Extract point forecasts from NetCDF for given stations."""
    init_time = init_time or parse_init_time(nc_path.name)
    if init_time is None:
        init_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    use_chunks = nc_path.stat().st_size > 500_000_000
    ds = xr.open_dataset(nc_path, chunks={"Time": 1} if use_chunks else None)
    lats, lons = _get_coords(ds)
    lead_times = _get_lead_times(ds, init_time)
    var_map = MODEL_VAR_MAP.get(model, MODEL_VAR_MAP["InaNWP"])

    records: list[dict[str, Any]] = []

    for param in parameters:
        if param in ("wind_speed_ff", "wind_dir_deg_dd"):
            continue

        candidates = var_map.get(param, [])
        var_name = _find_var(ds, candidates)
        if not var_name:
            continue

        field = ds[var_name].values
        interp = _interp_field(field, lats, lons, stations["lat"].values, stations["lon"].values)
        interp = _kelvin_to_celsius(interp, var_name)

        if interp.ndim == 1:
            interp = interp.reshape(1, -1)

        for li, lt in enumerate(lead_times[: interp.shape[0]]):
            valid_time = init_time + timedelta(hours=lt)
            for si, st in stations.iterrows():
                val = float(interp[li, si]) if not np.isnan(interp[li, si]) else None
                if val is not None:
                    records.append({
                        "model": model,
                        "station_id": st["station_id"],
                        "init_time": init_time.isoformat(),
                        "lead_time": lt,
                        "valid_time": valid_time.isoformat(),
                        "parameter": param,
                        "fcst": val,
                    })

    # Wind from U/V
    ws, wd = _extract_wind(ds, model, stations)
    if ws is not None:
        for li, lt in enumerate(lead_times[: ws.shape[0] if ws.ndim > 1 else 1]):
            valid_time = init_time + timedelta(hours=lt)
            for si, st in stations.iterrows():
                wsi = float(ws[li, si] if ws.ndim > 1 else ws[si])
                wdi = float(wd[li, si] if wd.ndim > 1 else wd[si])
                if not np.isnan(wsi):
                    records.append({
                        "model": model, "station_id": st["station_id"],
                        "init_time": init_time.isoformat(), "lead_time": lt,
                        "valid_time": valid_time.isoformat(),
                        "parameter": "wind_speed_ff", "fcst": wsi,
                    })
                if not np.isnan(wdi):
                    records.append({
                        "model": model, "station_id": st["station_id"],
                        "init_time": init_time.isoformat(), "lead_time": lt,
                        "valid_time": valid_time.isoformat(),
                        "parameter": "wind_dir_deg_dd", "fcst": wdi,
                    })

    ds.close()
    return pd.DataFrame(records)


def inspect_nc(nc_path: Path) -> dict[str, Any]:
    ds = xr.open_dataset(nc_path, decode_times=False)
    info = {
        "variables": list(ds.data_vars),
        "dims": dict(ds.dims),
        "coords": list(ds.coords),
        "init_time": parse_init_time(nc_path.name).isoformat() if parse_init_time(nc_path.name) else None,
    }
    ds.close()
    return info
