from __future__ import annotations

from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import csv
import io
import json
import math
import time

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
SNOW = DATA / "snow"

API = (
    "https://dataset.api.hub.geosphere.at/v1/timeseries/historical/"
    "snowgrid_cl-v2-1d-1km"
)

PARAMETER = "snow_depth"

VIENNA = ZoneInfo("Europe/Vienna")

# Wir laden etwas mehr als wir ausgeben.
# Dadurch können nachträgliche Revisionen von SNOWGRID
# bei jedem Lauf automatisch übernommen werden.
SOURCE_LOOKBACK_DAYS = 150

# Für das Frontend reichen 120 Tage.
CURRENT_OUTPUT_DAYS = 120

# Aktuelle Tagesdaten sind wesentlich kleiner als der
# 60-Jahres-Historienabruf. 120 Punkte pro Request sind
# hier problemlos handhabbar.
COORD_BATCH = 120

TIMEOUT = 180
RETRIES = 6

# Kleine Pause zwischen erfolgreichen Requests.
REQUEST_PAUSE_SECONDS = 1


def load_json(path: Path):
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def save_json(path: Path, obj):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def chunks(items, n):
    for i in range(
        0,
        len(items),
        n,
    ):
        yield items[i:i + n]


def parse_float(value):
    if value in (
        None,
        "",
        "nan",
        "NaN",
        "null",
        "None",
    ):
        return None

    try:
        x = float(
            str(value).replace(
                ",",
                ".",
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    return (
        x
        if math.isfinite(x)
        else None
    )


def find_column(
    fieldnames,
    exact=(),
    contains=(),
):
    fields = list(
        fieldnames or []
    )

    low = {
        str(name).lower(): name
        for name in fields
        if name is not None
    }

    for key in exact:
        if key.lower() in low:
            return low[key.lower()]

    for name in fields:
        lname = str(name).lower()

        if any(
            token.lower() in lname
            for token in contains
        ):
            return name

    return None


def now_vienna():
    return datetime.now(
        timezone.utc
    ).astimezone(
        VIENNA
    )


def request_csv(
    coords,
    start: date,
    end: date,
):
    params = [
        (
            "parameters",
            PARAMETER,
        ),
        (
            "start",
            start.isoformat(),
        ),
        (
            "end",
            end.isoformat(),
        ),
        (
            "output_format",
            "csv",
        ),
    ]

    params += [
        (
            "lat_lon",
            f"{lat:.6f},{lon:.6f}",
        )
        for lat, lon in coords
    ]

    last_error = None

    for attempt in range(
        RETRIES
    ):
        try:
            response = requests.get(
                API,
                params=params,
                timeout=TIMEOUT,
            )

            response.raise_for_status()

            print(
                f"SNOWGRID: "
                f"{len(coords)} Rasterpunkte, "
                f"{start} bis {end}, "
                f"datapoint-count="
                f"{response.headers.get('datapoint-count')}"
            )

            return response.text

        except Exception as exc:
            last_error = exc

            if attempt + 1 < RETRIES:
                wait = (
                    5
                    *
                    (2 ** attempt)
                )

                print(
                    "Request fehlgeschlagen. "
                    f"Neuer Versuch in {wait}s: "
                    f"{exc}"
                )

                time.sleep(
                    wait
                )

    raise RuntimeError(
        "GeoSphere request failed: "
        f"{last_error}"
    )


def parse_rows(text):
    reader = csv.DictReader(
        io.StringIO(text)
    )

    fields = (
        reader.fieldnames
        or []
    )

    time_col = find_column(
        fields,
        exact=(
            "time",
            "date",
            "timestamp",
        ),
        contains=(
            "time",
            "date",
        ),
    )

    lat_col = find_column(
        fields,
        exact=(
            "lat",
            "latitude",
        ),
        contains=(
            "latitude",
            "lat",
        ),
    )

    lon_col = find_column(
        fields,
        exact=(
            "lon",
            "longitude",
        ),
        contains=(
            "longitude",
            "lon",
        ),
    )

    snow_col = find_column(
        fields,
        exact=(
            "snow_depth",
        ),
        contains=(
            "snow_depth",
            "snow depth",
        ),
    )

    if not all(
        (
            time_col,
            lat_col,
            lon_col,
            snow_col,
        )
    ):
        raise RuntimeError(
            "Unerwartete SNOWGRID-CSV-Spalten: "
            f"{fields}"
        )

    rows = []

    for row in reader:
        raw_time = row.get(
            time_col
        )

        lat = parse_float(
            row.get(lat_col)
        )

        lon = parse_float(
            row.get(lon_col)
        )

        snow_m = parse_float(
            row.get(snow_col)
        )

        if (
            not raw_time
            or lat is None
            or lon is None
        ):
            continue

        # Fehlende Werte bleiben fehlend.
        # Sie werden niemals als 0 cm interpretiert.
        if (
            snow_m is not None
            and snow_m < 0
        ):
            snow_m = None

        snow_cm = (
            snow_m * 100.0
            if snow_m is not None
            else None
        )

        rows.append(
            (
                str(raw_time)[:10],
                lat,
                lon,
                snow_cm,
            )
        )

    return rows


def nearest_coord(
    lat,
    lon,
    coords,
):
    if not coords:
        raise RuntimeError(
            "Keine Rasterkoordinaten "
            "für Zuordnung vorhanden."
        )

    return min(
        coords,
        key=lambda p:
            (
                (p[0] - lat) ** 2
                +
                (p[1] - lon) ** 2
            ),
    )


def coord_key(
    lat,
    lon,
):
    return (
        round(
            float(lat),
            6,
        ),
        round(
            float(lon),
            6,
        ),
    )


def load_mapping():
    path = (
        SNOW
        /
        "municipalities.json"
    )

    if not path.exists():
        raise RuntimeError(
            "docs/data/snow/municipalities.json "
            "fehlt. Zuerst "
            "scripts/build_snow_mapping.py "
            "ausführen."
        )

    obj = load_json(
        path
    )

    cols = obj.get(
        "columns",
        []
    )

    required = {
        "id",
        "name",
        "state",
        "state_slug",
        "grid_id",
        "grid_lat",
        "grid_lon",
    }

    missing = (
        required
        -
        set(cols)
    )

    if missing:
        raise RuntimeError(
            "Fehlende Spalten in "
            "snow/municipalities.json: "
            + ", ".join(
                sorted(missing)
            )
        )

    ix = {
        name: i
        for i, name
        in enumerate(cols)
    }

    rows = []

    for row in obj.get(
        "municipalities",
        []
    ):
        rows.append(
            {
                "id":
                    str(
                        row[ix["id"]]
                    ),
                "name":
                    row[ix["name"]],
                "state":
                    row[ix["state"]],
                "state_slug":
                    row[ix["state_slug"]],
                "grid_id":
                    row[ix["grid_id"]],
                "grid_lat":
                    float(
                        row[ix["grid_lat"]]
                    ),
                "grid_lon":
                    float(
                        row[ix["grid_lon"]]
                    ),
            }
        )

    return rows


def find_latest_valid_day(
    values,
    end_day,
):
    valid = []

    for raw_day, value in values.items():

        if value is None:
            continue

        try:
            d = date.fromisoformat(
                raw_day
            )
        except ValueError:
            continue

        if d <= end_day:
            valid.append(
                d
            )

    return (
        max(valid)
        if valid
        else None
    )


def main():
    print(
        "Lade SNOWGRID-"
        "Gemeindezuordnung ..."
    )

    mapping = load_mapping()

    print(
        f"{len(mapping)} Gemeinden "
        "geladen."
    )

    # ---------------------------------------------------------
    # Eindeutige Rasterpunkte
    # ---------------------------------------------------------

    unique = {}

    for row in mapping:
        unique[
            row["grid_id"]
        ] = {
            "grid_id":
                row["grid_id"],
            "lat":
                row["grid_lat"],
            "lon":
                row["grid_lon"],
        }

    grid_records = [
        unique[k]
        for k in sorted(unique)
    ]

    print(
        f"{len(grid_records)} "
        "eindeutige Rasterpunkte."
    )

    # ---------------------------------------------------------
    # Zeitfenster
    # ---------------------------------------------------------

    local_now = now_vienna()

    # Der heutige lokale Kalendertag wird nie verwendet.
    target_end = (
        local_now.date()
        -
        timedelta(days=1)
    )

    source_start = (
        target_end
        -
        timedelta(
            days=SOURCE_LOOKBACK_DAYS - 1
        )
    )

    print(
        "Zielzeitraum: "
        f"{source_start} bis "
        f"{target_end}"
    )

    # ---------------------------------------------------------
    # Datencontainer
    # ---------------------------------------------------------

    daily = {
        g["grid_id"]: {}
        for g in grid_records
    }

    # ---------------------------------------------------------
    # SNOWGRID laden
    # ---------------------------------------------------------

    total_batches = math.ceil(
        len(grid_records)
        /
        COORD_BATCH
    )

    for batch_no, batch in enumerate(
        chunks(
            grid_records,
            COORD_BATCH,
        ),
        start=1,
    ):
        print()
        print(
            f"Aktueller Schnee "
            f"Batch {batch_no}/"
            f"{total_batches}: "
            f"{len(batch)} Rasterpunkte"
        )

        coords = [
            (
                g["lat"],
                g["lon"],
            )
            for g in batch
        ]

        # Verknüpft die ursprünglich angefragte
        # Rasterkoordinate mit unserer grid_id.
        coord_to_gid = {
            coord_key(
                g["lat"],
                g["lon"],
            ):
                g["grid_id"]
            for g in batch
        }

        text = request_csv(
            coords,
            source_start,
            target_end,
        )

        rows = parse_rows(
            text
        )

        for (
            day,
            lat,
            lon,
            snow_cm,
        ) in rows:

            requested_coord = nearest_coord(
                lat,
                lon,
                coords,
            )

            gid = coord_to_gid.get(
                coord_key(
                    *requested_coord
                )
            )

            if gid is None:
                continue

            # Fehlende Werte bleiben None.
            # Damit können wir sie später sauber verwerfen.
            daily[
                gid
            ][
                day
            ] = (
                snow_cm
            )

        if (
            batch_no
            <
            total_batches
        ):
            time.sleep(
                REQUEST_PAUSE_SECONDS
            )

    # ---------------------------------------------------------
    # Datenverfügbarkeit prüfen
    # ---------------------------------------------------------

    latest_by_grid = {}

    for grid in grid_records:
        gid = grid[
            "grid_id"
        ]

        latest = (
            find_latest_valid_day(
                daily[gid],
                target_end,
            )
        )

        if latest is not None:
            latest_by_grid[
                gid
            ] = latest

    if not latest_by_grid:
        raise RuntimeError(
            "Keine gültigen aktuellen "
            "SNOWGRID-Schneehöhen gefunden."
        )

    latest_global = max(
        latest_by_grid.values()
    )

    earliest_latest = min(
        latest_by_grid.values()
    )

    print()
    print(
        "SNOWGRID neuester verfügbarer Tag: "
        f"{latest_global}"
    )

    print(
        "Ältester letzter Rasterwert: "
        f"{earliest_latest}"
    )

    print(
        "Rasterpunkte mit Daten: "
        f"{len(latest_by_grid)}/"
        f"{len(grid_records)}"
    )

    # ---------------------------------------------------------
    # Bundesland-Dateien
    # ---------------------------------------------------------

    by_state_grids = {}

    for row in mapping:
        by_state_grids.setdefault(
            row["state_slug"],
            set(),
        ).add(
            row["grid_id"]
        )

    for state_slug, gids in sorted(
        by_state_grids.items()
    ):
        grids = {}

        state_latest_dates = []

        for gid in sorted(gids):

            values = daily.get(
                gid,
                {}
            )

            valid_days = [
                day
                for day, value
                in values.items()
                if value is not None
            ]

            valid_days.sort()

            # Nur die letzten 120 tatsächlich
            # verfügbaren Tageswerte ausgeben.
            days = valid_days[
                -CURRENT_OUTPUT_DAYS:
            ]

            grids[gid] = [
                [
                    day,
                    round(
                        values[day],
                        1,
                    ),
                ]
                for day in days
            ]

            latest = (
                latest_by_grid.get(
                    gid
                )
            )

            if latest is not None:
                state_latest_dates.append(
                    latest
                )

        state_latest = (
            max(
                state_latest_dates
            ).isoformat()
            if state_latest_dates
            else None
        )

        save_json(
            SNOW
            /
            "current"
            /
            f"{state_slug}.json",
            {
                "metric":
                    "daily_snow_depth",

                "parameter":
                    PARAMETER,

                "source":
                    "GeoSphere Austria "
                    "SNOWGRID Klima v2.1",

                "resource_id":
                    "snowgrid_cl-v2-1d-1km",

                "unit":
                    "cm",

                "columns": [
                    "date",
                    "snow_depth_cm",
                ],

                "latest_available_date":
                    state_latest,

                "grid_count":
                    len(grids),

                "grids":
                    grids,
            },
        )

        print(
            f"{state_slug}: "
            f"{len(grids)} Rasterpunkte, "
            f"neuester Tag "
            f"{state_latest}"
        )

    # ---------------------------------------------------------
    # Status / Provenienz
    # ---------------------------------------------------------

    grids_without_data = (
        len(grid_records)
        -
        len(latest_by_grid)
    )

    lag_days = (
        target_end
        -
        latest_global
    ).days

    # Anzahl Rasterpunkte, deren letzter Wert
    # älter als der österreichweit neueste Wert ist.
    grids_behind_latest = sum(
        1
        for d in latest_by_grid.values()
        if d < latest_global
    )

    save_json(
        SNOW
        /
        "status.json",
        {
            "status":
                "ok",

            "source":
                "GeoSphere Austria "
                "SNOWGRID Klima v2.1",

            "resource_id":
                "snowgrid_cl-v2-1d-1km",

            "parameter":
                PARAMETER,

            "metric":
                "daily_snow_depth",

            "unit":
                "cm",

            "latest_available_date":
                latest_global.isoformat(),

            "earliest_latest_grid_date":
                earliest_latest.isoformat(),

            "target_date":
                target_end.isoformat(),

            "publication_lag_days":
                lag_days,

            "grid_count":
                len(grid_records),

            "grids_with_data":
                len(latest_by_grid),

            "grids_without_data":
                grids_without_data,

            "grids_behind_latest":
                grids_behind_latest,

            "source_lookback_days":
                SOURCE_LOOKBACK_DAYS,

            "current_output_days":
                CURRENT_OUTPUT_DAYS,

            "updated_at":
                local_now.isoformat(
                    timespec="seconds"
                ),

            "note":
                (
                    "Daily snow depth from "
                    "GeoSphere Austria SNOWGRID "
                    "Klima v2.1. Values are converted "
                    "from metres to centimetres. "
                    "The current local calendar day "
                    "is never used. Missing source "
                    "values are never interpreted as "
                    "zero. No fallback source is used. "
                    f"The updater re-fetches the latest "
                    f"{SOURCE_LOOKBACK_DAYS} source days "
                    "on every run so source revisions "
                    "are automatically incorporated."
                ),
        },
    )

    print()
    print(
        "=" * 72
    )

    print(
        "Aktueller Schnee erfolgreich "
        "aktualisiert."
    )

    print(
        "Neuester SNOWGRID-Tag: "
        f"{latest_global}"
    )

    print(
        "Publikationsverzug gegenüber "
        f"gestern: {lag_days} Tage"
    )

    print(
        "Rasterpunkte ohne Daten: "
        f"{grids_without_data}"
    )

    print(
        "Rasterpunkte hinter dem "
        "neuesten Datum: "
        f"{grids_behind_latest}"
    )

    print(
        "Siehe "
        "docs/data/snow/status.json"
    )

    print(
        "=" * 72
    )


if __name__ == "__main__":
    main()
