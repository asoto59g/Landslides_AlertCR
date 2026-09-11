# Alerta temprana de laderas — Costa Rica

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-App-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Sentinel-1](https://img.shields.io/badge/Sentinel--1-InSAR-0B3D91?logo=esa&logoColor=white)](https://sentinel.esa.int/web/sentinel/missions/sentinel-1)
[![CNE WFS](https://img.shields.io/badge/CNE-WFS-1B5E20)](http://mapas.cne.go.cr/servicios/cne/wfs)
[![GeoPandas](https://img.shields.io/badge/GeoPandas-GIS-orange)](https://geopandas.org/)
[![ASF](https://img.shields.io/badge/ASF-asf__search-6A1B9A)](https://github.com/asfadmin/Discovery-asf_search)
[![HyP3](https://img.shields.io/badge/ASF-HyP3%20InSAR-004B87)](https://hyp3-docs.asf.alaska.edu/)
[![GitHub last commit](https://img.shields.io/github/last-commit/asoto59g/Landslides_AlertCR)](https://github.com/asoto59g/Landslides_AlertCR)
[![GitHub repo](https://img.shields.io/badge/GitHub-Landslides__AlertCR-181717?logo=github)](https://github.com/asoto59g/Landslides_AlertCR)

Monitoreo de deformación con **Sentinel-1 (InSAR)** sobre sitios del **CNE**, con umbrales inspirados en Shirzaei y disclaimers de Sah.

## Qué hace

- Carga deslizamientos desde el WFS CNE (`cne:deslizamientos` / coronas)
- AOIs con buffer métrico (CRTM05)
- Series LOS: demo, CSV o **InSAR end-to-end** (solo sitios filtrados/seleccionados)
- Alertas Verde / Amarillo / Naranja / Rojo (priorizar inspección, no predicción de colapso)
- Catálogo Sentinel-1 (ASF) y contexto DEM CR

## InSAR end-to-end (sitios seleccionados)

Pestaña **InSAR E2E**:

1. Busca SLC Sentinel-1 sobre el AOI
2. Arma pares consecutivos (misma path / dirección)
3. Envía jobs [ASF HyP3](https://hyp3-docs.asf.alaska.edu/) InSAR con desplazamiento LOS
4. Descarga productos y muestrea el AOI → `data/insar/<site_id>/los_series.csv`
5. Active el modo de serie **InSAR** en el sidebar para alimentar alertas

Alcance: **solo el sitio seleccionado** o un **lote acotado** de los sitios ya filtrados (no el país completo).

### Credenciales Earthdata

```toml
# .streamlit/secrets.toml
EARTHDATA_USERNAME = "su_usuario"
EARTHDATA_PASSWORD = "su_password"
```

Cuenta: [URS Earthdata](https://urs.earthdata.nasa.gov/). Ejemplo en `.streamlit/secrets.toml.example`.

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Configuración

Editar `config.yaml` (WFS, umbrales, DEM, `insar.max_pairs`, `insar.max_sites_batch`).

## Nota científica

Detectar movimiento lento medible **no** equivale a determinar cuándo fallará una ladera.
