# KURIER Klima-Monitor

Daten-Backend für den interaktiven KURIER Klima-Monitor auf kurier.at.

Das Repository bereitet Klima- und Wetterdaten von GeoSphere Austria für alle österreichischen Gemeinden auf und stellt sie als statische JSON-Dateien über GitHub Pages bereit.

Der Klima-Monitor umfasst derzeit drei Bereiche:

- Temperatur
- Niederschlag
- Schnee

Die Daten werden auf Gemeindeebene mit GeoSphere-Rasterdaten verknüpft. Historische Vergleichswerte, aktuelle Mess- bzw. Analysedaten und – bei der Temperatur – die Prognose werden getrennt erzeugt und vom Frontend zusammengeführt.

---

## Überblick

Die wichtigsten Daten liegen unter:

```text
docs/data/
```

Die drei fachlichen Bereiche sind:

```text
docs/data/temperature_grid/
docs/data/precipitation/
docs/data/snow/
```

Zusätzlich gibt es unter anderem:

```text
docs/data/forecast.json
docs/data/map_today.json
docs/data/austria_states.geojson
docs/data/municipalities.json
```

Die Daten werden über GitHub Pages öffentlich ausgeliefert:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/
```

---

# Gemeindenzuordnung und Raster

Der Klima-Monitor arbeitet für alle 2.092 österreichischen Gemeinden.

## Gemeinde-Referenzpunkte

Die grundlegende Gemeindeliste liegt unter:

```text
docs/data/municipalities.json
```

Als geografischer Referenzpunkt wird grundsätzlich der Bevölkerungsschwerpunkt der Gemeinde verwendet.

Für 2.091 Gemeinden stammen diese Punkte direkt aus dem Datensatz:

```text
Statistik Austria
Gemeinden – Schwerpunkte
STATISTIK_AUSTRIA_GEM_MP_20250101
Stand: 2025-01-01
```

Die Statistik-Austria-Schwerpunkte basieren auf dem Bevölkerungsschwerpunkt einer Gemeinde.

Für Wien gibt es in diesem Gemeindedatensatz keinen eigenen Punkt für die Gemeinde-ID `90001`, sondern 23 Punkte für die Wiener Gemeindebezirke.

Der Referenzpunkt für Wien wird deshalb separat aus den 23 offiziellen Bezirksschwerpunkten berechnet. Dabei werden die Bezirkspunkte mit der jeweiligen Bezirksbevölkerung zum 1. Jänner 2025 gewichtet.

Der daraus verwendete Wien-Referenzpunkt lautet:

```text
48.206301, 16.369308
```

Damit gilt grundsätzlich:

```text
Gemeinde
   ↓
Bevölkerungsschwerpunkt
   ↓
nächstgelegener GeoSphere-1-km-Rasterpunkt
```

Die Gemeindekoordinate selbst ist also nicht der Klima- oder Wetterwert.

Sie dient dazu, für den jeweiligen GeoSphere-Datensatz den nächstgelegenen Rasterpunkt zu bestimmen.

---

## SPARTACUS-Mapping

Die zentrale Rasterzuordnung für Temperatur und Niederschlag liegt unter:

```text
docs/data/precipitation/municipalities.json
```

Sie enthält unter anderem:

```text
id
name
state
state_slug
grid_id
grid_lat
grid_lon
```

Für jeden Gemeinde-Referenzpunkt wird der nächstgelegene Rasterpunkt aus GeoSphere SPARTACUS v3 verwendet.

Mehrere Gemeinden können demselben 1-km-Rasterpunkt zugeordnet sein. Daher gibt es weniger eindeutige Rasterpunkte als Gemeinden.

Aktuell werden für 2.092 Gemeinden insgesamt 2.089 unterschiedliche Rasterpunkte verwendet.

Temperatur und Niederschlag verwenden dieselben `grid_id`-Werte.

Auch die Temperaturprognose wird für dieselben Gemeinde-Rasterpunkte berechnet.

Für Wien ist der aktuell verwendete Rasterpunkt:

```text
grid_id:
g138202600_196368576

