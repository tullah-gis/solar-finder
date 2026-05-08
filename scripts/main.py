# scripts/main.py
# Solarpark Flächensuche - Hauptskript
# Lädt ALKIS Flurstücke und OSM Infrastrukturdaten,
# filtert geeignete Flächen und exportiert Ergebnisse

import requests
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString
from shapely.ops import unary_union
from sklearn.preprocessing import MinMaxScaler
from pyproj import Transformer
import folium
import time
import io
import os

# ─────────────────────────────────────────
# KONFIGURATION
# ─────────────────────────────────────────

# Testgebiet: Frankfurt Nord
LON_MIN, LAT_MIN = 8.62, 50.17
LON_MAX, LAT_MAX = 8.75, 50.23

# Für Overpass API (lat,lon)
BBOX_OSM = f"{LAT_MIN},{LON_MIN},{LAT_MAX},{LON_MAX}"

# Für WFS (UTM)
transformer = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
X_MIN, Y_MIN = transformer.transform(LON_MIN, LAT_MIN)
X_MAX, Y_MAX = transformer.transform(LON_MAX, LAT_MAX)
BBOX_UTM = f"{X_MIN:.0f},{Y_MIN:.0f},{X_MAX:.0f},{Y_MAX:.0f}"

# Kartenmittelpunkt
KARTE_MITTE = [(LAT_MIN + LAT_MAX) / 2, (LON_MIN + LON_MAX) / 2]

# Geeignete Nutzungsarten
GEEIGNETE_NUTZUNG = ["Ackerland", "Grünland", "Streuobst"]

# API URLs
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
WFS_URL      = "https://geowebdienste.frankfurt.de/SGK_Flurstuecke"
HEADERS      = {"User-Agent": "solar-finder/1.0"}

# Output-Ordner
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "../output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─────────────────────────────────────────
# FUNKTIONEN
# ─────────────────────────────────────────

def lade_osm(filter_tag, beschreibung, versuche=3):
    """Lädt OSM-Linien und gibt GeoDataFrame zurück"""
    query = f"""
        [out:json];
        way[{filter_tag}]({BBOX_OSM});
        out geom;
    """
    response = None
    for i in range(versuche):
        try:
            r = requests.get(
                OVERPASS_URL,
                params={"data": query},
                headers=HEADERS,
                timeout=30
            )
            # Leere Antwort abfangen
            if not r.text.strip():
                print(f"Versuch {i+1}: Leere Antwort, warte 5 Sekunden...")
                time.sleep(5)
                continue

            # Prüfen ob JSON parsebar
            r.json()
            response = r
            break

        except Exception as e:
            print(f"Versuch {i+1} fehlgeschlagen: {e}, warte 5 Sekunden...")
            time.sleep(5)

    if response is None:
        print(f"FEHLER: {beschreibung} konnte nicht geladen werden")
        return gpd.GeoDataFrame(columns=["id", "typ", "geometry"])

    data = response.json()
    rows = []
    for element in data['elements']:
        if 'geometry' not in element:
            continue
        coords = [(p['lon'], p['lat']) for p in element['geometry']]
        if len(coords) >= 2:
            rows.append({
                "id": element['id'],
                "typ": beschreibung,
                "geometry": LineString(coords)
            })

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    print(f"{beschreibung}: {len(gdf)} Abschnitte geladen")
    return gdf


def lade_alkis():
    """Lädt ALKIS Flurstücke vom WFS und filtert geeignete Flächen"""
    print("Lade ALKIS Flurstücke vom WFS...")

    response = requests.get(
        WFS_URL,
        params={
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "Amt62_Flurstuecke:Flurstueck",
            "BBOX": f"{BBOX_UTM},EPSG:25832",
            "SRSNAME": "EPSG:25832",
        },
        timeout=120
    )

    print(f"WFS Status: {response.status_code}")

    alle = gpd.read_file(io.BytesIO(response.content))
    alle = alle.set_crs("EPSG:25832", allow_override=True)
    alle["area_ha"] = alle.geometry.area / 10000

    print(f"Flurstücke geladen: {len(alle)}")

    # Lokal filtern: Nutzungsart + Größe > 7 ha
    maske = (
        alle["TNTXT"].str.contains(
            "|".join(GEEIGNETE_NUTZUNG),
            case=False, na=False
        )
        & (alle["area_ha"] > 7)
    )

    flaechen = alle[maske].copy()
    print(f"Geeignete Flächen (>7ha, richtige Nutzung): {len(flaechen)}")
    return flaechen


