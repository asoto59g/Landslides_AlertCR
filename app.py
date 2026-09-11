"""Streamlit early-warning dashboard for Costa Rica landslides (Sentinel-1 + CNE)."""

from __future__ import annotations

import sys
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
import yaml
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.alerts import AlertThresholds, LEVEL_COLOR, classify_alert  # noqa: E402
from src.cne_wfs import load_coronas, load_deslizamientos  # noqa: E402
from src.deformation import (  # noqa: E402
    compute_metrics,
    make_demo_series,
    parse_deformation_csv,
)
from src.dem import ensure_dem, sample_slope_stats  # noqa: E402
from src.insar import (  # noqa: E402
    build_consecutive_pairs,
    get_earthdata_creds,
    load_series_csv,
    refresh_and_ingest_site,
    run_insar_for_site,
    search_slc_stack,
    site_insar_dir,
)
from src.sentinel import demo_catalog_rows, search_sentinel1  # noqa: E402
from src.sites import build_aois, filter_sites, site_bbox  # noqa: E402


st.set_page_config(
    page_title="Alerta Laderas CR",
    page_icon="⛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data
def load_config() -> dict:
    path = ROOT / "config.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _path(cfg: dict, key: str) -> Path:
    return ROOT / cfg["paths"][key]


@st.cache_data(show_spinner="Cargando sitios CNE…")
def cached_sites(source: str, force: bool, ttl: float) -> tuple:
    cfg = load_config()
    gdf, msg = load_deslizamientos(
        source=source,
        wfs_url=cfg["wfs"]["url"],
        layer=cfg["wfs"]["deslizamientos"],
        cache_dir=_path(cfg, "cache_dir"),
        local_geojson=_path(cfg, "local_geojson"),
        ttl_hours=ttl,
        timeout=int(cfg["wfs"].get("timeout_seconds", 120)),
        force_refresh=force,
    )
    # Convert to WKT records for cacheability
    out = gdf.copy()
    return out.to_json(), msg


@st.cache_data(show_spinner="Cargando coronas CNE…")
def cached_coronas(force: bool, ttl: float, max_feat: int) -> tuple:
    cfg = load_config()
    gdf, msg = load_coronas(
        wfs_url=cfg["wfs"]["url"],
        layer=cfg["wfs"]["coronas"],
        cache_dir=_path(cfg, "cache_dir"),
        ttl_hours=ttl,
        timeout=int(cfg["wfs"].get("timeout_seconds", 120)),
        force_refresh=force,
        max_features=max_feat,
    )
    return gdf.to_json(), msg


def gdf_from_json(js: str):
    import geopandas as gpd
    from io import StringIO

    from src.cne_wfs import normalize_crs_wgs84

    return normalize_crs_wgs84(gpd.read_file(StringIO(js)))


def map_center(sites_gdf) -> tuple[float, float]:
    """Map center without unary_union (avoids GEOSException on invalid WFS polygons)."""
    try:
        minx, miny, maxx, maxy = sites_gdf.total_bounds
        lat = (miny + maxy) / 2.0
        lon = (minx + maxx) / 2.0
        # Reject projected leftovers (must be WGS84 lon/lat for Folium)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return (float(lat), float(lon))
    except Exception:
        pass
    return (9.9, -84.1)


def build_map(sites_gdf, coronas_gdf, selected_id: str | None, alert_by_id: dict):
    if sites_gdf.empty:
        m = folium.Map(location=[9.9, -84.1], zoom_start=8)
        return m

    lat, lon = map_center(sites_gdf)
    m = folium.Map(location=[lat, lon], zoom_start=10, tiles="OpenStreetMap")
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satélite",
        overlay=False,
        control=True,
    ).add_to(m)

    if coronas_gdf is not None and not coronas_gdf.empty:
        try:
            folium.GeoJson(
                coronas_gdf.__geo_interface__,
                name="Coronas CNE",
                style_function=lambda _: {"color": "#8d6e63", "weight": 1, "opacity": 0.5},
            ).add_to(m)
        except Exception:
            pass

    for _, row in sites_gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        sid = row.get("site_id", "")
        level = alert_by_id.get(sid)
        color = LEVEL_COLOR.get(level, "#1565c0") if level else "#1565c0"
        weight = 4 if sid == selected_id else 2
        try:
            folium.GeoJson(
                geom.__geo_interface__,
                name=str(row.get("name", sid)),
                style_function=lambda _feat, c=color, w=weight: {
                    "color": c,
                    "weight": w,
                    "fillOpacity": 0.25,
                    "fillColor": c,
                },
                tooltip=f"{row.get('name', sid)} ({sid})",
            ).add_to(m)
        except Exception:
            continue

    folium.LayerControl().add_to(m)
    return m