Koordinate:
48.202600, 16.368576
```

---

## SNOWGRID-Mapping

Schnee verwendet ein eigenes Mapping:

```text
docs/data/snow/municipalities.json
```

Das ist notwendig, weil SNOWGRID ein eigener GeoSphere-Datensatz mit eigenem Raster ist.

Auch hier wird vom Statistik-Austria-Bevölkerungsschwerpunkt der Gemeinde ausgegangen und anschließend der nächstgelegene SNOWGRID-Rasterpunkt bestimmt.

Aktuell werden auch im SNOWGRID-Mapping für 2.092 Gemeinden insgesamt 2.089 unterschiedliche Rasterpunkte verwendet.

Die Datei

```text
docs/data/municipalities.json
```

enthält somit die geografischen Gemeinde-Referenzpunkte.

Die Dateien

```text
docs/data/precipitation/municipalities.json
docs/data/snow/municipalities.json
```

enthalten dagegen die daraus abgeleiteten Zuordnungen zu den jeweiligen GeoSphere-Rastern.

---

# Temperatur

## Datenquelle

Historische und aktuelle abgeschlossene Tageswerte stammen aus:

```text
GeoSphere Austria
SPARTACUS v3
spartacus-v3-1d-1km
```

Verwendeter Parameter:

```text
TX
```

`TX` ist die tägliche Höchsttemperatur.

Die Daten liegen auf einem Raster mit ungefähr 1 km Auflösung.

---

## Historische Temperatur-Klimatologie

Skript:

```text
scripts/build_temperature_grid.py
```

Die historische Basis umfasst:

```text
1961-01-01 bis 2020-12-31
```

Es werden zwei Referenzperioden getrennt berechnet:

```text
1961–1990
1991–2020
```

Für jeden Kalendertag werden folgende Kennwerte berechnet:

```text
20. Perzentil
Median
80. Perzentil
```

Damit kann ein aktueller Temperaturwert beispielsweise als ungewöhnlich kalt, üblich oder ungewöhnlich warm eingeordnet werden.

Um die Statistik robuster zu machen, verwendet die Temperatur-Klimatologie ein saisonales Fenster von sieben Kalendertagen:

```text
Kalendertag ± 3 Tage
```

Für eine gültige Statistik werden mindestens 150 historische Werte verlangt.

Die Ergebnisse liegen nach Bundesland unter:

```text
docs/data/temperature_grid/climatology/
```

Beispiel:

```text
docs/data/temperature_grid/climatology/wien.json
```

Der Build-Status liegt unter:

```text
docs/data/temperature_grid/build_status.json
```

---

## Aktuelle Temperatur

Skript:

```text
scripts/update_temperature_grid.py
```

Auch die aktuellen abgeschlossenen Tage stammen aus SPARTACUS v3 und verwenden `TX`.

Die Daten liegen unter:

```text
docs/data/temperature_grid/current/
```

Beispiel:

```text
docs/data/temperature_grid/current/wien.json
```

Beim ersten Aufbau werden bis zu 120 Tage geladen.

Bei späteren Aktualisierungen werden die jüngsten 14 Tage erneut abgefragt. Dadurch können verspätete oder nachträglich korrigierte GeoSphere-Daten automatisch übernommen werden.

Der aktuelle lokale Kalendertag wird dabei nicht als abgeschlossener Beobachtungstag verwendet.

Wenn sich das Gemeinde-Rastermapping ändert, entfernt der Updater alte, nicht mehr benötigte Rasterpunkte automatisch aus den aktuellen Temperaturdateien.

---

# Temperaturprognose

Skript:

```text
scripts/update_forecast.py
```

Die Prognose stammt aus:

```text
GeoSphere Austria
NWP v2
nwp-v2-1h-1km
```

Verwendeter Parameter:

```text
2t
```

Das ist die stündliche Temperatur in 2 Metern Höhe.

Für jeden Rasterpunkt wird aus den Stundenwerten des aktuellen lokalen Tages die höchste Temperatur bestimmt.

Die Prognose wird damit als heutige erwartete Tageshöchsttemperatur verwendet.

Ausgabe:

```text
docs/data/forecast.json
```

Die Prognose verwendet dieselben Gemeinde-Rasterpunkte wie Temperatur und Niederschlag.

Dadurch können abgeschlossene SPARTACUS-Tage und der aktuelle Prognosetag direkt miteinander verbunden werden.

Eine bereits vorhandene Forecast-Datei wird nur wiederverwendet, wenn nicht nur die erwartete Anzahl der Rasterpunkte, sondern auch die konkreten `grid_id`-Werte dem aktuellen Gemeindemapping entsprechen.

Damit wird verhindert, dass nach einer Änderung der Gemeinde-Rasterzuordnung alte Forecast-Rasterpunkte weiterverwendet werden.

---

# Temperaturkarte

`scripts/update_forecast.py` erzeugt zusätzlich:

```text
docs/data/map_today.json
```

Diese Datei enthält die für eine Österreichkarte vorbereiteten Gemeindedaten des aktuellen Tages.

Unter anderem werden Forecast und Klimatologie miteinander verglichen und die Gemeinden entsprechend eingeordnet.

Die Datei enthält für alle erfolgreich verarbeiteten Gemeinden unter anderem:

```text
id
name
state
grid_id
lat
lon
forecast
p20
median
p80
anomaly
category
```

Zusätzlich wird die Bundesländer-Geometrie verwendet:

```text
docs/data/austria_states.geojson
```

Falls diese Datei fehlt, kann das Forecast-Skript sie aus der im Skript definierten GeoJSON-Quelle laden.

---

# Niederschlag

## Historische Daten und Klimatologie

Skript:

```text
scripts/build_precipitation.py
```

Primäre Datenquelle:

```text
GeoSphere Austria
SPARTACUS v3
spartacus-v3-1d-1km
```

Verwendeter Parameter:

```text
RR
```

Für den Klima-Monitor wird nicht nur der Niederschlag eines einzelnen Tages betrachtet, sondern die Niederschlagssumme der vergangenen 30 Kalendertage.

Die historische Datengrundlage umfasst:

```text
1961–2020
```

mit den Referenzperioden:

```text
1961–1990
1991–2020
```

Eine 30-Tage-Summe wird nur erzeugt, wenn für alle 30 Kalendertage Werte vorliegen.

Fehlende Tage werden nicht stillschweigend als `0` interpretiert.

Die Klimatologie liegt unter:

```text
docs/data/precipitation/climatology/
```

Beispiel:

```text
docs/data/precipitation/climatology/wien.json
```

Informationen über den letzten vollständigen Build liegen unter:

```text
docs/data/precipitation/build_status.json
```

Der Niederschlags-Build erzeugt außerdem die zentrale SPARTACUS-Rasterzuordnung:

```text
docs/data/precipitation/municipalities.json
```

Diese Rasterzuordnung wird auch von Temperatur und Temperaturprognose verwendet.

---

## Aktueller Niederschlag

Skript:

```text
scripts/update_precipitation.py
```

Die primäre Quelle bleibt SPARTACUS v3.

Aktuelle Daten liegen unter:

```text
docs/data/precipitation/current/
```

Der Updater lädt einen größeren Zeitraum erneut, damit nachträgliche Revisionen berücksichtigt werden können.

Dabei werden 150 Tage Quelldaten betrachtet und bis zu 120 Tage für die aktuelle Ausgabe gespeichert.

---

## INCA-Fallback

Da SPARTACUS-Tagesdaten mit zeitlichem Abstand veröffentlicht werden können, kann der Niederschlags-Updater für die jüngsten noch fehlenden vollständigen Tage zusätzlich INCA verwenden.

Fallback-Datensatz:

```text
GeoSphere Austria
INCA v2
inca-v2-1h-1km
```

Parameter:

```text
tp
```

Der INCA-Fallback ist auf höchstens drei Tage begrenzt.

Dabei gelten wichtige Regeln:

- SPARTACUS bleibt die primäre Datenquelle.
- Bereits vorhandene SPARTACUS-Werte werden niemals durch INCA überschrieben.
- INCA wird nur für vollständige lokale Kalendertage verwendet.
- Fehlende INCA-Stunden werden nicht als `0` interpretiert.
- Sobald SPARTACUS den entsprechenden Tag liefert, kann der provisorische Wert durch die Primärquelle ersetzt werden.

Der aktuelle Status der Niederschlagspipeline steht unter:

```text
docs/data/precipitation/status.json
```

---

## SPARTACUS-/INCA-Vergleichstest

Für die methodische Kontrolle des Übergangs zwischen beiden Quellen gibt es:

```text
scripts/test_inca_spartacus_precipitation.py
```

sowie den Workflow:

```text
.github/workflows/test_inca_spartacus_precipitation.yml
```

Damit können Zeiträume getestet werden, für die sowohl SPARTACUS als auch INCA verfügbar sind.

Der Test verändert keine Produktionsdaten.

---

# Schnee

## Datenquelle

Die Schneedaten stammen aus:

```text
GeoSphere Austria
SNOWGRID Klima v2.1
snowgrid_cl-v2-1d-1km
```

Verwendeter Parameter:

```text
snow_depth
```

Die von GeoSphere gelieferten Werte werden von Metern in Zentimeter umgerechnet.

Fehlende Werte bleiben fehlend und werden nicht als `0 cm` interpretiert.

Negative Schneehöhen werden ebenfalls als fehlende Werte behandelt.

---

## Gemeinde-Mapping für Schnee

Skript:

```text
scripts/build_snow_mapping.py
```

Da SNOWGRID ein eigenes Raster besitzt, wird für jede Gemeinde separat ein SNOWGRID-Rasterpunkt bestimmt.

Ausgangspunkt ist der Gemeinde-Bevölkerungsschwerpunkt aus:

```text
docs/data/municipalities.json
```

Für diesen Referenzpunkt wird der nächstgelegene von GeoSphere zurückgegebene SNOWGRID-Rasterpunkt verwendet.

Ausgabe:

```text
docs/data/snow/municipalities.json
```

---

## Historische Schnee-Klimatologie

Skript:

```text
scripts/build_snow_climatology.py
```

Historische Basis:

```text
1961–2020
```

Referenzperioden:

```text
1961–1990
1991–2020
```

Wie bei der Temperatur wird ein saisonales Fenster verwendet:

```text
Kalendertag ± 3 Tage
```

also insgesamt sieben Kalendertage.

Berechnet werden derzeit unter anderem:

```text
20. Perzentil
Median
80. Perzentil
Anteil schneefreier Tage
```

Der Anteil schneefreier Vergleichstage wird als `zero_share` gespeichert.

Damit kann zusätzlich zur Schneehöhe beurteilt werden, wie häufig an vergleichbaren historischen Tagen überhaupt Schnee lag.

Die Klimatologie liegt unter:

```text
docs/data/snow/climatology/
```

Beispiel:

```text
docs/data/snow/climatology/wien.json
```

Der Build-Status liegt unter:

```text
docs/data/snow/build_status.json
```

---

## Aktuelle Schneehöhe

Skript:

```text
scripts/update_snow.py
```

Die aktuellen Werte liegen unter:

```text
docs/data/snow/current/
```

Der Updater fragt die jüngsten 150 Tage erneut ab, damit nachträgliche Revisionen von SNOWGRID berücksichtigt werden.

Für das Frontend werden maximal 120 Tage gespeichert.

Der aktuelle lokale Kalendertag wird nicht als abgeschlossener Tageswert verwendet.

Es gibt keine alternative Fallback-Datenquelle.

Fehlende Werte werden nicht als `0 cm` interpretiert.

Status:

```text
docs/data/snow/status.json
```

---

# Datenstruktur

Vereinfacht sieht das Repository so aus:

```text
kurier-klimamonitor/
│
├── .github/
│   └── workflows/
│       ├── update.yml
│       ├── build_temperature_grid.yml
│       ├── forecast.yml
│       ├── build_precipitation.yml
│       ├── update_precipitation.yml
│       ├── build_snow_mapping.yml
│       ├── build_snow_climatology.yml
│       ├── update_snow.yml
│       └── test_inca_spartacus_precipitation.yml
│
├── scripts/
│   ├── build_temperature_grid.py
│   ├── update_temperature_grid.py
│   ├── update_forecast.py
│   ├── build_precipitation.py
│   ├── update_precipitation.py
│   ├── build_snow_mapping.py
│   ├── build_snow_climatology.py
│   ├── update_snow.py
│   └── test_inca_spartacus_precipitation.py
│
├── docs/
│   ├── index.html
│   └── data/
│       ├── municipalities.json
│       ├── forecast.json
│       ├── map_today.json
│       ├── austria_states.geojson
│       │
│       ├── temperature_grid/
│       │   ├── climatology/
│       │   ├── current/
│       │   └── build_status.json
│       │
│       ├── precipitation/
│       │   ├── municipalities.json
│       │   ├── climatology/
│       │   ├── current/
│       │   ├── build_status.json
│       │   └── status.json
│       │
│       └── snow/
│           ├── municipalities.json
│           ├── climatology/
│           ├── current/
│           ├── build_status.json
│           └── status.json
│
├── requirements.txt
└── README.md
```

---

# Skripte

| Skript | Aufgabe |
|---|---|
| `build_temperature_grid.py` | Erzeugt die historische Temperatur-Klimatologie |
| `update_temperature_grid.py` | Aktualisiert aktuelle abgeschlossene Temperaturtage |
| `update_forecast.py` | Erzeugt Temperaturprognose und Kartendaten |
| `build_precipitation.py` | Erzeugt SPARTACUS-Gemeindemapping und historische 30-Tage-Niederschlagsklimatologie |
| `update_precipitation.py` | Aktualisiert aktuelle 30-Tage-Niederschlagswerte |
| `build_snow_mapping.py` | Ordnet Gemeinde-Bevölkerungsschwerpunkte dem SNOWGRID-Raster zu |
| `build_snow_climatology.py` | Erzeugt die historische Schnee-Klimatologie |
| `update_snow.py` | Aktualisiert aktuelle Schneehöhen |
| `test_inca_spartacus_precipitation.py` | Vergleicht SPARTACUS und INCA methodisch |

---

# GitHub Actions

Alle derzeit im Repository definierten Fach-Workflows sind über `workflow_dispatch` manuell startbar.

## Temperatur-Klimatologie bauen

```text
.github/workflows/build_temperature_grid.yml
```

führt aus:

```text
scripts/build_temperature_grid.py
```

Dabei kann entweder ein einzelnes Bundesland oder ganz Österreich gebaut werden.

Der Workflow aktualisiert:

```text
docs/data/temperature_grid/climatology/
docs/data/temperature_grid/build_status.json
```

---

## Temperatur aktualisieren

```text
.github/workflows/update.yml
```

führt aus:

```text
scripts/update_temperature_grid.py
```

und committed:

```text
docs/data/temperature_grid/current/
```

---

## Temperaturprognose aktualisieren

```text
.github/workflows/forecast.yml
```

führt aus:

```text
scripts/update_forecast.py
```

und aktualisiert:

```text
docs/data/forecast.json
docs/data/map_today.json
docs/data/austria_states.geojson
```

---

## Niederschlag-Klimatologie bauen

```text
.github/workflows/build_precipitation.yml
```

führt aus:

```text
scripts/build_precipitation.py
```

Dabei kann entweder ein einzelnes Bundesland oder ganz Österreich gebaut werden.

Der Build aktualisiert auch die SPARTACUS-Rasterzuordnung der Gemeinden.

---

## Niederschlag aktualisieren

```text
.github/workflows/update_precipitation.yml
```

führt aus:

```text
scripts/update_precipitation.py
```

---

## Schnee-Mapping bauen

```text
.github/workflows/build_snow_mapping.yml
```

führt aus:

```text
scripts/build_snow_mapping.py
```

und erzeugt:

```text
docs/data/snow/municipalities.json
```

---

## Schnee-Klimatologie bauen

```text
.github/workflows/build_snow_climatology.yml
```

führt aus:

```text
scripts/build_snow_climatology.py
```

Dabei kann entweder ein einzelnes Bundesland oder ganz Österreich gebaut werden.

---

## Schnee aktualisieren

```text
.github/workflows/update_snow.yml
```

führt aus:

```text
scripts/update_snow.py
```

---

## Automatische Zeitsteuerung

Die Workflow-Dateien in diesem Repository enthalten derzeit keinen `schedule:`-Trigger.

Automatische Aufrufe, die beispielsweise über einen externen Cron-Dienst erfolgen, sind daher nicht Bestandteil dieses Repositories.

---

# Gemeinsames Datenmodell

Das zentrale Bindeglied zwischen Gemeinde und Klima-Raster ist die `grid_id`.

Vereinfacht:

```text
Gemeinde
   ↓
