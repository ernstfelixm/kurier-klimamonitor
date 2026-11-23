# KURIER Klima-Monitor

Daten-Backend für den interaktiven Klima-Monitor auf kurier.at.

## Öffentliche Daten

Temperatur:

- `https://ernstfelixm.github.io/kurier-klimamonitor/data/temperature_grid/current/wien.json`
- `https://ernstfelixm.github.io/kurier-klimamonitor/data/temperature_grid/climatology/wien.json`
- `https://ernstfelixm.github.io/kurier-klimamonitor/data/forecast.json`

Gemeindenzuordnung:

- `https://ernstfelixm.github.io/kurier-klimamonitor/data/precipitation/municipalities.json`

Die Rasterdaten sind nach Bundesland aufgeteilt, damit Shorthand nur die jeweils benötigten Daten lädt.

## Methodik Temperatur

- Messgröße: tägliche Höchsttemperatur
- historische Daten: GeoSphere Austria SPARTACUS v3, 1-km-Raster
- Parameter historische Tageshöchsttemperatur: `TX`
- Referenzperioden: 1961–1990 und 1991–2020
- Normalbereich: 20. bis 80. Perzentil
- Median: 50. Perzentil
- saisonales Vergleichsfenster: jeweiliger Kalendertag ± 3 Tage
- aktuelle abgeschlossene Tage: SPARTACUS v3
- heutige Prognose: GeoSphere NWP v2, 1-km-Raster
- Prognosewert: Maximum der stündlichen 2-m-Temperatur des aktuellen Tages

Alle Temperaturdaten werden über dieselbe gemeindebasierte Rasterzuordnung verknüpft.

## Automatische Aktualisierung

`.github/workflows/update.yml` aktualisiert die aktuellen SPARTACUS-Temperaturdaten.

Das Skript lädt bei regulären Aktualisierungen die letzten 14 Tage erneut. Dadurch können verspätete oder nachträglich korrigierte GeoSphere-Werte übernommen werden.

Die aktuellen Temperaturdaten liegen unter:

`docs/data/temperature_grid/current/<bundesland>.json`

Die historische Klimatologie liegt unter:

`docs/data/temperature_grid/climatology/<bundesland>.json`

Die Temperaturprognose wird über `.github/workflows/forecast.yml` erzeugt und liegt unter:

`docs/data/forecast.json`

## GitHub Pages

Repository → **Settings → Pages** → **Deploy from a branch** → Branch `main` → Ordner `/docs` → Save.

Danach ist die Datenseite unter

`https://ernstfelixm.github.io/kurier-klimamonitor/`

erreichbar.

## Weitere Datensätze

Niederschlag:

`docs/data/precipitation/`

Schnee:

`docs/data/snow/`

## Quellen

GeoSphere Austria Dataset API:

`https://dataset.api.hub.geosphere.at/v1/`
