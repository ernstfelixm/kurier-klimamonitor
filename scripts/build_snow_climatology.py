from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta
import argparse
import csv
import io
import json
import math
import statistics
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

HISTORY_START = date(1961, 1, 1)
HISTORY_END = date(2020, 12, 31)

REFERENCE_OLD = (1961, 1990)
REFERENCE_MODERN = (1991, 2020)

WINDOW_RADIUS_DAYS = 3

# 60 Jahre tägliche Daten:
# rund 21.915 Werte je Rasterpunkt.
#
# 35 Rasterpunkte bleiben damit klar unter
# dem GeoSphere-Limit von 1 Mio. Datenpunkten.
HISTORY_COORD_BATCH = 15

TIMEOUT = 240
RETRIES = 6

# Erwartet werden bei ±3 Tagen ungefähr
# 7 Tage * 30 Jahre = ca. 210 Werte.
#
# 150 ist eine konservative Untergrenze:
# bei weniger Daten geben wir keine Klimatologie aus.
MIN_WINDOW_VALUES = 150


STATE_SLUGS = {
    "Burgenland": "burgenland",
    "Kärnten": "kaernten",
    "Niederösterreich": "niederoesterreich",
    "Oberösterreich": "oberoesterreich",
    "Salzburg": "salzburg",
    "Steiermark": "steiermark",
    "Tirol": "tirol",
    "Vorarlberg": "vorarlberg",
    "Wien": "wien",
}

STATE_ORDER = list(STATE_SLUGS)

