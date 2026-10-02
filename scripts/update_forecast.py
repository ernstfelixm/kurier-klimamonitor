from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json

import requests

BASE = "https://dataset.api.hub.geosphere.at/v1/timeseries/forecast/nwp-v2-1h-1km"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"

PARAMETER = "2t"
TIMEOUT = 60
STATION_ID = 105


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def now_vienna():
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Vienna"))


def get_station():
    stations = load_json(DATA / "stations.json")["stations"]
    for station in stations:
        if int(station["id"]) == STATION_ID:
            return station
    raise RuntimeError(f"Station {STATION_ID} not found")


def main():
    station = get_station()

    params = [
        ("parameters", PARAMETER),
        ("lat_lon", f'{station["lat"]},{station["lon"]}'),
        ("output_format", "csv"),
    ]

    print("=== FORECAST DEBUG ===")
    print(f'Station: {station["id"]} {station["name"]}')
    print(f'Coordinates: {station["lat"]}, {station["lon"]}')
    print(f'Local now: {now_vienna().isoformat()}')
    print(f'Request URL base: {BASE}')
    print(f'Request params: {params}')

    response = requests.get(BASE, params=params, timeout=TIMEOUT)

    print(f"HTTP status: {response.status_code}")
    print(f"Content-Type: {response.headers.get('content-type')}")
    print(f"Final URL: {response.url}")
    print(f"Response length: {len(response.text)} characters")

    response.raise_for_status()

    print("\n=== FIRST 80 RESPONSE LINES ===")
    lines = response.text.splitlines()
    for i, line in enumerate(lines[:80], start=1):
        print(f"{i:03d}: {line}")

    print("\n=== END DEBUG ===")


if __name__ == "__main__":
    main()
