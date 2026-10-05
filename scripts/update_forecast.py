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
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"

PARAMETER = "2t"
TIMEOUT = 60
REQUEST_PAUSE_SECONDS = 0.30
MIN_SUCCESS_RATE = 0.90
EARLIEST_LOCAL_FETCH_TIME = dtime(5, 15)


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
                "lat": float(lat),
                "lon": float(lon),
            }
        )

    return out


def existing_complete_forecast_for_today(today, expected_station_count):
    path = DATA / "forecast.json"
    if not path.exists():
        return False

    try:
        obj = load_json(path)
    except Exception:
        return False

    return (
        obj.get("status") == "ok"
        and obj.get("forecast_date") == today.isoformat()
        and int(obj.get("station_count", 0)) >= expected_station_count
        and int(obj.get("expected_station_count", 0)) == expected_station_count
    )


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


def run():
    local_now = now_vienna()
    today = local_now.date()
    stations = used_stations()

    if local_now.timetz().replace(tzinfo=None) < EARLIEST_LOCAL_FETCH_TIME:
        print(
            f"Skipping forecast fetch: local time {local_now.strftime('%H:%M')} is before "
            f"{EARLIEST_LOCAL_FETCH_TIME.strftime('%H:%M')} Europe/Vienna."
        )
        return

    if existing_complete_forecast_for_today(today, len(stations)):
        print(
            f"Skipping forecast fetch: complete forecast for {today.isoformat()} "
            f"already exists for all {len(stations)} stations."
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


if __name__ == "__main__":
    run()
