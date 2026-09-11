"""End-to-end InSAR for selected/filtered CNE sites via ASF HyP3 + local sampling."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


@dataclass
class InSARPair:
    reference: str
    secondary: str
    ref_date: str
    sec_date: str
    path: int | None = None
    flight_direction: str | None = None


def bbox_to_wkt(bbox: tuple[float, float, float, float]) -> str:
    minx, miny, maxx, maxy = bbox
    return f"POLYGON(({minx} {miny},{maxx} {miny},{maxx} {maxy},{minx} {maxy},{minx} {miny}))"


def site_insar_dir(root: Path, site_id: str) -> Path:
    safe = re.sub(r"[^\w\-]+", "_", str(site_id))
    path = Path(root) / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_earthdata_creds() -> tuple[str | None, str | None]:
    """Prefer session form → Streamlit secrets → env vars."""
    user = pwd = None
    try:
        import streamlit as st

        user = st.session_state.get("earthdata_username") or None
        pwd = st.session_state.get("earthdata_password") or None
        if not user:
            user = st.secrets.get("EARTHDATA_USERNAME") or st.secrets.get("earthdata_username")
        if not pwd:
            pwd = st.secrets.get("EARTHDATA_PASSWORD") or st.secrets.get("earthdata_password")
    except Exception:
        pass
    import os

    user = user or os.environ.get("EARTHDATA_USERNAME")
    pwd = pwd or os.environ.get("EARTHDATA_PASSWORD")
    return (str(user) if user else None), (str(pwd) if pwd else None)


def search_slc_stack(
    bbox: tuple[float, float, float, float],
    *,
    start: str | None = None,
    end: str | None = None,
    max_results: int = 40,
    flight_direction: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """Search Sentinel-1 SLC scenes for an AOI (needed for HyP3 InSAR)."""
    try:
        import asf_search as asf
    except ImportError:
        return pd.DataFrame(), "Instale asf_search para buscar SLC."

    opts: dict[str, Any] = {
        "platform": asf.PLATFORM.SENTINEL1,
        "processingLevel": asf.PRODUCT_TYPE.SLC,
        "beamMode": asf.BEAMMODE.IW,
        "intersectsWith": bbox_to_wkt(bbox),
        "maxResults": int(max_results),
    }
    if start:
        opts["start"] = start
    if end:
        opts["end"] = end
    if flight_direction:
        fd = flight_direction.upper()
        opts["flightDirection"] = (
            asf.FLIGHT_DIRECTION.ASCENDING
            if fd.startswith("A")
            else asf.FLIGHT_DIRECTION.DESCENDING
        )

    try:
        results = asf.geo_search(**opts)
        rows = []
        for r in results:
            p = r.properties
            rows.append(
                {
                    "scene": p.get("sceneName"),
                    "startTime": p.get("startTime"),
                    "stopTime": p.get("stopTime"),
                    "flightDirection": p.get("flightDirection"),
                    "pathNumber": p.get("pathNumber"),
                    "frameNumber": p.get("frameNumber"),
                    "orbit": p.get("orbit"),
                    "polarization": p.get("polarization"),
                }
            )
        df = pd.DataFrame(rows)
        if df.empty:
            return df, "Sin SLC Sentinel-1 para el AOI/periodo"
        df["startTime"] = pd.to_datetime(df["startTime"], utc=True, errors="coerce")
        df = df.dropna(subset=["scene", "startTime"]).sort_values("startTime")
        return df.reset_index(drop=True), f"{len(df)} SLC encontradas"
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), f"Búsqueda SLC falló: {exc}"


def select_main_track(stack: pd.DataFrame) -> pd.DataFrame:
    """Keep the dominant path + flightDirection combination for SBAS-like pairs."""
    if stack.empty:
        return stack
    key = stack.assign(
        _k=stack["pathNumber"].astype(str) + "|" + stack["flightDirection"].astype(str)
    )
    top = key["_k"].value_counts().index[0]
    return key.loc[key["_k"] == top].drop(columns=["_k"]).reset_index(drop=True)


def build_consecutive_pairs(
    stack: pd.DataFrame,
    *,
    max_pairs: int = 6,
    max_temp_baseline_days: int = 48,
) -> list[InSARPair]:
    """Build chronological consecutive pairs within temporal baseline."""
    if stack is None or len(stack) < 2:
        return []
    df = select_main_track(stack).sort_values("startTime").reset_index(drop=True)
    pairs: list[InSARPair] = []
    for i in range(len(df) - 1):
        if len(pairs) >= max_pairs:
            break
        a, b = df.iloc[i], df.iloc[i + 1]
        dt = (b["startTime"] - a["startTime"]).days
        if dt <= 0 or dt > max_temp_baseline_days:
            continue
        pairs.append(
            InSARPair(
                reference=str(a["scene"]),
                secondary=str(b["scene"]),
                ref_date=a["startTime"].strftime("%Y-%m-%d"),
                sec_date=b["startTime"].strftime("%Y-%m-%d"),
                path=int(a["pathNumber"]) if pd.notna(a.get("pathNumber")) else None,
                flight_direction=str(a.get("flightDirection") or ""),
            )
        )
    return pairs


def pairs_to_frame(pairs: Iterable[InSARPair]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "reference": p.reference,
                "secondary": p.secondary,
                "ref_date": p.ref_date,
                "sec_date": p.sec_date,
                "path": p.path,
                "flight_direction": p.flight_direction,
            }
            for p in pairs
        ]
    )


def hyp3_client(username: str | None = None, password: str | None = None):
    try:
        import hyp3_sdk as sdk
    except ImportError as exc:
        raise ImportError("Instale hyp3-sdk (pip install hyp3-sdk)") from exc
    user, pwd = username, password
    if not user or not pwd:
        user, pwd = get_earthdata_creds()
    if not user or not pwd:
        raise ValueError(
            "Faltan credenciales Earthdata. Configure EARTHDATA_USERNAME / "
            "EARTHDATA_PASSWORD en secrets o variables de entorno."
        )
    return sdk.HyP3(username=user, password=pwd)


def submit_insar_pairs(
    pairs: list[InSARPair],
    *,
    job_name: str,
    username: str | None = None,
    password: str | None = None,
    include_los_displacement: bool = True,
) -> Any:
    """Submit HyP3 InSAR jobs for each pair. Returns hyp3 Batch."""
    import hyp3_sdk as sdk

    hyp3 = hyp3_client(username, password)
    batch = sdk.Batch()
    for p in pairs:
        batch += hyp3.submit_insar_job(
            p.reference,
            p.secondary,
            name=job_name[:20],  # HyP3 name length limit
            include_look_vectors=True,
            include_los_displacement=include_los_displacement,
            include_dem=False,
            looks="20x4",
        )
    return batch, hyp3


def refresh_jobs(hyp3, batch):
    return hyp3.refresh(batch)


def download_succeeded_jobs(batch, out_dir: Path) -> list[Path]:
    """Download and unzip succeeded HyP3 products."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    try:
        files = batch.download_files(str(out_dir))
    except Exception:
        # Older SDK / partial batch: download per job
        files = []
        for job in batch:
            if getattr(job, "status_code", "") != "SUCCEEDED":
                continue
            try:
                files.extend(job.download_files(str(out_dir)) or [])
            except Exception:
                continue

    for f in files or []:
        fp = Path(f)
        downloaded.append(fp)
        if fp.suffix.lower() == ".zip":
            dest = out_dir / fp.stem
            dest.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(fp, "r") as zf:
                    zf.extractall(dest)
                downloaded.append(dest)
            except Exception:
                pass
    return downloaded