def berechne_scoring(flaechen, autobahnen, bahnlinien):
    # DEBUG
    print(f"Flächen CRS: {flaechen.crs}")
    print(f"Autobahnen CRS: {autobahnen.crs}")
    print(f"Anzahl Flächen vor Filter: {len(flaechen)}")

    if len(flaechen) == 0:
        print("FEHLER: Keine geeigneten Flächen gefunden.")
        return flaechen

    # Infrastruktur auf UTM umstellen
    autobahnen_m = autobahnen.to_crs(epsg=25832)
    bahnlinien_m = bahnlinien.to_crs(epsg=25832)

    # Flächen auch auf UTM umstellen falls nötig
    if flaechen.crs.to_epsg() != 25832:
        print("Konvertiere Flächen zu UTM...")
        flaechen = flaechen.to_crs(epsg=25832)

    # Prüfen ob Flächen vorhanden
    if len(flaechen) == 0:
        print("FEHLER: Keine geeigneten Flächen gefunden.")
        print("Mögliche Ursachen:")
        print("  - Gebiet hat kein Ackerland/Grünland")
        print("  - Alle Flächen kleiner als 7 ha")
        print("  - Falsches Testgebiet")
        return flaechen

    """Berechnet Abstände und Score für jede Fläche"""

    # Infrastruktur auf UTM umstellen
    autobahnen_m = autobahnen.to_crs(epsg=25832)
    bahnlinien_m = bahnlinien.to_crs(epsg=25832)

    autobahn_union = unary_union(autobahnen_m.geometry)
    bahn_union     = unary_union(bahnlinien_m.geometry)

    # Abstände berechnen
    flaechen["dist_autobahn_m"] = flaechen.geometry.apply(
        lambda g: g.distance(autobahn_union)
    )
    flaechen["dist_bahn_m"] = flaechen.geometry.apply(
        lambda g: g.distance(bahn_union)
    )

    # Filter: Nähe zur Infrastruktur
    flaechen = flaechen[
        (flaechen["dist_autobahn_m"] < 2000) |
        (flaechen["dist_bahn_m"] < 2000)
    ].copy()

    print(f"Nach Infrastruktur-Filter (<2km): {len(flaechen)} Flächen")

    # Scoring
    flaechen["score_flaeche"] = flaechen["area_ha"]
    flaechen["score_infra"]   = 1 / (flaechen["dist_autobahn_m"] + 1)


    scaler = MinMaxScaler()
    flaechen[["score_flaeche", "score_infra"]] = scaler.fit_transform(
        flaechen[["score_flaeche", "score_infra"]]
    )

    # Gesamtscore: 40% Fläche, 60% Infrastruktur
    flaechen["score_gesamt"] = (
        flaechen["score_flaeche"] * 0.4 +
        flaechen["score_infra"]   * 0.6
    )

    return flaechen.sort_values("score_gesamt", ascending=False)


def farbe_nach_score(score):
    if score >= 0.66:
        return "green"
    elif score >= 0.33:
        return "orange"
    else:
        return "red"


def kategorie_nach_score(score):
    if score >= 0.66:
        return "Gut geeignet"
    elif score >= 0.33:
        return "Mittel geeignet"
    else:
        return "Wenig geeignet"


