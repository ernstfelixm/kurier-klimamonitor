from __future__ import annotations

from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import argparse
import csv
import io
import json
import math
import statistics
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
PRECIP = ROOT / "docs" / "data" / "precipitation"

SPARTACUS_API = "https://dataset.api.hub.geosphere.at/v1/timeseries/historical/spartacus-v3-1d-1km"
INCA_API = "https://dataset.api.hub.geosphere.at/v1/timeseries/historical/inca-v2-1h-1km"
INCA_METADATA = INCA_API + "/metadata"
SPARTACUS_PARAMETER = "RR"
INCA_PARAMETER = "tp"

VIENNA = ZoneInfo("Europe/Vienna")
TIMEOUT = 180
RETRIES = 4

# Räumlich breit gestreute Stichprobe. Fehlende Gemeinden werden übersprungen.
DEFAULT_PLACES = [
    "Wien",
    "Bregenz",
    "Innsbruck",
    "Reutte",
    "Salzburg",
    "Linz",
    "Graz",
    "Klagenfurt am Wörthersee",
    "Eisenstadt",
    "St. Pölten",
    "Lienz",
    "Bad Ischl",
    "Mariazell",
    "Zwettl-Niederösterreich",
    "Neusiedl am See",
]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def norm(s: str) -> str:
    return (
        str(s or "")
        .replace("ß", "ss")
        .lower()
        .strip()
    )


def parse_float(value):
    if value in (None, "", "nan", "NaN", "null", "None"):
        return None
    try:
        x = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def find_column(fieldnames, exact=(), contains=()):
    fields = list(fieldnames or [])
    low = {str(name).lower(): name for name in fields if name is not None}
    for key in exact:
        if key.lower() in low:
            return low[key.lower()]
    for name in fields:
        lname = str(name).lower()
        if any(token.lower() in lname for token in contains):
            return name
    return None


def request_text(url, params=None):
    last_error = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text, r.headers
        except Exception as exc:
            last_error = exc
            if attempt + 1 < RETRIES:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"GeoSphere request failed: {last_error}")


def request_csv(api, coords, start, end, parameter):
    params = [
        ("parameters", parameter),
        ("start", start),
        ("end", end),
        ("output_format", "csv"),
    ]
    params += [("lat_lon", f"{lat:.6f},{lon:.6f}") for lat, lon in coords]
    text, headers = request_text(api, params)
    print(
        f"{api.rsplit('/',1)[-1]}: {len(coords)} Punkte, {start} bis {end}, "
        f"datapoint-count={headers.get('datapoint-count')}"
    )
    return text


def parse_spartacus(text):
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []

    time_col = find_column(
        fields,
        exact=("time", "date", "timestamp"),
        contains=("time", "date"),
    )
    lat_col = find_column(
        fields,
        exact=("lat", "latitude"),
        contains=("latitude", "lat"),
    )
    lon_col = find_column(
        fields,
        exact=("lon", "longitude"),
        contains=("longitude", "lon"),
    )
    rr_col = find_column(
        fields,
        exact=("RR",),
        contains=("rr [", "rr[", "precip"),
    )

    if not all((time_col, lat_col, lon_col, rr_col)):
        raise RuntimeError(f"Unerwartete SPARTACUS-Spalten: {fields}")

    rows = []

    for row in reader:
        rr = parse_float(row.get(rr_col))
        lat = parse_float(row.get(lat_col))
        lon = parse_float(row.get(lon_col))
        raw = row.get(time_col)

        if raw and lat is not None and lon is not None:
            rows.append((str(raw)[:10], lat, lon, rr))

    return rows


def parse_iso_datetime(value: str) -> datetime:
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_inca(text):
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []

    time_col = find_column(
        fields,
        exact=("time", "date", "timestamp"),
        contains=("time", "date"),
    )
    lat_col = find_column(
        fields,
        exact=("lat", "latitude"),
        contains=("latitude", "lat"),
    )
    lon_col = find_column(
        fields,
        exact=("lon", "longitude"),
        contains=("longitude", "lon"),
    )
    tp_col = find_column(
        fields,
        exact=("tp",),
        contains=("tp [", "tp[", "precip"),
    )

    if not all((time_col, lat_col, lon_col, tp_col)):
        raise RuntimeError(f"Unerwartete INCA-Spalten: {fields}")

    rows = []

    for row in reader:
        rr = parse_float(row.get(tp_col))
        lat = parse_float(row.get(lat_col))
        lon = parse_float(row.get(lon_col))
        raw = row.get(time_col)

        if raw and lat is not None and lon is not None and rr is not None:
            rows.append((parse_iso_datetime(raw), lat, lon, rr))

    return rows