def main():
    cfg = load_config()
    st.title("Alerta temprana de laderas — Costa Rica")
    st.caption("Monitoreo Sentinel-1 (InSAR) sobre sitios CNE · deformación ≠ predicción de colapso")

    with st.expander("Aviso operativo (Shirzaei / Sah)", expanded=True):
        st.markdown(
            cfg.get(
                "disclaimer",
                "Monitoreo de deformación; no predicción determinista del momento de fallo.",
            )
        )
        st.markdown(
            "- **Shirzaei:** InSAR Sentinel-1 puede medir movimiento lento (~10 mm/mes); "
            "la aceleración puede ser más relevante que la velocidad absoluta.\n"
            "- **Sah:** la amplitud VV/VH sola no es un precursor robusto; "
            "hay que discriminar estacionalidad y no confundir deformación lenta con fecha de colapso."
        )

    # Sidebar
    st.sidebar.header("Datos")
    source = st.sidebar.selectbox(
        "Fuente de sitios",
        options=["wfs", "cache", "local"],
        format_func=lambda x: {
            "wfs": "WFS CNE (en vivo / caché)",
            "cache": "Solo caché local",
            "local": "GeoJSON local",
        }[x],
    )
    force = st.sidebar.button("Forzar refresco WFS")
    text = st.sidebar.text_input("Filtrar por nombre", "")
    min_area = st.sidebar.number_input("Área mínima (atributo CNE)", min_value=0.0, value=0.0, step=0.5)
    max_map = st.sidebar.slider("Máx. sitios en mapa", 20, 500, 120, 10)
    show_coronas = st.sidebar.checkbox("Mostrar coronas (muestra)", value=False)
    buffer_m = st.sidebar.slider("Buffer AOI (m)", 100, 1000, int(cfg["aoi"]["buffer_meters"]), 50)

    st.sidebar.header("Umbrales de alerta")
    th = AlertThresholds(
        green_max_mm_month=st.sidebar.number_input("Verde máx. |v| mm/mes", value=float(cfg["alerts"]["green_max_mm_month"])),
        orange_min_mm_month=st.sidebar.number_input("Naranja mín. |v| mm/mes", value=float(cfg["alerts"]["orange_min_mm_month"])),
        accel_mild_mm_month2=st.sidebar.number_input("Acel. leve mm/mes²", value=float(cfg["alerts"]["accel_mild_mm_month2"])),
        accel_clear_mm_month2=st.sidebar.number_input("Acel. clara mm/mes²", value=float(cfg["alerts"]["accel_clear_mm_month2"])),
    )

    st.sidebar.header("Serie de deformación")
    series_mode = st.sidebar.radio("Modo", ["Demo", "CSV", "InSAR"], horizontal=True)
    demo_scenario = st.sidebar.selectbox(
        "Escenario demo",
        ["stable", "slow_shirzaei", "accelerating", "seasonal_noise"],
        format_func=lambda s: {
            "stable": "Estable (~2 mm/mes)",
            "slow_shirzaei": "Lento tipo Shirzaei (~10 mm/mes)",
            "accelerating": "Con aceleración",
            "seasonal_noise": "Tendencia + estacional (Sah)",
        }[s],
    )
    csv_file = None
    if series_mode == "CSV":
        csv_file = st.sidebar.file_uploader("CSV (fecha, los_mm)", type=["csv"])

    # Load sites
    ttl = float(cfg["wfs"].get("cache_ttl_hours", 24))
    try:
        sites_json, sites_msg = cached_sites(source, force, ttl)
        sites = gdf_from_json(sites_json)
    except Exception as exc:
        st.error(f"No se pudieron cargar sitios: {exc}")
        return

    sites = filter_sites(
        sites,
        text=text,
        min_area=min_area if min_area > 0 else None,
        max_features=max_map,
    )
    st.info(f"{sites_msg} · {len(sites)} sitios visibles")

    coronas = None
    if show_coronas:
        try:
            cjson, cmsg = cached_coronas(force, ttl, 1500)
            coronas = gdf_from_json(cjson)
            st.caption(cmsg)
        except Exception as exc:
            st.warning(f"Coronas: {exc}")

    if sites.empty:
        st.warning("Sin sitios con los filtros actuales.")
        return

    aois = build_aois(sites, buffer_meters=buffer_m)
    labels = [f"{r.name} [{r.site_id}]" for r in sites.itertuples()]
    choice = st.selectbox("Sitio de monitoreo", options=list(range(len(labels))), format_func=lambda i: labels[i])
    site_row = sites.iloc[choice]
    aoi_row = aois.iloc[choice]
    selected_id = site_row["site_id"]

    # Deformation series for selected site
    insar_root = _path(cfg, "insar_dir") if "insar_dir" in cfg.get("paths", {}) else ROOT / "data" / "insar"
    if series_mode == "CSV" and csv_file is not None:
        try:
            series = parse_deformation_csv(csv_file)
            series_label = "CSV cargado"
        except Exception as exc:
            st.error(f"CSV inválido: {exc}")
            series = make_demo_series(scenario=demo_scenario, seed=hash(selected_id) % 10_000)
            series_label = f"Demo ({demo_scenario}) — fallback"
    elif series_mode == "InSAR":
        existing = load_series_csv(site_insar_dir(insar_root, selected_id) / "los_series.csv")
        if existing is not None and not existing.empty:
            series = existing
            series_label = "InSAR (serie local)"
        else:
            series = make_demo_series(scenario=demo_scenario, seed=hash(selected_id) % 10_000)
            series_label = "InSAR pendiente — usando demo hasta procesar"
    else:
        series = make_demo_series(scenario=demo_scenario, seed=hash(selected_id) % 10_000)
        series_label = f"Demo ({demo_scenario})"

    metrics = compute_metrics(series)
    alert = classify_alert(metrics.velocity_mm_month, metrics.acceleration_mm_month2, th)

    # Map coloring: prefer InSAR series per site when available
    alert_by_id = {}
    for _, r in sites.iterrows():
        sid = r["site_id"]
        if sid == selected_id:
            alert_by_id[sid] = alert.level
            continue
        local = load_series_csv(site_insar_dir(insar_root, sid) / "los_series.csv")
        if local is not None and not local.empty:
            m = compute_metrics(local)
            alert_by_id[sid] = classify_alert(m.velocity_mm_month, m.acceleration_mm_month2, th).level
        else:
            s = make_demo_series(scenario=demo_scenario, seed=hash(sid) % 10_000)
            m = compute_metrics(s)
            alert_by_id[sid] = classify_alert(m.velocity_mm_month, m.acceleration_mm_month2, th).level

    tab_map, tab_site, tab_s1, tab_insar, tab_science = st.tabs(
        ["Mapa", "Sitio / alerta", "Catálogo Sentinel-1", "InSAR E2E", "Base científica"]
    )

    with tab_map:
        m = build_map(sites, coronas, selected_id, alert_by_id)
        st_folium(m, width=None, height=560, returned_objects=[])

    with tab_site:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Nivel", alert.level.value)
        c2.metric("Velocidad LOS", f"{metrics.velocity_mm_month:.1f} mm/mes")
        c3.metric("Aceleración", f"{metrics.acceleration_mm_month2:.2f} mm/mes²")
        c4.metric("Serie", series_label)

        color = LEVEL_COLOR[alert.level]
        st.markdown(
            f"<div style='padding:0.75rem 1rem;border-left:6px solid {color};background:#f5f5f5;'>"
            f"<b>{alert.message}</b><br/>{alert.action}</div>",
            unsafe_allow_html=True,
        )

        st.subheader(site_row.get("name", selected_id))
        meta_cols = st.columns(3)
        meta_cols[0].write(f"**ID:** {selected_id}")
        meta_cols[1].write(f"**Área (CNE):** {site_row.get('area', '—')}")
        meta_cols[2].write(
            f"**Centroide:** {aoi_row['centroid_lat']:.5f}, {aoi_row['centroid_lon']:.5f}"
        )

        st.markdown("#### Serie temporal LOS (mm)")
        chart_df = metrics.series.set_index("date")[["los_mm"]]
        st.line_chart(chart_df)

        st.markdown("#### Contexto DEM")
        dem_dir = _path(cfg, "dem_dir")
        if st.button("Preparar / usar DEM CR"):
            with st.spinner("Descargando o localizando DEM…"):
                dem_file, dem_msg = ensure_dem(
                    dem_dir,
                    google_drive_id=cfg["dem"]["google_drive_id"],
                    filename=cfg["dem"]["filename"],
                )
            st.session_state["dem_file"] = str(dem_file) if dem_file else None
            st.session_state["dem_msg"] = dem_msg

        dem_msg = st.session_state.get("dem_msg", "DEM aún no preparado.")
        st.caption(dem_msg)
        dem_file = st.session_state.get("dem_file")
        if dem_file:
            stats = sample_slope_stats(Path(dem_file), aoi_row.geometry)
            if stats.get("ok"):
                d1, d2, d3 = st.columns(3)
                d1.metric("Elev. media", f"{stats['elev_mean']:.0f} m")
                d2.metric("Pendiente media", f"{stats['slope_mean_pct']:.1f} %")
                d3.metric("Pendiente p90", f"{stats['slope_p90_pct']:.1f} %")
            else:
                st.warning(stats.get("error", "No se pudo muestrear el DEM"))

    with tab_s1:
        st.markdown(
            "Catálogo de escenas **Sentinel-1 GRD** (ASF) sobre el AOI del sitio. "
            "Para interferometría use la pestaña **InSAR E2E** (SLC + HyP3)."
        )
        use_asf = st.checkbox("Consultar ASF (requiere asf_search + red)", value=True)
        bbox = site_bbox(aoi_row.geometry, pad_deg=0.05)
        if use_asf:
            with st.spinner("Buscando escenas Sentinel-1…"):
                cat, cat_msg = search_sentinel1(
                    bbox, max_results=int(cfg["sentinel1"].get("max_results", 30))
                )
            if cat.empty:
                st.warning(cat_msg)
                st.dataframe(demo_catalog_rows(), use_container_width=True)
                st.caption("Mostrando catálogo demo (revisitas ~12 días).")
            else:
                st.success(cat_msg)
                st.dataframe(cat, use_container_width=True)
        else:
            st.dataframe(demo_catalog_rows(), use_container_width=True)

    with tab_insar:
        st.markdown(
            """
### Pipeline InSAR end-to-end (solo sitios filtrados / seleccionados)

1. Buscar **SLC** Sentinel-1 sobre el AOI  
2. Armar pares consecutivos (misma órbita / dirección)  
3. Enviar jobs **ASF HyP3** InSAR (`include_los_displacement`)  
4. Descargar productos y muestrear **LOS medio** en el AOI  
5. Construir serie acumulada → alertas (velocidad / aceleración)

No se procesa el país completo: únicamente el sitio actual o un lote acotado de los sitios **ya filtrados** en el mapa.
"""
        )
        icfg = cfg.get("insar", {})
        user, pwd = get_earthdata_creds()

        with st.expander("Cómo configurar Earthdata (HyP3)", expanded=not bool(user and pwd)):
            st.markdown(
                """
**Opción A — Streamlit Cloud (recomendado para la app publicada)**

1. Abra su app en [share.streamlit.io](https://share.streamlit.io/) / Streamlit Cloud  
2. En la app desplegada: menú **⋮** (arriba a la derecha) → **Settings** → **Secrets**  
   (también: *Manage app* → **Settings** → **Secrets**)  
3. Pegue exactamente:

```toml
EARTHDATA_USERNAME = "su_usuario_urs"
EARTHDATA_PASSWORD = "su_password_urs"
```

4. Guarde y haga **Reboot** de la app  

Cuenta NASA: [urs.earthdata.nasa.gov](https://urs.earthdata.nasa.gov/) (crear usuario si no tiene).

**Opción B — Local (PC)**

Cree el archivo `.streamlit/secrets.toml` en la raíz del proyecto (hay un ejemplo en `.streamlit/secrets.toml.example`).

**Opción C — Esta sesión (abajo)**  
Ingrese usuario/contraseña solo para esta sesión del navegador (no se guarda en el repo).
"""
            )

        with st.form("earthdata_form"):
            st.markdown("#### Credenciales Earthdata (sesión)")
            f_user = st.text_input(
                "EARTHDATA_USERNAME",
                value=user or "",
                help="Usuario de https://urs.earthdata.nasa.gov/",
            )
            f_pwd = st.text_input(
                "EARTHDATA_PASSWORD",
                value="",
                type="password",
                help="No se sube a GitHub; solo vive en session_state de Streamlit.",
            )
            saved = st.form_submit_button("Usar en esta sesión")
            if saved:
                if f_user and f_pwd:
                    st.session_state["earthdata_username"] = f_user.strip()
                    st.session_state["earthdata_password"] = f_pwd
                    st.success("Credenciales guardadas en esta sesión. Ya puede enviar jobs HyP3.")
                    st.rerun()
                else:
                    st.error("Usuario y contraseña son obligatorios.")

        user, pwd = get_earthdata_creds()
        if user and pwd:
            st.success(f"Earthdata listo para HyP3: `{user}`")
        else:
            st.warning(
                "Aún no hay credenciales. Use el formulario de arriba, Secrets de Streamlit Cloud, "
                "o `.streamlit/secrets.toml` en local."
            )

        c_a, c_b, c_c = st.columns(3)
        start = c_a.date_input("Inicio búsqueda SLC", value=pd.Timestamp("2025-01-01").date())
        end = c_b.date_input("Fin búsqueda SLC", value=pd.Timestamp.today().date())
        max_pairs = c_c.slider("Máx. pares / sitio", 1, 8, int(icfg.get("max_pairs", 4)))
        max_base = st.slider(
            "Baseline temporal máx. (días)",
            12,
            96,
            int(icfg.get("max_temp_baseline_days", 48)),
            6,
        )
        wait_jobs = st.checkbox(
            "Esperar jobs HyP3 (puede tardar 30–90+ min)",
            value=False,
            help="Si está desmarcado, se envían jobs y puede refrescar después.",
        )
        scope = st.radio(
            "Alcance",
            ["Solo sitio seleccionado", "Lote de sitios filtrados"],
            horizontal=True,
        )
        max_batch = st.slider(
            "Máx. sitios en lote",
            1,
            int(icfg.get("max_sites_batch", 3)) + 2,
            int(icfg.get("max_sites_batch", 3)),
        )

        bbox = site_bbox(aoi_row.geometry, pad_deg=0.05)
        if st.button("Vista previa pares SLC (sitio seleccionado)"):
            with st.spinner("Buscando SLC…"):
                stack, msg = search_slc_stack(
                    bbox,
                    start=str(start),
                    end=str(end),
                    max_results=50,
                )
            st.caption(msg)
            if not stack.empty:
                pairs = build_consecutive_pairs(
                    stack, max_pairs=max_pairs, max_temp_baseline_days=max_base
                )
                st.dataframe(stack, use_container_width=True)
                st.write(f"**{len(pairs)} pares** propuestos")
                if pairs:
                    st.dataframe(
                        pd.DataFrame(
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
                        ),
                        use_container_width=True,
                    )

        col_run, col_ref = st.columns(2)
        run_clicked = col_run.button("Ejecutar InSAR E2E", type="primary")
        refresh_clicked = col_ref.button("Refrescar jobs / ingerir productos")

        targets = []
        if scope.startswith("Solo"):
            targets = [(selected_id, aoi_row.geometry, site_bbox(aoi_row.geometry, 0.05))]
        else:
            n = min(max_batch, len(aois))
            for i in range(n):
                row = aois.iloc[i]
                targets.append((row["site_id"], row.geometry, site_bbox(row.geometry, 0.05)))

        if run_clicked:
            progress = st.progress(0.0)
            logs = []
            for i, (sid, geom, bb) in enumerate(targets):
                with st.spinner(f"InSAR {sid} ({i+1}/{len(targets)})…"):
                    res = run_insar_for_site(
                        site_id=sid,
                        aoi_geom=geom,
                        bbox=bb,
                        out_root=insar_root,
                        start=str(start),
                        end=str(end),
                        max_pairs=max_pairs,
                        max_temp_baseline_days=max_base,
                        submit=True,
                        wait=wait_jobs,
                    )
                logs.append(f"**{sid}**: {res.get('message')}")
                if res.get("pairs") is not None and not res["pairs"].empty:
                    logs.append(f"- Pares: {len(res['pairs'])}")
                progress.progress((i + 1) / len(targets))
            for line in logs:
                st.markdown(line)
            st.info("Ponga el modo de serie en **InSAR** (sidebar) para usar `los_series.csv` en alertas.")
            st.rerun()

        if refresh_clicked:
            for sid, geom, _bb in targets:
                with st.spinner(f"Refrescando {sid}…"):
                    res = refresh_and_ingest_site(
                        site_id=sid,
                        aoi_geom=geom,
                        out_root=insar_root,
                    )
                st.write(f"**{sid}**: {res.get('message')}")
                if res.get("series") is not None and not res["series"].empty:
                    st.line_chart(res["series"].set_index("date")[["los_mm"]])
            st.rerun()

        local_series = load_series_csv(site_insar_dir(insar_root, selected_id) / "los_series.csv")
        if local_series is not None and not local_series.empty:
            st.subheader(f"Serie InSAR local — {selected_id}")
            st.dataframe(local_series, use_container_width=True)
            st.line_chart(local_series.set_index("date")[["los_mm"]])

    with tab_science:
        st.markdown(
            """
### Qué aporta Sentinel-1 InSAR (Shirzaei)

| Parámetro | Lectura operativa |
|-----------|-------------------|
| Técnica | Radar Sentinel-1, deformación InSAR (LOS) |
| Magnitud de referencia | ~10 mm/mes (movimiento lento, no avalancha) |
| Señal crítica | Posible **aceleración**, más que la velocidad absoluta |
| Límite | Detectar deformación ≠ fijar fecha de colapso |

### Matiz de Sah (EarthArXiv)

- No hay precursor robusto solo con **amplitud** VV/VH.
- Cambios aparentes pueden ser **estacionales**.
- El movimiento de varios meses puede ser medible, pero un sistema que mire cada ~12 días no necesariamente emite alerta oportuna del instante de fallo.

### Diseño de esta app para Costa Rica

1. Sitios oficiales desde el **WFS CNE** (`cne:deslizamientos`).
2. Monitoreo priorizado por **velocidad + aceleración** LOS.
3. **InSAR E2E** vía ASF HyP3 solo en sitios filtrados/seleccionados.
4. Alertas = **escalamiento a inspección**, no sirena de colapso inminente.
"""
        )


if __name__ == "__main__":
    main()
