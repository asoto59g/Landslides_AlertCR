"""DEM download (Google Drive) and local slope sampling for an AOI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def dem_path(dem_dir: Path, filename: str = "dem_cr.tif") -> Path:
    return Path(dem_dir) / filename


def ensure_dem(
    dem_dir: Path,
    *,
    google_drive_id: str,
    filename: str = "dem_cr.tif",
) -> tuple[Path | None, str]:
    """
    Ensure DEM exists locally. Downloads via gdown if missing.
    Returns (path_or_None, status_message).
    """
    dem_dir = Path(dem_dir)
    dem_dir.mkdir(parents=True, exist_ok=True)
    path = dem_path(dem_dir, filename)
    if path.exists() and path.stat().st_size > 1000:
        return path, f"DEM local: {path.name}"

    try:
        import gdown
    except ImportError:
        return None, "Instale gdown para descargar el DEM (pip install gdown)."

    url = f"https://drive.google.com/uc?id={google_drive_id}"
    try:
        out = gdown.download(url, str(path), quiet=False)
        if out and Path(out).exists():
            return Path(out), f"DEM descargado: {Path(out).name}"
        return None, "Descarga DEM falló (verifique permisos del enlace de Drive)."
    except Exception as exc:  # noqa: BLE001
        return None, f"Error descargando DEM: {exc}"


def sample_slope_stats(
    dem_file: Path,
    geom,
    *,
    max_pixels: int = 250_000,
) -> dict[str, Any]:
    """
    Sample elevation and approximate slope (%) inside a geometry (EPSG:4326).
    Returns empty/partial stats if rasterio unavailable or DEM missing.
    """
    try:
        import rasterio
        from rasterio.mask import mask
    except ImportError:
        return {"ok": False, "error": "rasterio no instalado"}

    if dem_file is None or not Path(dem_file).exists():
        return {"ok": False, "error": "DEM no disponible"}

    try:
        with rasterio.open(dem_file) as src:
            # Reproject geometry to DEM CRS if needed
            gdf_crs = "EPSG:4326"
            geoms = [geom.__geo_interface__]
            try:
                import geopandas as gpd
                from shapely.geometry import shape as shp_shape

                gtmp = gpd.GeoDataFrame(geometry=[geom], crs=gdf_crs)
                if src.crs is not None:
                    gtmp = gtmp.to_crs(src.crs)
                geoms = [gtmp.geometry.iloc[0].__geo_interface__]
            except Exception:
                pass

            out_img, out_transform = mask(src, geoms, crop=True, filled=False)
            band = out_img[0]
            if hasattr(band, "filled"):
                data = band.filled(np.nan).astype(float)
            else:
                data = np.array(band, dtype=float)
                nodata = src.nodata
                if nodata is not None:
                    data[data == nodata] = np.nan

            valid = data[np.isfinite(data)]
            if valid.size == 0:
                return {"ok": False, "error": "Sin píxeles DEM en el AOI"}

            # Downsample for slope if huge
            step = 1
            elev = data
            if elev.size > max_pixels:
                step = int(np.ceil(np.sqrt(elev.size / max_pixels)))
                elev = elev[::step, ::step]

            dy, dx = np.gradient(elev)
            if src.crs is not None and getattr(src.crs, "is_geographic", False):
                res_x = abs(out_transform.a) * step * 111_320 * np.cos(
                    np.deg2rad(geom.centroid.y)
                )
                res_y = abs(out_transform.e) * step * 110_540
            else:
                res_x = abs(out_transform.a) * step
                res_y = abs(out_transform.e) * step

            slope_rad = np.arctan(
                np.sqrt((dx / max(res_x, 1e-9)) ** 2 + (dy / max(res_y, 1e-9)) ** 2)
            )
            slope_pct = np.tan(slope_rad) * 100.0
            slope_valid = slope_pct[np.isfinite(elev) & np.isfinite(slope_pct)]

            return {
                "ok": True,
                "elev_min": float(np.nanmin(valid)),
                "elev_max": float(np.nanmax(valid)),
                "elev_mean": float(np.nanmean(valid)),
                "slope_mean_pct": float(np.nanmean(slope_valid)) if slope_valid.size else None,
                "slope_p90_pct": float(np.nanpercentile(slope_valid, 90)) if slope_valid.size else None,
                "n_pixels": int(valid.size),
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
