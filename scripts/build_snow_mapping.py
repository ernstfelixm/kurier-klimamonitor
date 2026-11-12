from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta
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

# Für die Rasterzuordnung reicht ein kurzer aktueller Zeitraum.
# Wir nehmen bewusst 31 Tage, damit ein Publikationsverzug von SNOWGRID
# keine Rolle spielt.
MAPPING_LOOKBACK_DAYS = 30

# Die Requests enthalten nur wenige Tageswerte je Punkt.
MAPPING_PLACE_BATCH = 180

TIMEOUT = 180
RETRIES = 4


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
                f"{len(coords)} Koordinaten, "
                f"{start} bis {end}, "
                f"datapoint-count="
                f"{response.headers.get('datapoint-count')}"
            )

            return response.text

        except Exception as exc:
            last_error = exc

            if attempt + 1 < RETRIES:
                wait = 2 ** attempt

                print(
                    f"Request fehlgeschlagen, "
                    f"neuer Versuch in {wait}s: "
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

        snow_depth = parse_float(
            row.get(snow_col)
        )

        if (
            not raw_time
            or lat is None
            or lon is None
        ):
            continue

        rows.append(
            (
                str(raw_time)[:10],
                lat,
                lon,
                snow_depth,
            )
        )

    return rows


def grid_id(
    lat,
    lon,
):
    """
    Deterministische Raster-ID.

    Gleiches Prinzip wie beim
    Niederschlagsmodul.
    """

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


def municipality_rows():
    """
    Verwendet ausdrücklich die ursprünglichen
    Gemeinde-Koordinaten aus:

    docs/data/municipalities.json

    Also NICHT die bereits auf SPARTACUS
    gesnappten Koordinaten.
    """

    obj = load_json(
        DATA / "municipalities.json"
    )

    cols = obj["columns"]

    ix = {
        name: i
        for i, name
        in enumerate(cols)
    }

    required = {
        "id",
        "name",
        "state",
        "lat",
        "lon",
    }

    missing = (
        required
        -
        set(cols)
    )

    if missing:
        raise RuntimeError(
            "Fehlende Spalten in "
            "municipalities.json: "
            + ", ".join(
                sorted(missing)
            )
        )

    out = []

    for row in obj[
        "municipalities"
    ]:
        state = row[
            ix["state"]
        ]

        if (
            state
            not in STATE_SLUGS
        ):
            raise RuntimeError(
                f"Unbekanntes Bundesland: "
                f"{state}"
            )

        lat = float(
            row[ix["lat"]]
        )

        lon = float(
            row[ix["lon"]]
        )

        out.append(
            {
                "id": str(
                    row[ix["id"]]
                ),
                "name": row[
                    ix["name"]
                ],
                "state": state,
                "state_slug":
                    STATE_SLUGS[
                        state
                    ],
                "lat": lat,
                "lon": lon,
            }
        )

    return out


def squared_distance(
    lat1,
    lon1,
    lat2,
    lon2,
):
    return (
        (lat2 - lat1) ** 2
        +
        (lon2 - lon1) ** 2
    )


def nearest_coord(
    lat,
    lon,
    coords,
):
    if not coords:
        raise RuntimeError(
            "Keine SNOWGRID-"
            "Rasterkoordinate "
            "zurückgegeben."
        )

    return min(
        coords,
        key=lambda p:
            squared_distance(
                lat,
                lon,
                p[0],
                p[1],
            ),
    )


def haversine_km(
    lat1,
    lon1,
    lat2,
    lon2,
):
    """
    Nur für die Qualitätskontrolle
    der Rasterzuordnung.
    """

    radius = 6371.0088

    p1 = math.radians(
        lat1
    )

    p2 = math.radians(
        lat2
    )

    dp = math.radians(
        lat2 - lat1
    )

    dl = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(dp / 2) ** 2
        +
        math.cos(p1)
        *
        math.cos(p2)
        *
        math.sin(dl / 2) ** 2
    )

    c = (
        2
        *
        math.atan2(
            math.sqrt(a),
            math.sqrt(1 - a),
        )
    )

    return (
        radius * c
    )