def find_displacement_tifs(root: Path) -> list[Path]:
    patterns = ("*los_disp*.tif", "*los_displacement*.tif", "*disp*.tif", "*unw_phase*.tif")
    found: list[Path] = []
    root = Path(root)
    for pat in patterns:
        found.extend(root.rglob(pat))
    # Prefer explicit LOS displacement
    found = sorted(set(found), key=lambda p: (0 if "los" in p.name.lower() else 1, p.name))
    return found


def sample_raster_mean(raster_path: Path, geom) -> float | None:
    """Mean value of raster over geometry (EPSG:4326 geom; reprojects as needed)."""
    try:
        import geopandas as gpd
        import rasterio
        from rasterio.mask import mask
    except ImportError:
        return None

    try:
        with rasterio.open(raster_path) as src:
            gtmp = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
            if src.crs is not None:
                gtmp = gtmp.to_crs(src.crs)
            out, _ = mask(src, [gtmp.geometry.iloc[0].__geo_interface__], crop=True, filled=False)
            data = out[0]
            arr = np.array(data, dtype=float)
            if hasattr(data, "filled"):
                arr = data.filled(np.nan).astype(float)
            nodata = src.nodata
            if nodata is not None:
                arr[arr == nodata] = np.nan
            valid = arr[np.isfinite(arr)]
            if valid.size == 0:
                return None
            return float(np.nanmean(valid))
    except Exception:
        return None