SLUG_TO_STATE = {
    slug: state
    for state, slug in STATE_SLUGS.items()
}


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

    if not math.isfinite(x):
        return None

    return x


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
                wait = 5 * (2 ** attempt)

                print(
                    f"Request fehlgeschlagen. "
                    f"Neuer Versuch in {wait}s: "
                    f"{exc}"
                )

                time.sleep(wait)

    raise RuntimeError(
        f"GeoSphere request failed: "
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

        # Fehlende Werte bleiben None.
        #
        # Negative Schneehöhen sind physikalisch
        # nicht sinnvoll und werden ebenfalls
        # als fehlend behandelt.
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


def grid_id(
    lat,
    lon,
):
    a = int(
        round(
            (lat + 90)
            * 1_000_000
        )
    )

    b = int(
        round(
            (lon + 180)
            * 1_000_000
        )
    )

    return (
        f"g{a:09d}_{b:09d}"
    )


def nearest_coord(
    lat,
    lon,
    coords,
):
    if not coords:
        raise RuntimeError(
            "Keine SNOWGRID-"
            "Rasterkoordinate vorhanden."
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


def percentile(
    values,
    q,
):
    vals = sorted(
        v
        for v in values
        if (
            v is not None
            and math.isfinite(v)
        )
    )

    if not vals:
        return None

    if len(vals) == 1:
        return vals[0]

    pos = (
        (len(vals) - 1)
        * q
    )

    lo = int(
        math.floor(pos)
    )

    hi = int(
        math.ceil(pos)
    )

    if lo == hi:
        return vals[lo]

    frac = pos - lo

    return (
        vals[lo]
        * (1 - frac)
        +
        vals[hi]
        * frac
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
            "build_snow_mapping.py ausführen."
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

    places = []

    for row in obj.get(
        "municipalities",
        []
    ):
        places.append(
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

    return places


def unique_grids_for_state(
    places,
    state_name,
):
    unique = {}

    for p in places:
        if p["state"] != state_name:
            continue

        unique[p["grid_id"]] = {
            "grid_id":
                p["grid_id"],
            "lat":
                p["grid_lat"],
            "lon":
                p["grid_lon"],
        }

    return [
        unique[k]
        for k in sorted(unique)
    ]


def fetch_history_for_grids(
    grid_records,
):
    """
    Lädt tägliche Schneehöhen 1961-2020
    für alle Rasterpunkte eines Bundeslandes.

    Rückgabe:
        {
            grid_id: {
                "YYYY-MM-DD": snow_depth_cm | None
            }
        }
    """

    by_grid = {
        g["grid_id"]: {}
        for g in grid_records
    }

    total_batches = math.ceil(
        len(grid_records)
        /
        HISTORY_COORD_BATCH
    )

    for batch_no, batch in enumerate(
        chunks(
            grid_records,
            HISTORY_COORD_BATCH,
        ),
        start=1,
    ):
        print(
            f"Historie Batch "
            f"{batch_no}/{total_batches}: "
            f"{len(batch)} Rasterpunkte "
            f"({HISTORY_START} bis "
            f"{HISTORY_END})"
        )

        requested = [
            (
                g["lat"],
                g["lon"],
            )
            for g in batch
        ]

        text = request_csv(
            requested,
            HISTORY_START,
            HISTORY_END,
        )

        rows = parse_rows(
            text
        )

        # GeoSphere bei großen historischen SNOWGRID-
        # Abfragen etwas Luft zwischen den Requests geben.
        time.sleep(2)

        for (
            day,
            lat,
            lon,
            snow_cm,
        ) in rows:

            # GeoSphere sollte exakt den angefragten
            # SNOWGRID-Punkt zurückgeben.
            #
            # nearest_coord macht die Zuordnung robust
            # gegenüber kleinen Rundungsunterschieden.
            glat, glon = nearest_coord(
                lat,
                lon,
                requested,
            )

            gid = grid_id(
                glat,
                glon,
            )

            if gid not in by_grid:
                continue

            by_grid[gid][day] = (
                snow_cm
            )

    return by_grid


# Wir verwenden ein Schaltjahr als
# Kalender-Schablone.
#
# Dadurch existiert auch 29. Februar.
CALENDAR_START = date(
    2000,
    1,
    1,
)

CALENDAR_DAYS = [
    (
        CALENDAR_START
        +
        timedelta(days=i)
    ).strftime("%m-%d")
    for i in range(366)
]

CALENDAR_INDEX = {
    md: i
    for i, md
    in enumerate(
        CALENDAR_DAYS
    )
}


def calendar_window(
    month_day,
):
    """
    Liefert das ±3-Tage-Fenster um
    einen Kalendertag.

    Beispiel:
        01-15
        -> 01-12 bis 01-18

    Der Jahreswechsel wird zyklisch
    behandelt.
    """

    center = (
        CALENDAR_INDEX[
            month_day
        ]
    )

    result = []

    for offset in range(
        -WINDOW_RADIUS_DAYS,
        WINDOW_RADIUS_DAYS + 1,
    ):
        idx = (
            center + offset
        ) % len(
            CALENDAR_DAYS
        )

        result.append(
            CALENDAR_DAYS[idx]
        )

    return result


def split_reference_buckets(
    daily_values,
):
    """
    Erstellt pro Referenzperiode
    Month-Day-Buckets.

    Fehlende Werte werden NICHT
    als 0 interpretiert.
    """

    old = {}
    modern = {}

    for day, value in daily_values.items():

        if value is None:
            continue

        try:
            d = date.fromisoformat(
                day
            )
        except ValueError:
            continue

        md = d.strftime(
            "%m-%d"
        )

        if (
            REFERENCE_OLD[0]
            <= d.year
            <= REFERENCE_OLD[1]
        ):
            old.setdefault(
                md,
                []
            ).append(
                value
            )

        elif (
            REFERENCE_MODERN[0]
            <= d.year
            <= REFERENCE_MODERN[1]
        ):
            modern.setdefault(
                md,
                []
            ).append(
                value
            )

    return (
        old,
        modern,
    )


def stats_for_window(
    buckets,
    month_day,
):
    values = []

    for md in calendar_window(
        month_day
    ):
        values.extend(
            buckets.get(
                md,
                []
            )
        )

    values = [
        v
        for v in values
        if (
            v is not None
            and math.isfinite(v)
        )
    ]

    n = len(values)

    if (
        n
        <
        MIN_WINDOW_VALUES
    ):
        return {
            "p20": None,
            "median": None,
            "p80": None,
            "zero_share": None,
            "n": n,
        }

    p20 = percentile(
        values,
        0.20,
    )

    median = statistics.median(
        values
    )

    p80 = percentile(
        values,
        0.80,
    )

    zero_count = sum(
        1
        for v in values
        if abs(v) < 1e-9
    )

    zero_share = (
        zero_count
        /
        n
    )

    return {
        "p20":
            round(
                p20,
                1,
            ),
        "median":
            round(
                median,
                1,
            ),
        "p80":
            round(
                p80,
                1,
            ),
        "zero_share":
            round(
                zero_share,
                4,
            ),
        "n":
            n,
    }


def climatology(
    daily_values,
):
    """
    Berechnet für jeden Kalendertag:

    - p20
    - Median
    - p80
    - Null-Anteil
    - n

    jeweils für:
    - 1961-1990
    - 1991-2020

    und jeweils aus einem
    ±3-Tage-Kalenderfenster.
    """

    old_buckets, modern_buckets = (
        split_reference_buckets(
            daily_values
        )
    )

    out = {}

    for md in CALENDAR_DAYS:

        old = stats_for_window(
            old_buckets,
            md,
        )

        modern = stats_for_window(
            modern_buckets,
            md,
        )

        out[md] = [
            old["p20"],
            old["median"],
            old["p80"],
            old["zero_share"],

            modern["p20"],
            modern["median"],
            modern["p80"],
            modern["zero_share"],

            old["n"],
            modern["n"],
        ]

    return out


def build_state(
    state_name,
    all_places,
):
    state_slug = (
        STATE_SLUGS[
            state_name
        ]
    )

    grids = (
        unique_grids_for_state(
            all_places,
            state_name,
        )
    )

    municipality_count = sum(
        1
        for p in all_places
        if p["state"] == state_name
    )

    print()
    print(
        "=" * 72
    )

    print(
        f"Baue Schnee-Klimatologie "
        f"für {state_name}"
    )

    print(
        f"Gemeinden: "
        f"{municipality_count}"
    )

    print(
        f"Eindeutige Rasterpunkte: "
        f"{len(grids)}"
    )

    print(
        "=" * 72
    )

    if not grids:
        raise RuntimeError(
            f"Keine Rasterpunkte für "
            f"{state_name} gefunden."
        )

    history = (
        fetch_history_for_grids(
            grids
        )
    )

    compact_grids = {}

    for i, grid in enumerate(
        grids,
        start=1,
    ):
        gid = (
            grid["grid_id"]
        )

        daily = (
            history.get(
                gid,
                {}
            )
        )

        clim = climatology(
            daily
        )

        rows = []

        for md in CALENDAR_DAYS:
            values = (
                clim[md]
            )

            rows.append(
                [
                    md,
                    *values,
                ]
            )

        compact_grids[
            gid
        ] = rows

        if (
            i % 25 == 0
            or i == len(grids)
        ):
            print(
                f"{state_name}: "
                f"Klimatologie "
                f"{i}/{len(grids)}"
            )

    output_path = (
        SNOW
        /
        "climatology"
        /
        f"{state_slug}.json"
    )

    save_json(
        output_path,
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
            "historical_window_days":
                7,
            "historical_window_radius_days":
                WINDOW_RADIUS_DAYS,
            "reference_periods": [
                "1961-1990",
                "1991-2020",
            ],
            "minimum_window_values":
                MIN_WINDOW_VALUES,
            "columns": [
                "day",

                "p20_1961_1990",
                "median_1961_1990",
                "p80_1961_1990",
                "zero_share_1961_1990",

                "p20_1991_2020",
                "median_1991_2020",
                "p80_1991_2020",
                "zero_share_1991_2020",

                "n_1961_1990",
                "n_1991_2020",
            ],
            "grid_count":
                len(compact_grids),
            "grids":
                compact_grids,
        },
    )

    print(
        f"{state_name}: "
        f"fertig."
    )

    print(
        f"Geschrieben: "
        f"{output_path}"
    )


def write_build_status(
    all_places,
):
    built_states = []

    for (
        state,
        slug,
    ) in STATE_SLUGS.items():

        path = (
            SNOW
            /
            "climatology"
            /
            f"{slug}.json"
        )

        if path.exists():
            built_states.append(
                state
            )

    unique_grids = {
        p["grid_id"]
        for p in all_places
        if p.get(
            "grid_id"
        )
    }

    save_json(
        SNOW
        /
        "build_status.json",
        {
            "status":
                (
                    "ok"
                    if (
                        len(built_states)
                        ==
                        len(STATE_SLUGS)
                    )
                    else "partial"
                ),

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

            "historical_window_days":
                7,

            "historical_window_radius_days":
                WINDOW_RADIUS_DAYS,

            "reference_periods": [
                "1961-1990",
                "1991-2020",
            ],

            "minimum_window_values":
                MIN_WINDOW_VALUES,

            "built_states":
                built_states,

            "mapped_municipality_count":
                len(all_places),

            "unique_grid_count":
                len(unique_grids),

            "generated_at":
                date.today().isoformat(),
        },
    )


def resolve_state(value):
    if value == "all":
        return None

    if value in STATE_SLUGS:
        return value

    if value in SLUG_TO_STATE:
        return (
            SLUG_TO_STATE[
                value
            ]
        )

    raise SystemExit(
        "Unbekanntes Bundesland: "
        f"{value}. "
        "Erlaubt: all, "
        + ", ".join(
            STATE_SLUGS.values()
        )
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--state",
        default="all",
        help=(
            "Bundesland-Slug oder 'all'. "
            "Beispiel: wien, tirol, "
            "niederoesterreich"
        ),
    )

    args = (
        parser.parse_args()
    )

    print(
        "Lade SNOWGRID-"
        "Gemeindezuordnung ..."
    )

    all_places = (
        load_mapping()
    )

    print(
        f"{len(all_places)} "
        f"Gemeinden geladen."
    )

    unique_grid_count = len(
        {
            p["grid_id"]
            for p in all_places
        }
    )

    print(
        f"{unique_grid_count} "
        f"eindeutige Rasterpunkte."
    )

    requested_state = (
        resolve_state(
            args.state
        )
    )

    if requested_state is None:
        states = (
            STATE_ORDER
        )

    else:
        states = [
            requested_state
        ]

    for state in states:
        build_state(
            state,
            all_places,
        )

        write_build_status(
            all_places
        )

    print()
    print(
        "=" * 72
    )

    print(
        "Schnee-Klimatologie "
        "abgeschlossen."
    )

    print(
        "Siehe "
        "docs/data/snow/"
        "build_status.json"
    )

    print(
        "=" * 72
    )


if __name__ == "__main__":
    main()
