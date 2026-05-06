# 🌞 Solar Finder

Automatisierte Flächensuche für Solarparks im Raum Frankfurt.

## Was macht dieses Tool?

Analysiert Geodaten aus ALKIS (Kataster) und OpenStreetMap um geeignete 
Grundstücke für Solarparks zu identifizieren und zu bewerten.

**Filterkriterien:**
- Fläche größer 7 Hektar
- Landnutzung: Ackerland, Grünland, Streuobst
- Weniger als 2km Entfernung zu Autobahn oder Bahnlinie

**Scoring-Modell:**
- 40% Flächengröße
- 60% Nähe zur Infrastruktur

## Ergebnisse

- Web Karte (HTML) mit Farbkodierung nach Eignung
- GeoJSON-Export der potentiellen Flächen
- CSV-Export 

## Installation

**Voraussetzungen:** Conda

```bash
# Umgebung erstellen
conda create -n solar python=3.11 -y
conda activate solar

# GIS-Pakete installieren
conda install -c conda-forge geopandas shapely rasterio fiona pyproj jupyterlab -y
pip install overpy requests pandas scikit-learn folium
```

## Verwendung

```bash
conda activate solar
cd solar-finder

# Skript ausführen
python scripts/main.py

# Oder Jupyter starten
jupyter lab
```

## Testgebiet ändern

In `scripts/main.py` nur diese drei Zeilen anpassen:

```python
LON_MIN, LAT_MIN = 8.62, 50.17  # Südwest-Ecke
LON_MAX, LAT_MAX = 8.75, 50.23  # Nordost-Ecke
```

Koordinaten für beliebige Gebiete in Raum Frankfurt


## Projektstruktur

```
solar-finder/
├── scripts/
│   └── main.py              # Hauptskript
├── notebooks/
│   └── solar_analyse.ipynb  # Jupyter Notebook
├── data/                    # Rohe Geodaten
└── output/
    ├── karte.html           # Interaktive Karte
    ├── top_flaechen.geojson
    └── top_flaechen.csv
```


# Datenquellen

| Quelle | Inhalt | 
|--------|--------|
| ALKIS Frankfurt | Flurstücke, Nutzungsarten |
| OpenStreetMap | Autobahnen, Bahnlinien |


## Technologien

- Python 3.11
- GeoPandas, Shapely, Rasterio
- Overpass API (OpenStreetMap)
- WFS (Web Feature Service)
- Folium (interaktive Karten)
- scikit-learn (Scoring)