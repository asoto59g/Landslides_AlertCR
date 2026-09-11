# Alerta temprana de laderas — Costa Rica

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-App-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Sentinel-1](https://img.shields.io/badge/Sentinel--1-InSAR-0B3D91?logo=esa&logoColor=white)](https://sentinel.esa.int/web/sentinel/missions/sentinel-1)
[![CNE WFS](https://img.shields.io/badge/CNE-WFS-1B5E20)](http://mapas.cne.go.cr/servicios/cne/wfs)
[![GeoPandas](https://img.shields.io/badge/GeoPandas-GIS-orange)](https://geopandas.org/)
[![ASF](https://img.shields.io/badge/ASF-asf__search-6A1B9A)](https://github.com/asfadmin/Discovery-asf_search)
[![GitHub last commit](https://img.shields.io/github/last-commit/asoto59g/Landslides_AlertCR)](https://github.com/asoto59g/Landslides_AlertCR)
[![GitHub repo](https://img.shields.io/badge/GitHub-Landslides__AlertCR-181717?logo=github)](https://github.com/asoto59g/Landslides_AlertCR)

Monitoreo de deformación con **Sentinel-1 (InSAR)** sobre sitios del **CNE**, con umbrales inspirados en Shirzaei y disclaimers de Sah.

## Qué hace (v1)

- Carga deslizamientos desde el WFS CNE: `http://mapas.cne.go.cr/servicios/cne/wfs` (`cne:deslizamientos`, opcional `cne:coronas_de_deslizamientos`)
- Fallback a `data/Deslizamientos_CNE.geojson` si el WFS no responde
- AOIs con buffer métrico (CRTM05)
- Series LOS demo (~10 mm/mes / aceleración / estacional) o CSV (`fecha, los_mm`)
- Alertas Verde / Amarillo / Naranja / Rojo (priorizar inspección, no predicción de colapso)
- Catálogo Sentinel-1 vía ASF (`asf_search`)
- Contexto DEM CR (Google Drive) con muestreo de pendiente

## Instalación

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecutar

```bash
streamlit run app.py
```

## Configuración

Editar `config.yaml` (URL WFS, umbrales, id del DEM en Drive, TTL de caché).

## Nota científica

Detectar movimiento lento medible **no** equivale a determinar cuándo fallará una ladera. La app es una herramienta de priorización y seguimiento.
