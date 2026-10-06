from pathlib import Path
from datetime import datetime, timezone, time as dtime
from zoneinfo import ZoneInfo
import csv
import io
import json
import math
import time

import requests

BASE = "https://dataset.api.hub.geosphere.at/v1/timeseries/forecast/nwp-v2-1h-1km"
GEOJSON_SOURCE_URL = (
    "https://raw.githubusercontent.com/ginseng666/GeoJSON-TopoJSON-Austria/"
    "master/2021/simplified-99.9/laender_999_geo.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"

PARAMETER = "2t"
TIMEOUT = 60
REQUEST_PAUSE_SECONDS = 0.30
MIN_SUCCESS_RATE = 0.90
EARLIEST_LOCAL_FETCH_TIME = dtime(5, 15)
REFERENCE_PERIOD = "1991-2020"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def now_vienna():
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Vienna"))


def used_station_ids():
    obj = load_json(DATA / "municipalities.json")
    columns = obj["columns"]
    station_index = columns.index("station_id")

    return {
        int(row[station_index])
        for row in obj["municipalities"]
        if row[station_index] not in (None, "")
    }


def used_stations():
    stations = load_json(DATA / "stations.json")["stations"]
    wanted = used_station_ids()
    by_id = {int(s["id"]): s for s in stations}

    missing = sorted(wanted - set(by_id))
    if missing:
        raise RuntimeError(
            f"{len(missing)} station IDs from municipalities.json are missing in stations.json"
        )

    out = []
    for sid in sorted(wanted):
        station = by_id[sid]
        lat = station.get("lat")
        lon = station.get("lon")

        if lat is None or lon is None:
            raise RuntimeError(f"Station {sid} has no coordinates")

        out.append(
            {
                "id": sid,
                "name": station.get("name"),
                "state": station.get("state"),
                "state_slug": station.get("state_slug"),
                "lat": float(lat),
                "lon": float(lon),
            }
        )

    return out


def existing_complete_forecast_for_today(today, expected_station_count):
    path = DATA / "forecast.json"
    if not path.exists():
        return None

    try:
        obj = load_json(path)
    except Exception:
        return None

    if (
        obj.get("status") == "ok"
        and obj.get("forecast_date") == today.isoformat()
        and int(obj.get("station_count", 0)) >= expected_station_count
        and int(obj.get("expected_station_count", 0)) == expected_station_count
    ):
        return obj

    return None


def fetch_station_forecast(station):
    params = [
        ("parameters", PARAMETER),
        ("lat_lon", f'{station["lat"]},{station["lon"]}'),
        ("output_format", "csv"),
    ]

    last_error = None

    for attempt in range(4):
        try:
            response = requests.get(BASE, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            time.sleep(2 ** attempt)

    raise RuntimeError(
        f'GeoSphere forecast request failed for station {station["id"]}: {last_error}'
    )


def parse_number(raw):
    if raw in (None, "", "nan", "NaN", "null", "None"):
        return None

    try:
        value = float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return None

    return value if math.isfinite(value) else None


def parse_time(raw):
    if not raw:
        return None

    text = str(raw).strip()

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def find_parameter_column(fieldnames):
    if not fieldnames:
        return None

    if PARAMETER in fieldnames:
        return PARAMETER

    prefix = PARAMETER + " "
    for name in fieldnames:
        if name and (name.startswith(prefix) or name.startswith(PARAMETER + "[")):
            return name

    return None


def daily_max_for_today(csv_text, today_local):
    reader = csv.DictReader(io.StringIO(csv_text))
    parameter_column = find_parameter_column(reader.fieldnames)

    if parameter_column is None:
        raise RuntimeError(
            f"Temperature column not found. CSV columns: {reader.fieldnames}"
        )

    values = []

    for row in reader:
        raw_time = row.get("time") or row.get("date") or row.get("timestamp")
        dt = parse_time(raw_time)
        if dt is None:
            continue

        local_dt = dt.astimezone(ZoneInfo("Europe/Vienna"))
        if local_dt.date() != today_local:
            continue

        value = parse_number(row.get(parameter_column))
        if value is None:
            continue

        values.append(value)

    if not values:
        return None

    return round(max(values), 1)


def ensure_austria_states_geojson():
    """Download the static state geometry once if it is not in the repo yet."""
    path = DATA / "austria_states.geojson"
    if path.exists():
        return

    response = requests.get(GEOJSON_SOURCE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    obj = response.json()

    if obj.get("type") != "FeatureCollection" or not obj.get("features"):
        raise RuntimeError("Downloaded Austria state geometry is not a valid FeatureCollection")

    save_json(path, obj)
    print(f"Austria state geometry written: {path}")


def build_map_today(forecast_obj, stations, local_now):
    """Build the compact data file used by the Shorthand Austria map."""
    if forecast_obj.get("forecast_date") != local_now.date().isoformat():
        raise RuntimeError("Cannot build map_today.json from a forecast for another date")

    day_key = local_now.strftime("%m-%d")
    forecast_values = forecast_obj.get("stations", {})
    climate_cache = {}
    map_rows = []
    window_days = None

    for station in stations:
        sid = str(station["id"])
        forecast = parse_number(forecast_values.get(sid))
        if forecast is None:
            continue

        state_slug = station.get("state_slug")
        if not state_slug:
            continue

        if state_slug not in climate_cache:
            climate = load_json(DATA / "climatology" / f"{state_slug}.json")
            columns = climate.get("columns", [])
            climate_cache[state_slug] = (climate, columns)
            if window_days is None:
                window_days = climate.get("window_days")

        climate, columns = climate_cache[state_slug]
        series = climate.get("stations", {}).get(sid)
        if not series:
            continue

        try:
            p20_i = columns.index("p20_1991_2020")
            median_i = columns.index("median_1991_2020")
            p80_i = columns.index("p80_1991_2020")
        except ValueError as exc:
            raise RuntimeError(
                f"Expected 1991-2020 climatology columns missing in {state_slug}.json"
            ) from exc

        row = next((item for item in series if item and item[0] == day_key), None)
        if row is None:
            continue

        p20 = parse_number(row[p20_i])
        median = parse_number(row[median_i])
        p80 = parse_number(row[p80_i])
        if p20 is None or median is None or p80 is None:
            continue

        if forecast > p80:
            category = "warm"
        elif forecast < p20:
            category = "cold"
        else:
            category = "normal"

        map_rows.append(
            {
                "id": station["id"],
                "name": station.get("name"),
                "state": station.get("state"),
                "lat": round(station["lat"], 5),
                "lon": round(station["lon"], 5),
                "forecast": round(forecast, 1),
                "p20": round(p20, 2),
                "median": round(median, 2),
                "p80": round(p80, 2),
                "anomaly": round(forecast - median, 2),
                "category": category,
            }
        )

    counts = {
        "warm": sum(1 for item in map_rows if item["category"] == "warm"),
        "normal": sum(1 for item in map_rows if item["category"] == "normal"),
        "cold": sum(1 for item in map_rows if item["category"] == "cold"),
    }

    output = {
        "status": "ok" if map_rows else "error",
        "date": local_now.date().isoformat(),
        "generated_at": forecast_obj.get("generated_at") or local_now.isoformat(timespec="seconds"),
        "reference_period": REFERENCE_PERIOD,
        "window_days": window_days,
        "station_count": len(map_rows),
        "expected_station_count": len(stations),
        "category_counts": counts,
        "categories": {
            "warm": "ungewöhnlich warm",
            "normal": "normal",
            "cold": "ungewöhnlich kalt",
        },
        "stations": map_rows,
    }

    save_json(DATA / "map_today.json", output)
    print(
        "Map data written: "
        f"{len(map_rows)}/{len(stations)} stations; "
        f"warm={counts['warm']}, normal={counts['normal']}, cold={counts['cold']}"
    )


def run():
    local_now = now_vienna()
    today = local_now.date()
    stations = used_stations()

    # Static geometry is needed only once. If already present, this is a no-op.
    ensure_austria_states_geojson()

    existing = existing_complete_forecast_for_today(today, len(stations))
    if existing is not None:
        print(
            f"Skipping forecast fetch: complete forecast for {today.isoformat()} "
            f"already exists for all {len(stations)} stations."
        )
        # Still rebuild map_today.json. This is useful directly after installing
        # the map feature, even when today's forecast already exists.
        build_map_today(existing, stations, local_now)
        return

    if local_now.timetz().replace(tzinfo=None) < EARLIEST_LOCAL_FETCH_TIME:
        print(
            f"Skipping forecast fetch: local time {local_now.strftime('%H:%M')} is before "
            f"{EARLIEST_LOCAL_FETCH_TIME.strftime('%H:%M')} Europe/Vienna."
        )
        return

    results = {}
    errors = []

    for index, station in enumerate(stations, start=1):
        try:
            csv_text = fetch_station_forecast(station)
            tlmax = daily_max_for_today(csv_text, today)

            if tlmax is None:
                errors.append(
                    {
                        "station_id": station["id"],
                        "error": "no forecast values for today's local date",
                    }
                )
            else:
                results[str(station["id"])] = tlmax

        except Exception as exc:
            errors.append(
                {
                    "station_id": station["id"],
                    "error": str(exc)[:300],
                }
            )

        print(
            f'[{index}/{len(stations)}] station {station["id"]}: '
            f'{results.get(str(station["id"]), "no value")}'
        )

        if index < len(stations):
            time.sleep(REQUEST_PAUSE_SECONDS)

    success_rate = len(results) / len(stations) if stations else 0

    output = {
        "status": "ok" if success_rate >= MIN_SUCCESS_RATE else "error",
        "forecast_date": today.isoformat(),
        "generated_at": local_now.isoformat(timespec="seconds"),
        "metric": "tlmax_forecast",
        "parameter": PARAMETER,
        "unit": "°C",
        "model": "GeoSphere Austria NWP-v2 1 km",
        "station_count": len(results),
        "expected_station_count": len(stations),
        "stations": results,
    }

    if errors:
        output["errors"] = errors[:25]
        output["error_count"] = len(errors)

    save_json(DATA / "forecast.json", output)

    print(
        f'Forecast written: {len(results)}/{len(stations)} stations '
        f'({success_rate:.1%}), date {today.isoformat()}'
    )

    if output["status"] != "ok":
        raise RuntimeError(
            f"Forecast coverage too low: {len(results)}/{len(stations)} stations"
        )

    build_map_today(output, stations, local_now)


if __name__ == "__main__":
    run()
