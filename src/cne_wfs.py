"""CNE WFS client for landslide layers with disk cache and offline fallback."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import shape


DEFAULT_WFS_URL = "http://mapas.cne.go.cr/servicios/cne/wfs"
LAYER_DESLIZAMIENTOS = "cne:deslizamientos"
LAYER_CORONAS = "cne:coronas_de_deslizamientos"


def _cache_path(cache_dir: Path, layer: str) -> Path:
    safe = layer.replace(":", "_").replace("/", "_")
    return cache_dir / f"{safe}.geojson"


def _meta_path(cache_dir: Path, layer: str) -> Path:
    return _cache_path(cache_dir, layer).with_suffix(".meta.json")


def cache_is_fresh(cache_dir: Path, layer: str, ttl_hours: float) -> bool:
    meta = _meta_path(cache_dir, layer)
    data = _cache_path(cache_dir, layer)
    if not meta.exists() or not data.exists():
        return False
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
        age_h = (time.time() - float(payload.get("fetched_at", 0))) / 3600.0
        return age_h <= ttl_hours
    except (json.JSONDecodeError, TypeError, ValueError):
        return False


def fetch_wfs_geojson(
    layer: str,
    *,
    wfs_url: str = DEFAULT_WFS_URL,
    timeout: int = 120,
    count: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    """GetFeature as GeoJSON FeatureCollection."""
    params: dict[str, Any] = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": layer,
        "outputFormat": "application/json",
    }
    if count is not None:
        params["count"] = int(count)
    if bbox is not None:
        # CRS84 lon/lat: minx,miny,maxx,maxy
        params["bbox"] = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]},EPSG:4326"

    url = f"{wfs_url}?{urlencode(params)}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def geojson_to_gdf(geojson: dict[str, Any]) -> gpd.GeoDataFrame:
    features = geojson.get("features") or []
    if not features:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    rows = []
    geoms = []
    for feat in features:
        props = dict(feat.get("properties") or {})
        geom = feat.get("geometry")
        if geom is None:
            continue
        geoms.append(shape(geom))
        rows.append(props)

    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    if not gdf.empty:
        try:
            from shapely import make_valid

            gdf = gdf[gdf.geometry.notna()].copy()
            gdf["geometry"] = gdf.geometry.make_valid()
            gdf = gdf[~gdf.geometry.is_empty].copy()
        except Exception:
            pass
    return gdf


def save_cache(cache_dir: Path, layer: str, geojson: dict[str, Any]) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, layer)
    path.write_text(json.dumps(geojson), encoding="utf-8")
    _meta_path(cache_dir, layer).write_text(
        json.dumps({"fetched_at": time.time(), "layer": layer}, indent=2),
        encoding="utf-8",
    )
    return path


def load_cache(cache_dir: Path, layer: str) -> gpd.GeoDataFrame:
    path = _cache_path(cache_dir, layer)
    if not path.exists():
        raise FileNotFoundError(path)
    return gpd.read_file(path)


def load_local_geojson(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")
    # Normalize columns so UI can treat local fallback similarly
    if "name" not in gdf.columns:
        if "OBJECTID" in gdf.columns:
            gdf["name"] = gdf["OBJECTID"].astype(str).map(lambda x: f"Sitio local {x}")
        else:
            gdf["name"] = [f"Sitio local {i+1}" for i in range(len(gdf))]
    if "area" not in gdf.columns:
        gdf["area"] = pd.NA
    return gdf


def load_deslizamientos(
    *,
    source: str = "wfs",
    wfs_url: str = DEFAULT_WFS_URL,
    layer: str = LAYER_DESLIZAMIENTOS,
    cache_dir: Path,
    local_geojson: Path | None = None,
    ttl_hours: float = 24.0,
    timeout: int = 120,
    force_refresh: bool = False,
) -> tuple[gpd.GeoDataFrame, str]:
    """
    Load landslide polygons.

    source: 'wfs' | 'cache' | 'local'
    Returns (gdf, status_message).
    """
    cache_dir = Path(cache_dir)

    if source == "local":
        if local_geojson is None or not Path(local_geojson).exists():
            raise FileNotFoundError("GeoJSON local no disponible")
        return load_local_geojson(Path(local_geojson)), "Fuente: GeoJSON local"

    if source == "cache" or (source == "wfs" and not force_refresh and cache_is_fresh(cache_dir, layer, ttl_hours)):
        try:
            gdf = load_cache(cache_dir, layer)
            return gdf, f"Fuente: caché WFS ({layer})"
        except FileNotFoundError:
            if source == "cache":
                if local_geojson and Path(local_geojson).exists():
                    return load_local_geojson(Path(local_geojson)), "Caché vacía → GeoJSON local"
                raise

    try:
        geojson = fetch_wfs_geojson(layer, wfs_url=wfs_url, timeout=timeout)
        save_cache(cache_dir, layer, geojson)
        gdf = geojson_to_gdf(geojson)
        return gdf, f"Fuente: WFS en vivo ({layer})"
    except Exception as exc:  # noqa: BLE001 — intentional soft fallback for UI
        if cache_dir and _cache_path(cache_dir, layer).exists():
            gdf = load_cache(cache_dir, layer)
            return gdf, f"WFS falló ({exc}); usando caché"
        if local_geojson and Path(local_geojson).exists():
            return load_local_geojson(Path(local_geojson)), f"WFS falló ({exc}); usando GeoJSON local"
        raise


def load_coronas(
    *,
    wfs_url: str = DEFAULT_WFS_URL,
    layer: str = LAYER_CORONAS,
    cache_dir: Path,
    ttl_hours: float = 24.0,
    timeout: int = 120,
    force_refresh: bool = False,
    max_features: int | None = 2000,
) -> tuple[gpd.GeoDataFrame, str]:
    """Load crown scar lines (optionally capped for map performance)."""
    cache_dir = Path(cache_dir)
    if not force_refresh and cache_is_fresh(cache_dir, layer, ttl_hours):
        try:
            gdf = load_cache(cache_dir, layer)
            if max_features is not None and len(gdf) > max_features:
                gdf = gdf.iloc[:max_features].copy()
            return gdf, f"Fuente: caché WFS ({layer})"
        except FileNotFoundError:
            pass

    try:
        geojson = fetch_wfs_geojson(
            layer, wfs_url=wfs_url, timeout=timeout, count=max_features
        )
        save_cache(cache_dir, layer, geojson)
        gdf = geojson_to_gdf(geojson)
        return gdf, f"Fuente: WFS en vivo ({layer})"
    except Exception as exc:  # noqa: BLE001
        if _cache_path(cache_dir, layer).exists():
            gdf = load_cache(cache_dir, layer)
            if max_features is not None and len(gdf) > max_features:
                gdf = gdf.iloc[:max_features].copy()
            return gdf, f"WFS falló ({exc}); usando caché"
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), f"Coronas no disponibles: {exc}"