Statistik-Austria-Bevölkerungsschwerpunkt
   ↓
GeoSphere-Rasterpunkt
   ↓
grid_id
   ↓
aktueller Wert
historische Klimatologie
Prognose
```

Temperatur und Niederschlag verwenden dieselbe Rasterzuordnung aus:

```text
docs/data/precipitation/municipalities.json
```

Schnee besitzt wegen des eigenständigen SNOWGRID-Rasters eine separate Zuordnung.

Diese Architektur vermeidet die frühere Abhängigkeit von einzelnen Wetterstationen und ermöglicht eine flächendeckende Zuordnung aller österreichischen Gemeinden zu Rasterdaten.

---

# Referenzperioden und Einordnung

Für die historischen Vergleiche werden zwei Klimaperioden geführt:

```text
1961–1990
1991–2020
```

Für die aktuelle Darstellung wird primär `1991–2020` verwendet.

Typische Kennwerte sind:

```text
p20
Median
p80
```

Damit kann ein aktueller Wert relativ zur historischen Verteilung eingeordnet werden.

Beispielsweise bei Temperatur:

```text
Wert < p20  → ungewöhnlich kalt
p20–p80     → üblicher Bereich
Wert > p80  → ungewöhnlich warm
```

Für Niederschlag und Schnee wird dasselbe Grundprinzip fachlich an die jeweilige Messgröße angepasst.

Beim Schnee wird zusätzlich der historische Anteil schneefreier Vergleichstage berücksichtigt.

---

# Öffentliche Beispiele

Gemeinde-Referenzpunkte:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/municipalities.json
```