def sqdist(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def nearest_coord(lat, lon, coords):
    return min(coords, key=lambda p: sqdist((lat, lon), p))


def coord_key(lat, lon):
    return (round(float(lat), 6), round(float(lon), 6))


def load_mapping():
    obj = load_json(PRECIP / "municipalities.json")
    cols = obj["columns"]
    ix = {c: i for i, c in enumerate(cols)}
    rows = []
    for r in obj["municipalities"]:
        rows.append({
            "id": str(r[ix["id"]]),
            "name": r[ix["name"]],
            "state": r[ix["state"]],
            "grid_id": r[ix["grid_id"]],
            "grid_lat": float(r[ix["grid_lat"]]),
            "grid_lon": float(r[ix["grid_lon"]]),
        })
    return rows


def select_places(mapping, names):
    out = []
    for name in names:
        exact = [m for m in mapping if norm(m["name"]) == norm(name)]
        if exact:
            out.append(exact[0])
        else:
            print(f"WARNUNG: Gemeinde nicht gefunden: {name}")
    if not out:
        raise RuntimeError("Keine Testgemeinden gefunden.")
    return out


def expected_local_hours(day: date):
    a = datetime(day.year, day.month, day.day, tzinfo=VIENNA).astimezone(timezone.utc)
    b = (datetime(day.year, day.month, day.day, tzinfo=VIENNA) + timedelta(days=1)).astimezone(timezone.utc)
    return int((b - a).total_seconds() // 3600)


def aggregate_inca(rows, requested_coords, label_mode):
    # label_mode=start: Zeitstempel wird dem lokalen Kalendertag direkt zugeordnet.
    # label_mode=end: RR gilt als vorangegangene Stunde; 1 Mikrosekunde abziehen.
    by_coord = {coord_key(*c): {} for c in requested_coords}
    returned = {}

    for ts_utc, lat, lon, rr in rows:
        req = nearest_coord(lat, lon, requested_coords)
        k = coord_key(*req)
        returned.setdefault(k, (lat, lon))

        if label_mode == "end":
            ts_utc = ts_utc - timedelta(microseconds=1)

        local = ts_utc.astimezone(VIENNA)
        d = local.date()
        by_coord[k].setdefault(d, []).append(rr)

    out = {k: {} for k in by_coord}
    counts = {k: {} for k in by_coord}
    for k, days in by_coord.items():
        for d, vals in days.items():
            counts[k][d] = len(vals)
            if len(vals) == expected_local_hours(d):
                out[k][d] = sum(vals)
            else:
                out[k][d] = None
    return out, counts, returned


def aggregate_spartacus(rows, requested_coords):
    out = {coord_key(*c): {} for c in requested_coords}
    returned = {}
    for day, lat, lon, rr in rows:
        req = nearest_coord(lat, lon, requested_coords)
        k = coord_key(*req)
        returned.setdefault(k, (lat, lon))
        out[k][date.fromisoformat(day)] = rr
    return out, returned


def valid_pairs(sp, inc, overlap_start, overlap_end):
    pairs = []
    d = overlap_start
    while d <= overlap_end:
        a = sp.get(d)
        b = inc.get(d)
        if a is not None and b is not None:
            pairs.append((d, a, b))
        d += timedelta(days=1)
    return pairs


def metrics(pairs):
    if not pairs:
        return None
    diffs = [b - a for _, a, b in pairs]
    absdiff = [abs(x) for x in diffs]
    sq = [x*x for x in diffs]
    ssum = sum(a for _, a, _ in pairs)
    isum = sum(b for _, _, b in pairs)
    return {
        "n_days": len(pairs),
        "spartacus_total_mm": round(ssum, 2),
        "inca_total_mm": round(isum, 2),
        "bias_mm_total": round(isum - ssum, 2),
        "bias_percent_total": round((isum / ssum - 1) * 100, 1) if ssum > 0 else None,
        "mean_daily_bias_mm": round(statistics.mean(diffs), 3),
        "mae_mm": round(statistics.mean(absdiff), 3),
        "rmse_mm": round(math.sqrt(statistics.mean(sq)), 3),
    }


def rolling30(values, end_day):
    vals = []
    for i in range(29, -1, -1):
        v = values.get(end_day - timedelta(days=i))
        if v is None:
            return None
        vals.append(v)
    return sum(vals)


def hybrid_rolling_comparisons(sp, inc, overlap_start, overlap_end):
    rows = []

    d = overlap_start
    while d <= overlap_end:
        pure = rolling30(sp, d)

        if pure is None:
            d += timedelta(days=1)
            continue

        for replace_days in (1, 2, 3):
            hybrid = dict(sp)

            replacement_ok = True

            for offset in range(replace_days):
                dd = d - timedelta(days=offset)
                value = inc.get(dd)

                if value is None:
                    replacement_ok = False
                    break

                hybrid[dd] = value

            if not replacement_ok:
                continue

            hy = rolling30(hybrid, d)

            if hy is None:
                continue

            rows.append({
                "date": d.isoformat(),
                "inca_days": replace_days,
                "spartacus_30d_mm": round(pure, 2),
                "hybrid_30d_mm": round(hy, 2),
                "difference_mm": round(hy - pure, 2),
                "difference_percent": (
                    round((hy / pure - 1) * 100, 1)
                    if pure > 0
                    else None
                ),
            })

        d += timedelta(days=1)

    return rows


def pooled_metrics(all_pairs):
    return metrics(all_pairs)


def main():
    ap = argparse.ArgumentParser(
        description="Methodischer Overlap-Test: SPARTACUS v3 daily vs. INCA v2 hourly RR."
    )
    ap.add_argument("--start", default="2026-10-01", help="Erster Überlappungstag, YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="Letzter Testtag, YYYY-MM-DD; Default: gestern")
    ap.add_argument(
        "--places",
        default=",".join(DEFAULT_PLACES),
        help="Kommagetrennte Gemeindenamen",
    )
    ap.add_argument("--output-dir", default="tmp/inca_overlap_test")
    args = ap.parse_args()

    overlap_start = date.fromisoformat(args.start)
    overlap_end = date.fromisoformat(args.end) if args.end else (
        datetime.now(timezone.utc).astimezone(VIENNA).date() - timedelta(days=1)
    )
    if overlap_end < overlap_start:
        raise RuntimeError("Enddatum liegt vor Startdatum.")

    mapping = load_mapping()
    places = select_places(mapping, [x.strip() for x in args.places.split(",") if x.strip()])
    coords = [(p["grid_lat"], p["grid_lon"]) for p in places]

    # Für reine 30-Tage-SPARTACUS-Vergleiche 29 Tage Vorlauf holen.
    sp_start = overlap_start - timedelta(days=29)

    # INCA v2 beginnt am 1.10.2026. Stunde 00 UTC des Folgetags mitnehmen,
    # damit ein lokaler Kalendertag vollständig aggregiert werden kann.
    inca_start = overlap_start.isoformat() + "T00:00"
    inca_end = (overlap_end + timedelta(days=1)).isoformat() + "T00:00"

    # Metadaten nur zur Dokumentation. Fehler hier soll den Test nicht abbrechen.
    metadata = None
    try:
        txt, _ = request_text(INCA_METADATA)
        metadata = json.loads(txt)
    except Exception as exc:
        metadata = {"metadata_error": str(exc)}

    sp_text = request_csv(
        SPARTACUS_API,
        coords,
        sp_start.isoformat(),
        overlap_end.isoformat(),
        SPARTACUS_PARAMETER,
    )
    inca_text = request_csv(
        INCA_API,
        coords,
        inca_start,
        inca_end,
        INCA_PARAMETER,
    )

    sp_rows = parse_spartacus(sp_text)
    inca_rows = parse_inca(inca_text)

    sp_by, sp_returned = aggregate_spartacus(sp_rows, coords)

    # Stundenstempel-Konvention wird explizit getestet.
    candidates = {}
    for mode in ("start", "end"):
        inc_by, counts, inc_returned = aggregate_inca(inca_rows, coords, mode)
        pooled = []
        for p in places:
            k = coord_key(p["grid_lat"], p["grid_lon"])
            pooled.extend(valid_pairs(sp_by[k], inc_by[k], overlap_start, overlap_end))
        candidates[mode] = {
            "inca_by": inc_by,
            "counts": counts,
            "returned": inc_returned,
            "pooled": pooled_metrics(pooled),
        }

    usable = [
        (mode, data["pooled"]["mae_mm"])
        for mode, data in candidates.items()
        if data["pooled"] is not None
    ]
    if not usable:
        raise RuntimeError("Keine gemeinsamen vollständigen SPARTACUS/INCA-Tage gefunden.")
    best_mode = min(usable, key=lambda x: x[1])[0]
    chosen = candidates[best_mode]

    report_places = []
    csv_rows = []
    all_pairs = []

    for p in places:
        k = coord_key(p["grid_lat"], p["grid_lon"])
        pairs = valid_pairs(sp_by[k], chosen["inca_by"][k], overlap_start, overlap_end)
        all_pairs.extend(pairs)
        m = metrics(pairs)
        roll = hybrid_rolling_comparisons(
            sp_by[k], chosen["inca_by"][k], overlap_start, overlap_end
        )

        sp_coord = sp_returned.get(k)
        inca_coord = chosen["returned"].get(k)

        report_places.append({
            "id": p["id"],
            "name": p["name"],
            "state": p["state"],
            "query_spartacus_grid": [p["grid_lat"], p["grid_lon"]],
            "returned_spartacus_grid": list(sp_coord) if sp_coord else None,
            "returned_inca_grid": list(inca_coord) if inca_coord else None,
            "daily_metrics": m,
            "rolling30_comparisons": roll,
        })

        for d, a, b in pairs:
            csv_rows.append({
                "place": p["name"],
                "state": p["state"],
                "date": d.isoformat(),
                "spartacus_mm": round(a, 3),
                "inca_mm": round(b, 3),
                "difference_mm": round(b-a, 3),
                "difference_percent": round((b/a-1)*100, 1) if a > 0 else "",
            })

    pooled = pooled_metrics(all_pairs)

    replacement_stats = {}

    for replace_days in (1, 2, 3):
        diffs_mm = []
        diffs_pct = []

        for p in report_places:
            for r in p["rolling30_comparisons"]:
                if r["inca_days"] != replace_days:
                    continue

                diffs_mm.append(abs(r["difference_mm"]))

                if r["difference_percent"] is not None:
                    diffs_pct.append(abs(r["difference_percent"]))

        replacement_stats[str(replace_days)] = {
            "n": len(diffs_mm),
            "abs_difference_mm_median": (
                round(statistics.median(diffs_mm), 2)
                if diffs_mm
                else None
            ),
            "abs_difference_mm_max": (
                round(max(diffs_mm), 2)
                if diffs_mm
                else None
            ),
            "abs_difference_percent_median": (
                round(statistics.median(diffs_pct), 1)
                if diffs_pct
                else None
            ),
            "abs_difference_percent_max": (
                round(max(diffs_pct), 1)
                if diffs_pct
                else None
            ),
        }

    summary = {
        "status": "ok",
        "purpose": "SPARTACUS-INCA precipitation overlap/bias test",
        "parameter": "RR",
        "spartacus_resource": "spartacus-v3-1d-1km",
        "inca_resource": "inca-v2-1h-1km",
        "overlap_start": overlap_start.isoformat(),
        "overlap_end_requested": overlap_end.isoformat(),
        "timezone_for_inca_daily_aggregation": "Europe/Vienna",
        "inca_timestamp_assignment_tested": ["start", "end"],
        "chosen_inca_timestamp_assignment": best_mode,
        "selection_rule": "lower pooled daily MAE against SPARTACUS in overlap",
        "place_count": len(places),
        "pooled_daily_metrics": pooled,
        "rolling30_replacement_test": replacement_stats,
        "interpretation_hint": (
            "Kein fixer Akzeptanzgrenzwert ist eingebaut. Entscheidend sind systematischer Bias, "
            "MAE/RMSE, räumliche Ausreißer und der Effekt auf die 30-Tage-Summe."
        ),
    }

    report = {
        "summary": summary,
        "timestamp_mode_candidates": {
            mode: data["pooled"] for mode, data in candidates.items()
        },
        "inca_metadata": metadata,
        "places": report_places,
    }

    out_dir = ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    save_json(out_dir / "report.json", report)

    with (out_dir / "daily_comparison.csv").open("w", encoding="utf-8", newline="") as f:
        fields = [
            "place", "state", "date", "spartacus_mm", "inca_mm",
            "difference_mm", "difference_percent"
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(csv_rows)

    print("")
    print("=== ERGEBNIS ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("")
    print(f"Report: {out_dir / 'report.json'}")
    print(f"CSV:    {out_dir / 'daily_comparison.csv'}")


if __name__ == "__main__":
    main()