def map_municipalities(
    places,
):
    """
    Fragt SNOWGRID für die originalen
    Gemeinde-Koordinaten ab.

    Die von GeoSphere zurückgegebenen
    Koordinaten sind die tatsächlichen
    SNOWGRID-Rasterpunkte.
    """

    # Nicht auf den aktuellen Tag angewiesen.
    # Der 30-Tage-Zeitraum funktioniert auch,
    # wenn SNOWGRID 1-2 Tage Publikationsverzug hat.
    end = (
        date.today()
        -
        timedelta(days=1)
    )

    start = (
        end
        -
        timedelta(
            days=MAPPING_LOOKBACK_DAYS
        )
    )

    mapped = []

    total_batches = math.ceil(
        len(places)
        /
        MAPPING_PLACE_BATCH
    )

    for batch_no, batch in enumerate(
        chunks(
            places,
            MAPPING_PLACE_BATCH,
        ),
        start=1,
    ):
        print()
        print(
            f"Rasterzuordnung "
            f"Batch {batch_no}/"
            f"{total_batches}: "
            f"{len(batch)} Gemeinden"
        )

        requested = [
            (
                p["lat"],
                p["lon"],
            )
            for p in batch
        ]

        text = request_csv(
            requested,
            start,
            end,
        )

        rows = parse_rows(
            text
        )

        returned = sorted(
            {
                (
                    round(lat, 6),
                    round(lon, 6),
                )
                for (
                    _day,
                    lat,
                    lon,
                    _snow,
                )
                in rows
            }
        )

        if not returned:
            raise RuntimeError(
                "Keine Rasterpunkte für "
                f"Mapping-Batch "
                f"{batch_no} "
                "zurückgegeben."
            )

        for place in batch:
            glat, glon = (
                nearest_coord(
                    place["lat"],
                    place["lon"],
                    returned,
                )
            )

            distance_km = (
                haversine_km(
                    place["lat"],
                    place["lon"],
                    glat,
                    glon,
                )
            )

            mapped.append(
                {
                    **place,
                    "grid_id":
                        grid_id(
                            glat,
                            glon,
                        ),
                    "grid_lat":
                        glat,
                    "grid_lon":
                        glon,
                    "_grid_distance_km":
                        distance_km,
                }
            )

    return mapped


def write_output(
    mapped,
):
    rows = []

    for p in sorted(
        mapped,
        key=lambda x: (
            list(
                STATE_SLUGS
            ).index(
                x["state"]
            ),
            x["name"],
            x["id"],
        ),
    ):
        rows.append(
            [
                p["id"],
                p["name"],
                p["state"],
                p["state_slug"],
                p["grid_id"],
                round(
                    p["grid_lat"],
                    6,
                ),
                round(
                    p["grid_lon"],
                    6,
                ),
            ]
        )

    obj = {
        "version":
            date.today().isoformat(),
        "source":
            "GeoSphere Austria "
            "SNOWGRID Klima v2.1",
        "resource_id":
            "snowgrid_cl-v2-1d-1km",
        "parameter":
            PARAMETER,
        "count":
            len(rows),
        "columns": [
            "id",
            "name",
            "state",
            "state_slug",
            "grid_id",
            "grid_lat",
            "grid_lon",
        ],
        "municipalities":
            rows,
    }

    path = (
        SNOW
        /
        "municipalities.json"
    )

    save_json(
        path,
        obj,
    )

    return path


def print_quality_report(
    mapped,
):
    distances = [
        p["_grid_distance_km"]
        for p in mapped
    ]

    unique_grids = {
        p["grid_id"]
        for p in mapped
    }

    print()
    print(
        "============================"
    )
    print(
        "SNOWGRID-MAPPING FERTIG"
    )
    print(
        "============================"
    )

    print(
        f"Gemeinden: "
        f"{len(mapped)}"
    )

    print(
        f"Eindeutige Rasterpunkte: "
        f"{len(unique_grids)}"
    )

    if distances:
        print(
            "Rasterdistanz Median: "
            f"{statistics.median(distances):.3f} km"
        )

        print(
            "Rasterdistanz Maximum: "
            f"{max(distances):.3f} km"
        )

    warnings = [
        p
        for p in mapped
        if p[
            "_grid_distance_km"
        ] > 2.0
    ]

    if warnings:
        print()
        print(
            "WARNUNG: Gemeinden mit "
            "Rasterdistanz > 2 km:"
        )

        for p in sorted(
            warnings,
            key=lambda x:
                x[
                    "_grid_distance_km"
                ],
            reverse=True,
        )[:20]:
            print(
                f"  {p['name']} "
                f"({p['state']}): "
                f"{p['_grid_distance_km']:.2f} km"
            )

    else:
        print(
            "Keine Rasterdistanz "
            "> 2 km."
        )

    print()
    print(
        "Stichproben:"
    )

    sample_names = {
        "Wien",
        "Bregenz",
        "Innsbruck",
        "Reutte",
        "Salzburg",
        "Graz",
        "Klagenfurt am Wörthersee",
        "Mariazell",
        "Lienz",
        "Zwettl-Niederösterreich",
    }

    for p in mapped:
        if (
            p["name"]
            in sample_names
        ):
            print(
                f"  {p['name']}: "
                f"{p['grid_lat']:.6f}, "
                f"{p['grid_lon']:.6f} "
                f"({p['_grid_distance_km']:.2f} km)"
            )


def main():
    print(
        "Lade ursprüngliche "
        "Gemeindekoordinaten ..."
    )

    places = (
        municipality_rows()
    )

    print(
        f"{len(places)} Gemeinden "
        "geladen."
    )

    if (
        len(places)
        != 2092
    ):
        print(
            "WARNUNG: Erwartet wurden "
            "2092 Gemeinden."
        )

    print()
    print(
        "Mappe Gemeinden auf "
        "SNOWGRID ..."
    )

    mapped = (
        map_municipalities(
            places
        )
    )

    if (
        len(mapped)
        != len(places)
    ):
        raise RuntimeError(
            "Nicht alle Gemeinden "
            "wurden gemappt."
        )

    path = write_output(
        mapped
    )

    print_quality_report(
        mapped
    )

    print()
    print(
        f"Datei geschrieben: "
        f"{path}"
    )


if __name__ == "__main__":
    main()