SPARTACUS-Gemeindemapping:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/precipitation/municipalities.json
```

SNOWGRID-Gemeindemapping:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/snow/municipalities.json
```

Temperatur aktuell:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/temperature_grid/current/wien.json
```

Temperatur-Klimatologie:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/temperature_grid/climatology/wien.json
```

Temperaturprognose:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/forecast.json
```

Temperaturkarte:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/map_today.json
```

Niederschlag aktuell:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/precipitation/current/wien.json
```

Niederschlag-Klimatologie:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/precipitation/climatology/wien.json
```

Schnee aktuell:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/snow/current/wien.json
```

Schnee-Klimatologie:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/data/snow/climatology/wien.json
```

---

# Abhängigkeiten

Die Python-Skripte verwenden derzeit:

```text
Python 3.12
requests
```

Die festgelegte Python-Abhängigkeit steht in:

```text
requirements.txt
```

Aktuell:

```text
requests==2.32.5
```

---

# GitHub Pages

Die Dateien im Ordner

```text
docs/
```

werden über GitHub Pages veröffentlicht.

Konfiguration:

```text
Repository
→ Settings
→ Pages
→ Deploy from a branch
→ main
→ /docs
```

Die öffentliche Startseite ist anschließend:

```text
https://ernstfelixm.github.io/kurier-klimamonitor/
```

