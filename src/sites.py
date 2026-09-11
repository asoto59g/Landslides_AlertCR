"""AOI construction and site filtering for CNE landslide features."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point


def ensure_site_id(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = gdf.copy()
    if "site_id" not in out.columns:
        out["site_id"] = [f"CNE-{i+1:04d}" for i in range(len(out))]
    if "name" not in out.columns:
        out["name"] = out["site_id"]
    out["name"] = out["name"].fillna("Sin nombre").astype(str)
    # Prefer meaningful display labels
    blankish = out["name"].str.strip().isin(["", "Polígono sin título", "Poligono sin titulo"])
    out.loc[blankish, "name"] = out.loc[blankish, "site_id"]
    return out


def filter_sites(
    gdf: gpd.GeoDataFrame,
    *,
    text: str = "",
    min_area: float | None = None,
    max_features: int | None = None,
) -> gpd.GeoDataFrame:
    out = ensure_site_id(gdf)
    if text:
        q = text.strip().lower()
        mask = out["name"].str.lower().str.contains(q, na=False)
        if "site_id" in out.columns:
            mask = mask | out["site_id"].str.lower().str.contains(q, na=False)
        out = out.loc[mask].copy()
    if min_area is not None and "area" in out.columns:
        area = pd.to_numeric(out["area"], errors="coerce").fillna(0)
        out = out.loc[area >= float(min_area)].copy()
    if max_features is not None and len(out) > max_features:
        # Prefer larger areas when capping
        if "area" in out.columns:
            out = out.assign(_area=pd.to_numeric(out["area"], errors="coerce").fillna(0))
            out = out.sort_values("_area", ascending=False).head(max_features).drop(columns=["_area"])
        else:
            out = out.head(max_features)
    return out.reset_index(drop=True)


def build_aois(
    gdf: gpd.GeoDataFrame,
    *,
    buffer_meters: float = 400.0,
) -> gpd.GeoDataFrame:
    """
    Build monitoring AOIs: centroid + metric buffer around each feature.
    Returns GeoDataFrame in EPSG:4326 with columns site_id, name, aoi_geometry, centroid.
    """
    sites = ensure_site_id(gdf)
    if sites.empty:
        return gpd.GeoDataFrame(
            columns=["site_id", "name", "area", "centroid", "geometry"],
            geometry="geometry",
            crs="EPSG:4326",
        )

    # Project to CRTM05 (EPSG:5367) for metric buffers in Costa Rica
    metric = sites.to_crs("EPSG:5367")
    centroids_m = metric.geometry.centroid
    aois_m = centroids_m.buffer(float(buffer_meters))

    aois = gpd.GeoDataFrame(
        {
            "site_id": sites["site_id"].values,
            "name": sites["name"].values,
            "area": sites["area"].values if "area" in sites.columns else None,
        },
        geometry=aois_m,
        crs="EPSG:5367",
    ).to_crs("EPSG:4326")

    cents = gpd.GeoSeries(centroids_m, crs="EPSG:5367").to_crs("EPSG:4326")
    aois["centroid_lon"] = cents.x.values
    aois["centroid_lat"] = cents.y.values
    aois["centroid"] = [Point(xy) for xy in zip(aois["centroid_lon"], aois["centroid_lat"])]
    return aois


def site_bbox(geom, pad_deg: float = 0.02) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = geom.bounds
    return (minx - pad_deg, miny - pad_deg, maxx + pad_deg, maxy + pad_deg)