def parse_dates_from_hyp3_name(path: Path) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """
    HyP3 folders often contain dates like S1AA_YYYYMMDD_YYYYMMDD_...
    """
    name = path.name
    dates = re.findall(r"(20\d{6})", name)
    if len(dates) >= 2:
        try:
            return pd.to_datetime(dates[0]), pd.to_datetime(dates[1])
        except Exception:
            return None, None
    # parent folder
    dates = re.findall(r"(20\d{6})", path.parent.name)
    if len(dates) >= 2:
        try:
            return pd.to_datetime(dates[0]), pd.to_datetime(dates[1])
        except Exception:
            return None, None
    return None, None


def build_series_from_products(
    product_dir: Path,
    aoi_geom,
    *,
    pair_meta: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Build cumulative LOS series (mm) from successive interferogram displacement maps.
    Assumes displacement rasters are in meters (HyP3 los_disp) unless values look like radians.
    """
    tifs = find_displacement_tifs(product_dir)
    rows = []
    for tif in tifs:
        mean_val = sample_raster_mean(tif, aoi_geom)
        if mean_val is None:
            continue
        d0, d1 = parse_dates_from_hyp3_name(tif)
        if d0 is None and pair_meta is not None and not pair_meta.empty:
            # try match by substring
            for _, pr in pair_meta.iterrows():
                if str(pr["ref_date"]).replace("-", "") in tif.name or str(pr["sec_date"]).replace("-", "") in tif.name:
                    d0 = pd.to_datetime(pr["ref_date"])
                    d1 = pd.to_datetime(pr["sec_date"])
                    break
        if d0 is None or d1 is None:
            continue
        # HyP3 LOS displacement typically meters → mm
        disp_mm = mean_val * 1000.0 if abs(mean_val) < 50 else mean_val
        rows.append({"ref_date": d0, "sec_date": d1, "delta_los_mm": disp_mm, "raster": str(tif)})

    if not rows:
        return pd.DataFrame(columns=["date", "los_mm"])

    deltas = pd.DataFrame(rows).sort_values("sec_date")
    # Cumulative chain from first reference
    t0 = deltas["ref_date"].iloc[0]
    series_rows = [{"date": t0, "los_mm": 0.0}]
    cum = 0.0
    for _, r in deltas.iterrows():
        cum += float(r["delta_los_mm"])
        series_rows.append({"date": r["sec_date"], "los_mm": cum})
    out = pd.DataFrame(series_rows).drop_duplicates(subset=["date"]).sort_values("date")
    return out.reset_index(drop=True)


def save_series_csv(series: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    series.to_csv(path, index=False)
    return path


def load_series_csv(path: Path) -> pd.DataFrame | None:
    path = Path(path)
    if not path.exists():
        return None
    df = pd.read_csv(path)
    if "date" not in df.columns or "los_mm" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def run_insar_for_site(
    *,
    site_id: str,
    aoi_geom,
    bbox: tuple[float, float, float, float],
    out_root: Path,
    start: str | None = None,
    end: str | None = None,
    max_pairs: int = 4,
    max_temp_baseline_days: int = 48,
    submit: bool = True,
    wait: bool = False,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    """
    End-to-end InSAR for one site AOI:
    search SLC → pairs → (optional) HyP3 submit → sample products → LOS CSV.
    """
    site_dir = site_insar_dir(out_root, site_id)
    meta_path = site_dir / "pipeline_meta.json"
    series_path = site_dir / "los_series.csv"

    stack, stack_msg = search_slc_stack(bbox, start=start, end=end, max_results=50)
    pairs = build_consecutive_pairs(
        stack, max_pairs=max_pairs, max_temp_baseline_days=max_temp_baseline_days
    )
    pairs_df = pairs_to_frame(pairs)
    pairs_df.to_csv(site_dir / "pairs.csv", index=False)

    result: dict[str, Any] = {
        "site_id": site_id,
        "stack_msg": stack_msg,
        "n_slc": int(len(stack)),
        "n_pairs": len(pairs),
        "pairs": pairs_df,
        "site_dir": str(site_dir),
        "series": None,
        "jobs_status": None,
        "message": stack_msg,
    }

    if not pairs:
        result["message"] = f"{stack_msg}. No hay pares válidos (revise periodo / cobertura SLC)."
        meta_path.write_text(json.dumps({k: result[k] for k in ("site_id", "n_slc", "n_pairs", "message")}, indent=2), encoding="utf-8")
        return result

    batch = None
    if submit:
        try:
            batch, hyp3 = submit_insar_pairs(
                pairs,
                job_name=f"CR-{site_id}"[:20],
                username=username,
                password=password,
            )
            result["jobs_status"] = str(batch)
            result["message"] = f"Enviados {len(pairs)} jobs HyP3 InSAR para {site_id}"
            if wait:
                batch = hyp3.watch(batch)
                download_succeeded_jobs(batch, site_dir / "products")
                result["message"] += " · jobs completados y descargados"
            else:
                # Persist job ids for later refresh
                job_ids = []
                for job in batch:
                    job_ids.append(
                        {
                            "job_id": getattr(job, "job_id", None),
                            "status": getattr(job, "status_code", None),
                        }
                    )
                (site_dir / "hyp3_jobs.json").write_text(
                    json.dumps(job_ids, indent=2), encoding="utf-8"
                )
        except Exception as exc:  # noqa: BLE001
            result["message"] = f"HyP3 no enviado: {exc}. Se intentará usar productos locales si existen."

    # Always try to build series from any local products
    series = build_series_from_products(site_dir, aoi_geom, pair_meta=pairs_df)
    if not series.empty:
        save_series_csv(series, series_path)
        result["series"] = series
        result["message"] = (
            result.get("message") or ""
        ) + f" · serie LOS con {len(series)} épocas"
    else:
        existing = load_series_csv(series_path)
        if existing is not None and not existing.empty:
            result["series"] = existing
            result["message"] = (result.get("message") or "") + " · serie LOS previa cargada"

    meta = {
        "site_id": site_id,
        "updated": datetime.now(timezone.utc).isoformat(),
        "n_slc": result["n_slc"],
        "n_pairs": result["n_pairs"],
        "message": result["message"],
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return result


def refresh_and_ingest_site(
    *,
    site_id: str,
    aoi_geom,
    out_root: Path,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    """Refresh HyP3 jobs for a site, download SUCCEEDED products, rebuild LOS series."""
    import hyp3_sdk as sdk

    site_dir = site_insar_dir(out_root, site_id)
    jobs_file = site_dir / "hyp3_jobs.json"
    pairs_path = site_dir / "pairs.csv"
    pair_meta = pd.read_csv(pairs_path) if pairs_path.exists() else None

    if not jobs_file.exists():
        series = build_series_from_products(site_dir, aoi_geom, pair_meta=pair_meta)
        if not series.empty:
            save_series_csv(series, site_dir / "los_series.csv")
        return {
            "site_id": site_id,
            "message": "Sin hyp3_jobs.json; solo se re-muestrearon productos locales.",
            "series": series if not series.empty else load_series_csv(site_dir / "los_series.csv"),
        }

    hyp3 = hyp3_client(username, password)
    raw = json.loads(jobs_file.read_text(encoding="utf-8"))
    batch = sdk.Batch()
    for item in raw:
        jid = item.get("job_id")
        if not jid:
            continue
        try:
            batch += hyp3.get_job_by_id(jid)
        except Exception:
            continue
    if len(batch) == 0:
        # Fallback: find by job name prefix stored in meta
        name = f"CR-{site_id}"[:20]
        try:
            batch = hyp3.find_jobs(name=name)
        except Exception:
            batch = sdk.Batch()
    batch = hyp3.refresh(batch) if len(batch) else batch
    n_ok = sum(1 for j in batch if getattr(j, "status_code", "") == "SUCCEEDED")
    if len(batch):
        download_succeeded_jobs(batch, site_dir / "products")
    series = build_series_from_products(site_dir, aoi_geom, pair_meta=pair_meta)
    if not series.empty:
        save_series_csv(series, site_dir / "los_series.csv")
    statuses = [
        {"job_id": getattr(j, "job_id", None), "status": getattr(j, "status_code", None)}
        for j in batch
    ]
    jobs_file.write_text(json.dumps(statuses, indent=2), encoding="utf-8")
    return {
        "site_id": site_id,
        "message": f"Jobs actualizados: {n_ok}/{len(batch)} SUCCEEDED" if len(batch) else "Sin jobs HyP3 para refrescar",
        "series": series if not series.empty else load_series_csv(site_dir / "los_series.csv"),
        "statuses": statuses,
    }