def berechne_pvgis(flaechen):
    """
    Berechnet Sonneneinstrahlung für den Mittelpunkt jeder Fläche.
    Außerdem: Energiepotenzial und CO₂-Einsparung.
    """
    print("Lade Sonneneinstrahlung von PVGIS...")

    ertraege = []
    einstrahlungen = []

    for idx, row in flaechen.iterrows():
        # Mittelpunkt der Fläche berechnen
        mittelpunkt = row.geometry.centroid
        lat = mittelpunkt.y
        lon = mittelpunkt.x

        try:
            response = requests.get(
                "https://re.jrc.ec.europa.eu/api/v5_2/PVcalc",
                params={
                    "lat": lat,
                    "lon": lon,
                    "peakpower": 1,
                    "loss": 14,
                    "outputformat": "json"
                },
                timeout=30
            )
            data = response.json()
            ertrag       = data["outputs"]["totals"]["fixed"]["E_y"]
            einstrahlung = data["outputs"]["totals"]["fixed"]["H(i)_y"]

        except Exception as e:
            print(f"  PVGIS Fehler für Fläche {idx}: {e}")
            ertrag       = None
            einstrahlung = None

        ertraege.append(ertrag)
        einstrahlungen.append(einstrahlung)
        print(f"  Fläche {idx}: {einstrahlung} kWh/m²")
        time.sleep(1)  # Pause damit PVGIS nicht überlastet wird

    flaechen["solar_kwh_m2"]     = einstrahlungen
    flaechen["solar_ertrag_kwp"] = ertraege

    # Energiepotenzial: 150 Wp/m², 80% Flächennutzung
    flaechen["energie_mwh_jahr"] = (
        flaechen["area_ha"] * 10000
        * 0.80
        * 0.15
        * flaechen["solar_ertrag_kwp"]
        / 1000
    )

    # CO₂-Einsparung: 400g CO₂ pro kWh Netzstrom
    flaechen["co2_t_jahr"] = flaechen["energie_mwh_jahr"] * 1000 * 0.4 / 1000

    print(f"\nPVGIS abgeschlossen:")
    print(flaechen[["area_ha", "solar_kwh_m2", "energie_mwh_jahr", "co2_t_jahr"]].round(1))
    return flaechen


def berechne_solar_score(flaechen):
    """Score um Solar-Komponente erweitern nach PVGIS"""

    scaler = MinMaxScaler()
    flaechen[["score_solar"]] = scaler.fit_transform(
        flaechen[["solar_kwh_m2"]]
    )

    # Score neu berechnen: 30% Fläche, 40% Infrastruktur, 30% Solar
    flaechen["score_gesamt"] = (
        flaechen["score_flaeche"] * 0.3 +
        flaechen["score_infra"]   * 0.4 +
        flaechen["score_solar"]   * 0.3
    )

    return flaechen.sort_values("score_gesamt", ascending=False)


