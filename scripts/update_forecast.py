from pathlib import Path
from datetime import datetime, timezone
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
    """Station IDs that are actually used by the municipality search."""
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


def fetch_station_forecast(station):
    """
    Fetch the newest NWP-v2 forecast for one station coordinate.

    One request per station is deliberate:
    - no ambiguity when the API snaps the coordinate to the nearest 1-km grid point
    - 109 daily requests are below GeoSphere's documented hourly request limit
    - a short pause keeps us below the per-second limit
    """
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

    # GeoSphere timestamps are ISO-like and include a timezone.
    # Accept Z as UTC as well.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def daily_max_for_today(csv_text, today_local):
    """
    Compute today's maximum of hourly 2m temperature in Europe/Vienna time.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    values = []

    for row in reader:
        raw_time = row.get("time") or row.get("date") or row.get("timestamp")
        dt = parse_time(raw_time)
        if dt is None:
            continue

        local_dt = dt.astimezone(ZoneInfo("Europe/Vienna"))
        if local_dt.date() != today_local:
            continue

        value = parse_number(row.get(PARAMETER))
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

        # GeoSphere documents 5 requests/second and 240/hour.
        # 0.30 s keeps this workflow comfortably below 5 requests/second.
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
