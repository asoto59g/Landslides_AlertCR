"""Sentinel-1 scene catalog via ASF (asf_search), with graceful fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


def search_sentinel1(
    bbox: tuple[float, float, float, float],
    *,
    max_results: int = 30,
    start: str | None = None,
    end: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """
    Search Sentinel-1 GRD scenes intersecting bbox (minx, miny, maxx, maxy) in WGS84.

    Returns (dataframe, status_message). Empty frame if asf_search unavailable.
    """
    try:
        import asf_search as asf
    except ImportError:
        return (
            pd.DataFrame(),
            "Instale asf_search para consultar el catálogo Sentinel-1 (pip install asf_search).",
        )

    minx, miny, maxx, maxy = bbox
    # WKT polygon
    wkt = (
        f"POLYGON(({minx} {miny},{maxx} {miny},{maxx} {maxy},{minx} {maxy},{minx} {miny}))"
    )

    opts: dict[str, Any] = {
        "platform": asf.PLATFORM.SENTINEL1,
        "processingLevel": asf.PRODUCT_TYPE.GRD_HD,
        "intersectsWith": wkt,
        "maxResults": int(max_results),
    }
    if start:
        opts["start"] = start
    if end:
        opts["end"] = end

    try:
        results = asf.geo_search(**opts)
        rows = []
        for r in results:
            props = r.properties if hasattr(r, "properties") else {}
            rows.append(
                {
                    "scene": props.get("sceneName") or props.get("fileID") or str(r),
                    "startTime": props.get("startTime"),
                    "stopTime": props.get("stopTime"),
                    "flightDirection": props.get("flightDirection"),
                    "pathNumber": props.get("pathNumber"),
                    "frameNumber": props.get("frameNumber"),
                    "polarization": props.get("polarization"),
                    "url": props.get("url") or props.get("browse"),
                    "platform": props.get("platform"),
                }
            )
        df = pd.DataFrame(rows)
        if not df.empty and "startTime" in df.columns:
            df = df.sort_values("startTime", ascending=False).reset_index(drop=True)
        msg = f"{len(df)} escenas Sentinel-1 (ASF) para el AOI"
        return df, msg
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), f"Búsqueda ASF falló: {exc}"


def demo_catalog_rows(n: int = 8) -> pd.DataFrame:
    """Placeholder catalog when ASF is offline — 12-day revisits."""
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(n):
        day = now.toordinal() - i * 12
        dt = datetime.fromordinal(day).replace(tzinfo=timezone.utc)
        rows.append(
            {
                "scene": f"S1A_IW_GRDH_DEMO_{dt.strftime('%Y%m%d')}",
                "startTime": dt.isoformat(),
                "stopTime": dt.isoformat(),
                "flightDirection": "ASCENDING" if i % 2 == 0 else "DESCENDING",
                "pathNumber": 151,
                "frameNumber": 500 + i,
                "polarization": "VV+VH",
                "url": "",
                "platform": "Sentinel-1A",
            }
        )
    return pd.DataFrame(rows)