def erstelle_karte(flaechen, autobahnen, bahnlinien):
    """Erstellt Folium-Karte und speichert als HTML"""

    flaechen_wgs = flaechen.to_crs(epsg=4326)
    m = folium.Map(
    location=KARTE_MITTE,
    zoom_start=13,
    tiles="CartoDB positron"
    )

    # Autobahnen lila
    for _, row in autobahnen.iterrows():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        folium.PolyLine(
            coords, color="purple", weight=3,
            opacity=0.7, tooltip="Autobahn"
        ).add_to(m)

    # Bahnlinien blau
    for _, row in bahnlinien.iterrows():
        coords = [(lat, lon) for lon, lat in row.geometry.coords]
        folium.PolyLine(
            coords, color="blue", weight=2,
            opacity=0.5, tooltip="Bahnlinie"
        ).add_to(m)

    # Flächen nach Score einfärben
    for _, row in flaechen_wgs.iterrows():
        farbe = farbe_nach_score(row.score_gesamt)
        popup_text = folium.Popup(
            f"<b>{kategorie_nach_score(row.score_gesamt)}</b><br>"
            f"Nutzung: {row.TNTXT[:50]}...<br>"
            f"Fläche: {row.area_ha:.1f} ha<br>"
            f"Abstand Autobahn: {row.dist_autobahn_m:.0f} m<br>"
            f"Sonneneinstrahlung: {row.solar_kwh_m2:.0f} kWh/m²<br>"
            f"Energiepotenzial: {row.energie_mwh_jahr:.0f} MWh/Jahr<br>"
            f"CO₂-Einsparung: {row.co2_t_jahr:.0f} t/Jahr<br>"
            f"Score: {row.score_gesamt:.3f}",
            max_width=250
        )

        geom = row.geometry
        teile = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]

        for teil in teile:
            coords = [(lat, lon) for lon, lat in teil.exterior.coords]
            folium.Polygon(
                locations=coords,
                color=farbe,
                fill=True,
                fill_opacity=0.5,
                popup=popup_text
            ).add_to(m)

    # Legende
    legende_html = """
    <div style="
        position: fixed; bottom: 30px; right: 30px; z-index: 1000;
        background-color: white; padding: 15px; border-radius: 8px;
        border: 1px solid #ccc; font-family: Arial, sans-serif;
        font-size: 13px; box-shadow: 2px 2px 6px rgba(0,0,0,0.2);">
        <b style="font-size:14px;">🗺️ Legende</b><br><br>
        <span style="display:inline-block; width:30px; height:4px;
            background:purple; margin-right:8px; vertical-align:middle;"></span>
        Autobahn<br><br>
        <span style="display:inline-block; width:30px; height:4px;
            background:blue; margin-right:8px; vertical-align:middle;"></span>
        Bahnlinie<br><br>
        <hr style="margin:8px 0;">
        <b>Flächenbewertung:</b><br><br>
        <span style="display:inline-block; width:16px; height:16px;
            background:green; opacity:0.6; margin-right:8px;
            vertical-align:middle; border:1px solid green;"></span>
        Gut geeignet (Score &gt; 0.66)<br><br>
        <span style="display:inline-block; width:16px; height:16px;
            background:orange; opacity:0.6; margin-right:8px;
            vertical-align:middle; border:1px solid orange;"></span>
        Mittel geeignet (Score 0.33–0.66)<br><br>
        <span style="display:inline-block; width:16px; height:16px;
            background:red; opacity:0.6; margin-right:8px;
            vertical-align:middle; border:1px solid red;"></span>
        Wenig geeignet (Score &lt; 0.33)<br><br>
        <hr style="margin:8px 0;">
        <b>Scoring-Gewichtung:</b><br>
        • 30% Flächengröße<br>
        • 40% Nähe Infrastruktur<br>
        • 30% Sonneneinstrahlung<br><br>
        <hr style="margin:8px 0;">
        <b>Datenquellen:</b><br>
        • ALKIS Frankfurt (Flurstücke)<br>
        • OpenStreetMap (Infrastruktur)<br>
        • PVGIS (Sonneneinstrahlung)<br><br>
        <b>Filterkriterien:</b><br>
        • Fläche &gt; 7 ha<br>
        • &lt; 2km von Autobahn/Bahn<br>
        • Nutzung: Acker, Grünland
    </div>
    """
    m.get_root().html.add_child(folium.Element(legende_html))
    return m


def exportiere(flaechen, karte):
    """Exportiert Ergebnisse als GeoJSON, CSV und HTML"""

    flaechen_export = flaechen.to_crs(epsg=4326)

    # GeoJSON
    geojson_path = os.path.join(OUTPUT_DIR, "top_flaechen.geojson")
    flaechen_export.to_file(geojson_path, driver="GeoJSON")

    # CSV
    csv_path = os.path.join(OUTPUT_DIR, "top_flaechen.csv")
    flaechen_export.drop(columns="geometry").to_csv(csv_path, index=False)

    # Karte
    karte_path = os.path.join(OUTPUT_DIR, "karte.html")
    karte.save(karte_path)

    print(f"\nExport abgeschlossen:")
    print(f"  {len(flaechen_export)} Flächen exportiert")
    print(f"  {geojson_path}")
    print(f"  {csv_path}")
    print(f"  {karte_path}")


# ─────────────────────────────────────────
# HAUPTPROGRAMM
# ─────────────────────────────────────────

if __name__ == "__main__":
    autobahnen = lade_osm('"highway"="motorway"', "Autobahnen")
    bahnlinien = lade_osm('"railway"="rail"',     "Bahnlinien")
    flaechen   = lade_alkis()

    # 1. PVGIS zuerst
    flaechen_wgs = flaechen.to_crs(epsg=4326)
    flaechen_wgs = berechne_pvgis(flaechen_wgs)

    # 2. Basis-Scoring
    flaechen = berechne_scoring(flaechen_wgs, autobahnen, bahnlinien)

    # 3. Solar-Score einberechnen
    flaechen = berechne_solar_score(flaechen)

    karte = erstelle_karte(flaechen, autobahnen, bahnlinien)
    exportiere(flaechen, karte)

    print("\nTop 5 Flächen:")
    print(flaechen[["TNTXT", "area_ha", "dist_autobahn_m",
                    "solar_kwh_m2", "energie_mwh_jahr", "score_gesamt"]].head(5).round(2))
    print("\nFertig!")