---

# Datenquellen

## GeoSphere Austria

Die fachlichen Klima- und Wetterdaten stammen von GeoSphere Austria über die Dataset API:

```text
https://dataset.api.hub.geosphere.at/v1/
```

Verwendete Produkte sind derzeit insbesondere:

```text
SPARTACUS v3 daily 1 km
NWP v2 hourly 1 km
INCA v2 hourly 1 km
SNOWGRID Klima v2.1 daily 1 km
```

## Statistik Austria

Die geografischen Gemeinde-Referenzpunkte stammen aus:

```text
Statistik Austria
Gemeinden – Schwerpunkte
STATISTIK_AUSTRIA_GEM_MP_20250101
Stand: 2025-01-01
```

Für Wien wird der Gemeinde-Referenzpunkt aus den 23 Bezirksschwerpunkten abgeleitet und mit der Bezirksbevölkerung zum 1. Jänner 2025 gewichtet.

---

# Grundprinzip

Das Repository trennt bewusst zwischen:

```text
Gemeinde-Referenzpunkt
        ↓
GeoSphere-Rasterzuordnung
        ↓
historischer Klimatologie
        +
aktuellen abgeschlossenen Tagesdaten
        +
aktueller Prognose
```

Die großen GeoSphere-Rohdatensätze werden serverseitig über die Python-Skripte aufbereitet.

Das Frontend muss deshalb nicht selbst große GeoSphere-Datensätze laden oder Rasterpunkte bestimmen.

So kann der KURIER Klima-Monitor für jede österreichische Gemeinde aktuelle Wetter- bzw. Klimawerte mit historischen Vergleichsperioden einordnen.
