# KURIER Klima-Monitor

Daten-Backend für den interaktiven Klima-Monitor auf kurier.at/Shorthand.

## Öffentliche URLs nach Aktivierung von GitHub Pages

- `https://ernstfelixm.github.io/kurier-klimamonitor/data/stations.json`
- `https://ernstfelixm.github.io/kurier-klimamonitor/data/status.json`
- `https://ernstfelixm.github.io/kurier-klimamonitor/data/climatology/wien.json`
- `https://ernstfelixm.github.io/kurier-klimamonitor/data/current/wien.json`

Die Dateien sind nach Bundesland aufgeteilt, damit Shorthand nur die benötigten Daten lädt.

## Methodik Temperatur

- Messgröße: tägliche Höchsttemperatur (`tlmax`)
- historische Referenzperioden: 1961–1990 und 1991–2020
- Normalbereich: 20. bis 80. Perzentil
- Median: 50. Perzentil
- saisonales Vergleichsfenster: jeweiliger Kalendertag ± 7 Tage
- Quelle: GeoSphere Austria, Datensatz `klima-v2-1d`

## Automatische Aktualisierung

`.github/workflows/update.yml` läuft einmal täglich um 09:15 UTC und kann zusätzlich unter **Actions → Update GeoSphere temperature data → Run workflow** manuell gestartet werden.

Das Skript lädt jeweils die letzten 10 Tage erneut. Dadurch werden auch verspätete oder nachträglich korrigierte GeoSphere-Werte übernommen. Es ändert nur `docs/data/current/*.json` und `docs/data/status.json`.

## GitHub Pages aktivieren

Repository → **Settings → Pages** → **Deploy from a branch** → Branch `main` → Ordner `/docs` → Save.

Danach ist die Datenseite unter `https://ernstfelixm.github.io/kurier-klimamonitor/` erreichbar.

## Datenstruktur

`docs/data/stations.json` enthält Stationen, Koordinaten, Höhe und Abdeckungswerte.

`docs/data/climatology/<bundesland>.json` enthält für jede Station und jeden Kalendertag p20/Median/p80 für beide Referenzperioden.

`docs/data/current/<bundesland>.json` enthält die Tageshöchsttemperaturen des laufenden Jahres.

## Quellen

GeoSphere Austria Dataset API: https://dataset.api.hub.geosphere.at/v1/
