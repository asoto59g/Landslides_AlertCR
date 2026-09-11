# Alerta temprana de laderas — Costa Rica

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
