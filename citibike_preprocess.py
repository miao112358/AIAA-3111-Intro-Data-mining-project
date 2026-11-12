#!/usr/bin/env python3
"""NYC Citi Bike 2025: HTTP download, disk-backed audit/cleaning and OD aggregation.

Dependencies: Python >= 3.10, numpy, pandas >= 2, pyarrow.
  python3 -m pip install numpy 'pandas>=2' pyarrow
  python3 citibike_preprocess.py self-test
  python3 citibike_preprocess.py download --months 1 7
  python3 citibike_preprocess.py run --months 1 7 --offline
  python3 citibike_preprocess.py run --months 1 2 3 4 5 6 7 8 9 10 11 12
  python3 citibike_preprocess.py audit --months 1 7 --offline

Historical trips are public monthly ZIPs, not the live GBFS API; no API key needed.
Every run creates a NEW directory under data/processed; never combines run counts.
To reuse downloads pass --offline. Exact global deduplication applies to ALL selected
months in a run; rerun the entire selection when adding months. SQLite retains raw
payloads and source row numbers for audit; budget tens of GB for a full-year run.
No network or file mutations occur on import. See generated README.md for semantics.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import resource
import sqlite3
import sys
import tempfile
import time
import unittest
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

LOG = logging.getLogger("citibike")
FIELDS = ("ride_id", "rideable_type", "started_at", "ended_at",
          "start_station_name", "start_station_id", "end_station_name", "end_station_id",
          "start_lat", "start_lng", "end_lat", "end_lng", "member_casual")
NY = ZoneInfo("America/New_York")
BASE_URL = "https://s3.amazonaws.com/tripdata"
OFFICIAL_PAGE = "https://citibikenyc.com/system-data"
GRID_VERSION = "utm18n_o0_v1"
WINDOWS = {"AM": (7, 10), "MID": (11, 14), "PM": (16, 19)}


def json_write(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def open_url(url, timeout=60):
    return urlopen(Request(url, headers={"User-Agent": "NYC-CitiBike-Research/1.0"}), timeout=timeout)


def download_month(year, month, raw_dir, offline=False, retries=3, opener=open_url):
    """Stream to .part, validate ZIP CRC, atomically publish; preserve existing raw ZIPs.

    Official archives have used both .csv.zip and -csv.zip. Try the second only on
    a genuine 404 of the first, never on timeout/403/corruption. No JC fallback.
    """
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    names = [f"{year}{month:02d}-citibike-tripdata.zip",
             f"{year}{month:02d}-citibike-tripdata.csv.zip",
             f"{year}{month:02d}-citibike-tripdata-csv.zip"]
    existing = [raw_dir / n for n in names if (raw_dir / n).is_file()]
    if len(existing) > 1:
        raise ValueError(f"Multiple archive versions for {year}-{month:02d}: {existing}; select one explicitly")
    headers, downloaded_at = {}, None
    if existing:
        target = existing[0]
        sidecar = target.with_suffix(target.suffix + ".json")
        if sidecar.exists():
            old = json.loads(sidecar.read_text(encoding="utf-8"))
            expected = old.get("sha256")
            if expected and sha256_file(target) != expected:
                raise ValueError(f"Checksum changed: {target}; raw file left untouched")
            headers = old.get("http_headers", {})
            downloaded_at = old.get("downloaded_at")
    else:
        if offline:
            raise FileNotFoundError(f"Missing NYC archive for {year}-{month:02d} in {raw_dir}")
        target = None
        for name in names:
            candidate = raw_dir / name
            url = f"{BASE_URL}/{quote(name)}"
            for attempt in range(retries):
                part = candidate.with_suffix(candidate.suffix + ".part")
                try:
                    LOG.info("Requesting %s", url)
                    with opener(url) as response, part.open("wb") as out:
                        headers = {k.lower(): v for k, v in response.headers.items()}
                        total = 0
                        while True:
                            block = response.read(4 * 1024 * 1024)
                            if not block:
                                break
                            out.write(block)
                            total += len(block)
                    length = headers.get("content-length")
                    if length and int(length) != total:
                        raise OSError(f"Truncated HTTP response: expected {length}, got {total}")
                    validate_zip(part)
                    part.replace(candidate)
                    target = candidate
                    downloaded_at = utc_now()
                    break
                except HTTPError as exc:
                    if exc.code == 404:
                        break
                    if exc.code not in (408, 429, 500, 502, 503, 504) or attempt + 1 == retries:
                        raise
                    time.sleep(min(2 ** attempt, 8))
                except (URLError, OSError, zipfile.BadZipFile):
                    if attempt + 1 == retries:
                        raise
                    time.sleep(min(2 ** attempt, 8))
            if target is not None:
                break
        if target is None:
            raise FileNotFoundError(f"Official NYC archive not found for {year}-{month:02d}")
    members = validate_zip(target)
    entry = {"source_month": f"{year}-{month:02d}", "official_page": OFFICIAL_PAGE,
             "url": f"{BASE_URL}/{quote(target.name)}", "local_path": str(target.resolve()),
             "size_bytes": target.stat().st_size, "sha256": sha256_file(target),
             "downloaded_at": downloaded_at, "verified_at": utc_now(), "http_headers": headers,
             "csv_members": members, "status": "verified"}
    json_write(target.with_suffix(target.suffix + ".json"), entry)
    return entry


def validate_zip(path):
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise zipfile.BadZipFile(f"CRC failed: {bad}")
        members = sorted(i.filename for i in archive.infolist()
                         if not i.is_dir() and i.filename.lower().endswith(".csv")
                         and not i.filename.startswith("__MACOSX/")
                         and not PurePosixPath(i.filename).name.startswith("."))
        if not members or len(set(members)) != len(members):
            raise ValueError(f"No CSVs or duplicate member paths: {path}")
        if any(PurePosixPath(m).name.upper().startswith("JC") for m in members):
            raise ValueError(f"JC data detected in NYC archive: {path}")
        return members


def project_utm18(lon, lat):
    """WGS84 -> EPSG:32618, fixed central meridian -75°, standard UTM series.

    Used only in configured NYC vicinity (default lon -75..-72, lat 39..42).
    Terms through A^6 (northing) and A^5 (easting); sub-millimetre error near NYC.
    Self-test checks an independently published USGS UTM reference coordinate.
    Fixed definition avoids a mandatory PROJ binary dependency.
    """
    lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    a, e2, k = 6378137.0, 0.0066943799901413165, 0.9996
    phi = np.radians(lat)
    ep2 = e2 / (1 - e2)
    n = a / np.sqrt(1 - e2 * np.sin(phi) ** 2)
    t, c = np.tan(phi) ** 2, ep2 * np.cos(phi) ** 2
    aa = np.cos(phi) * np.radians(lon + 75)
    m = a * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * np.sin(2 * phi)
             + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * np.sin(4 * phi)
             - 35 * e2**3 / 3072 * np.sin(6 * phi))
    x = 500000 + k * n * (aa + (1 - t + c) * aa**3 / 6
                          + (5 - 18*t + t*t + 72*c - 58*ep2) * aa**5 / 120)
    y = k * (m + n * np.tan(phi) * (aa**2 / 2
             + (5 - t + 9*c + 4*c*c) * aa**4 / 24
             + (61 - 58*t + t*t + 600*c - 330*ep2) * aa**6 / 720))
    return x, y


def inverse_utm18(x, y):
    """Invert the same local projection numerically for map coordinates."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    lat = y / 111000
    lon = -75 + (x - 500000) / (111000 * np.cos(np.radians(lat)))
    for _ in range(8):
        xx, yy = project_utm18(lon, lat)
        xp, yp = project_utm18(lon + 1e-5, lat)
        xq, yq = project_utm18(lon, lat + 1e-5)
        ax, ay, bx, by = (xp-xx)/1e-5, (yp-yy)/1e-5, (xq-xx)/1e-5, (yq-yy)/1e-5
        det = ax*by - ay*bx
        lon += ((x-xx)*by - (y-yy)*bx) / det
        lat += ((y-yy)*ax - (x-xx)*ay) / det
    return lon, lat


def grid_index(x, size):
    return np.floor(np.asarray(x) / size).astype(np.int64)


def region_id(size, ix, iy):
    return f"{GRID_VERSION}:{size}:{ix}:{iy}"


def localize(value):
    """Return (local datetime, ambiguity flag), rejecting nonexistent local times."""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None, False
    if dt.tzinfo is not None:
        return dt.astimezone(NY), False
    a, b = dt.replace(tzinfo=NY, fold=0), dt.replace(tzinfo=NY, fold=1)
    valid_a = a.astimezone(timezone.utc).astimezone(NY).replace(tzinfo=None) == dt
    valid_b = b.astimezone(timezone.utc).astimezone(NY).replace(tzinfo=None) == dt
    if not valid_a and not valid_b:
        return None, False
    return (a if valid_a else b), valid_a and valid_b and a.utcoffset() != b.utcoffset()


def clock_window(hour):
    return next((w for w, (lo, hi) in WINDOWS.items() if lo <= hour < hi), "OTHER")


def clean_frame(frame, year, bbox):
    """Classify one deduplicated batch; return valid rows, exclusive reasons, flags."""
    f = frame.copy()
    reasons = pd.Series("valid", index=f.index, dtype=object)
    flags = Counter()
    dates, hours = [], []
    for idx, start, end in zip(f.index, f.started_at, f.ended_at):
        s, sa = localize(start)
        e, ea = localize(end)
        reason = "valid"
        if s is None or e is None:
            reason = "invalid_time"
        elif sa or ea:
            flags["ambiguous_dst"] += 1
            # The true duration cannot be inferred without a UTC offset. Quarantine
            # rather than fabricate an offset or classify a negative wall duration.
            reason = "ambiguous_time"
        else:
            seconds = (e.astimezone(timezone.utc) - s.astimezone(timezone.utc)).total_seconds()
            if seconds <= 0:
                reason = "nonpositive_duration"
            elif s.year != year:
                reason = "outside_year"
        reasons.at[idx] = reason
        dates.append(s.date().isoformat() if s else "")
        hours.append(s.hour if s else -1)
    f["local_date"], f["hour"] = dates, hours
    f["window"] = [clock_window(h) for h in hours]
    f["day_type"] = ["weekday" if date.fromisoformat(d).weekday() < 5 else "weekend" if d else "" for d in dates] if all(dates) else [
        ("weekday" if date.fromisoformat(d).weekday() < 5 else "weekend") if d else "" for d in dates]
    f["split"] = ["development" if d and int(d[5:7]) <= 9 else "holdout" for d in dates]
    coords = ["start_lat", "start_lng", "end_lat", "end_lng"]
    for col in coords:
        f[col] = pd.to_numeric(f[col], errors="coerce")
    finite = np.isfinite(f[coords].to_numpy()).all(axis=1)
    legal = finite & f.start_lat.between(-90, 90) & f.end_lat.between(-90, 90) & f.start_lng.between(-180, 180) & f.end_lng.between(-180, 180)
    nonzero = (f[coords] != 0).all(axis=1)
    invalid = ~(legal & nonzero)
    reasons[(reasons == "valid") & invalid] = "invalid_endpoint"
    west, south, east, north = bbox
    nearby = f.start_lng.between(west, east) & f.end_lng.between(west, east) & f.start_lat.between(south, north) & f.end_lat.between(south, north)
    reasons[(reasons == "valid") & ~nearby] = "outside_coordinate_envelope"
    flags["missing_start_station_name"] += int((f.start_station_name == "").sum())
    flags["missing_end_station_name"] += int((f.end_station_name == "").sum())
    flags["unknown_member_casual"] += int((~f.member_casual.isin(["member", "casual"])).sum())
    flags["unknown_rideable_type"] += int((~f.rideable_type.isin(["classic_bike", "electric_bike", "docked_bike"])).sum())
    return f.loc[reasons == "valid"].copy(), reasons, flags


def init_db(db):
    db.executescript("""
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=NORMAL;
      PRAGMA temp_store=FILE;
      PRAGMA cache_size=-65536;
      CREATE TABLE raw(seq INTEGER PRIMARY KEY, fragment_id INTEGER, source_row INTEGER,
                       ride_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL);
      CREATE TABLE fragments(id INTEGER PRIMARY KEY, source_month TEXT, archive TEXT,
                             member TEXT, row_count INTEGER, status TEXT);
      CREATE TABLE quality(source_month TEXT, outcome TEXT, count INTEGER,
                           PRIMARY KEY(source_month,outcome));
      CREATE TABLE flags(source_month TEXT, flag TEXT, count INTEGER,
                         PRIMARY KEY(source_month,flag));
      CREATE TABLE od(local_date TEXT, day_type TEXT, split TEXT, window TEXT,
        grid_size_m INTEGER, origin_region TEXT, destination_region TEXT, ride_count INTEGER,
        PRIMARY KEY(local_date,window,grid_size_m,origin_region,destination_region));
      CREATE TABLE endpoints(lon REAL, lat REAL, grid_size_m INTEGER, region_id TEXT,
        PRIMARY KEY(lon,lat,grid_size_m));
      CREATE TABLE stations(station_id TEXT, lon REAL, lat REAL, grid_size_m INTEGER,
        region_id TEXT, first_date TEXT, last_date TEXT, observations INTEGER,
        PRIMARY KEY(station_id,lon,lat,grid_size_m));
      CREATE TABLE regions(grid_size_m INTEGER, region_id TEXT PRIMARY KEY, ix INTEGER, iy INTEGER,
        parent_region TEXT, center_lon REAL, center_lat REAL, min_x REAL, min_y REAL,
        max_x REAL, max_y REAL);
      CREATE TABLE hourly(local_date TEXT, hour INTEGER, ride_count INTEGER,
                          PRIMARY KEY(local_date,hour));
      CREATE TABLE disposition(seq INTEGER PRIMARY KEY, outcome TEXT);
    """)


def ingest(db, entries, batch_size):
    """Read EVERY CSV member without extracting ZIP paths to the filesystem."""
    fragments = []
    for entry in entries:
        with zipfile.ZipFile(entry["local_path"]) as z:
            for member in entry["csv_members"]:
                LOG.info("Reading %s :: %s", entry["source_month"], member)
                cur = db.execute("INSERT INTO fragments(source_month,archive,member,row_count,status) VALUES(?,?,?,?,?)",
                                 (entry["source_month"], entry["local_path"], member, 0, "reading"))
                fid, rows = cur.lastrowid, 0
                info = {"fragment_id": fid, "source_month": entry["source_month"], "archive": entry["local_path"],
                        "member": member, "rows": 0, "status": "reading"}
                fragments.append(info)
                try:
                    with z.open(member) as binary:
                        # Reject duplicate/extra/missing fields BEFORE pandas can mangle names.
                        wrapper = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
                        reader = csv.reader(wrapper)
                        header = next(reader)
                    info["fields"] = header
                    if len(header) != 13 or len(set(header)) != 13 or set(header) != set(FIELDS):
                        raise ValueError(f"Expected exactly 13 unique fields; got {header}")
                    with z.open(member) as binary:
                        for chunk in pd.read_csv(binary, dtype=str, keep_default_na=False,
                                                 encoding="utf-8-sig", chunksize=batch_size, on_bad_lines="error"):
                            chunk = chunk.loc[:, FIELDS].copy()
                            for col in ("ride_id", "start_station_id", "end_station_id"):
                                chunk[col] = chunk[col].str.strip()
                            records = []
                            for offset, values in enumerate(chunk.itertuples(index=False, name=None), rows + 2):
                                payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
                                records.append((fid, offset, values[0], hashlib.sha256(payload.encode()).hexdigest(), payload))
                            db.executemany("INSERT INTO raw(fragment_id,source_row,ride_id,fingerprint,payload) VALUES(?,?,?,?,?)", records)
                            rows += len(records)
                            db.commit()
                    db.execute("UPDATE fragments SET row_count=?,status='complete' WHERE id=?", (rows, fid))
                    info.update(rows=rows, status="complete")
                    db.commit()
                except Exception as exc:
                    info.update(rows=rows, status="failed", error=str(exc))
                    db.execute("UPDATE fragments SET row_count=?,status='failed' WHERE id=?", (rows, fid))
                    db.commit()
                    raise
    return fragments


def global_deduplicate(db):
    """Global across all selected archives; conflicting IDs have NO surviving row."""
    db.executescript("""
      CREATE INDEX raw_ride_id ON raw(ride_id);
      CREATE TABLE ids AS SELECT ride_id, MIN(seq) AS first_seq,
        (MIN(fingerprint) != MAX(fingerprint)) AS conflict FROM raw
        WHERE ride_id != '' GROUP BY ride_id;
      CREATE UNIQUE INDEX ids_ride_id ON ids(ride_id);
      INSERT INTO disposition
        SELECT r.seq, CASE WHEN r.ride_id='' THEN 'missing_id'
          WHEN i.conflict=1 THEN 'conflict_duplicate'
          WHEN r.seq!=i.first_seq THEN 'exact_duplicate' ELSE 'pending' END
        FROM raw r LEFT JOIN ids i USING(ride_id);
      INSERT INTO quality SELECT f.source_month,d.outcome,COUNT(*) FROM raw r
        JOIN fragments f ON f.id=r.fragment_id JOIN disposition d ON d.seq=r.seq
        WHERE d.outcome!='pending' GROUP BY f.source_month,d.outcome;
    """)
    db.commit()


def aggregate(db, valid, sizes):
    if valid.empty:
        return
    hourly = valid.groupby(["local_date", "hour"]).size()
    db.executemany("INSERT INTO hourly VALUES(?,?,?) ON CONFLICT(local_date,hour) DO UPDATE SET ride_count=ride_count+excluded.ride_count",
                   [(d, int(h), int(n)) for (d, h), n in hourly.items()])
    sx, sy = project_utm18(valid.start_lng.to_numpy(), valid.start_lat.to_numpy())
    ex, ey = project_utm18(valid.end_lng.to_numpy(), valid.end_lat.to_numpy())
    for size in sizes:
        six, siy, eix, eiy = [grid_index(v, size) for v in (sx, sy, ex, ey)]
        f = valid.copy()
        f["origin_region"] = [region_id(size, int(x), int(y)) for x, y in zip(six, siy)]
        f["destination_region"] = [region_id(size, int(x), int(y)) for x, y in zip(eix, eiy)]
        counts = f.groupby(["local_date", "day_type", "split", "window", "origin_region", "destination_region"]).size()
        db.executemany("INSERT INTO od VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(local_date,window,grid_size_m,origin_region,destination_region) DO UPDATE SET ride_count=ride_count+excluded.ride_count",
                       [(d, typ, split, w, size, a, b, int(n)) for (d, typ, split, w, a, b), n in counts.items()])
        pairs = sorted(set(zip(six.tolist()+eix.tolist(), siy.tolist()+eiy.tolist())))
        cx = np.array([(x+.5)*size for x, y in pairs])
        cy = np.array([(y+.5)*size for x, y in pairs])
        lons, lats = inverse_utm18(cx, cy)
        db.executemany("INSERT OR IGNORE INTO regions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       [(size, region_id(size, x, y), x, y,
                         region_id(1000, x//2, y//2) if size == 500 else None,
                         float(lon), float(lat), x*size, y*size, (x+1)*size, (y+1)*size)
                        for (x, y), lon, lat in zip(pairs, lons, lats)])
        for prefix, keys in (("start", f.origin_region), ("end", f.destination_region)):
            ep = pd.DataFrame({"station_id": f[prefix+"_station_id"], "lon": f[prefix+"_lng"],
                               "lat": f[prefix+"_lat"], "region_id": keys, "local_date": f.local_date})
            unique = ep.drop_duplicates(["lon", "lat"])
            db.executemany("INSERT OR IGNORE INTO endpoints VALUES(?,?,?,?)",
                           [(float(r.lon), float(r.lat), size, r.region_id) for r in unique.itertuples()])
            grouped = ep[ep.station_id != ""].groupby(["station_id", "lon", "lat", "region_id"]).local_date.agg(["min", "max", "count"])
            db.executemany("INSERT INTO stations VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(station_id,lon,lat,grid_size_m) DO UPDATE SET first_date=MIN(first_date,excluded.first_date),last_date=MAX(last_date,excluded.last_date),observations=observations+excluded.observations",
                           [(sid, float(lon), float(lat), size, rid, row["min"], row["max"], int(row["count"]))
                            for (sid, lon, lat, rid), row in grouped.iterrows()])


def process(db, config):
    query = """SELECT r.seq,r.payload,f.source_month FROM raw r
       JOIN disposition d ON d.seq=r.seq JOIN fragments f ON f.id=r.fragment_id
       WHERE d.outcome='pending' ORDER BY r.seq"""
    # Keyset pagination permits committed writes without invalidating an active cursor.
    last = 0
    while True:
        rows = db.execute("SELECT * FROM (" + query.replace(" ORDER BY r.seq", "") + ") WHERE seq>? ORDER BY seq LIMIT ?",
                          (last, config["batch_size"])).fetchall()
        if not rows:
            break
        last = rows[-1][0]
        frame = pd.DataFrame([json.loads(r[1]) for r in rows], columns=FIELDS)
        frame["seq"] = [r[0] for r in rows]
        frame["source_month"] = [r[2] for r in rows]
        valid, reasons, _ = clean_frame(frame, config["year"], config["coordinate_envelope"])
        db.executemany("UPDATE disposition SET outcome=? WHERE seq=?",
                       [(reason, int(seq)) for reason, seq in zip(reasons, frame.seq)])
        for month, indexes in frame.groupby("source_month").groups.items():
            counts = reasons.loc[indexes].value_counts()
            db.executemany("INSERT INTO quality VALUES(?,?,?) ON CONFLICT(source_month,outcome) DO UPDATE SET count=count+excluded.count",
                           [(month, reason, int(n)) for reason, n in counts.items()])
            _, _, flags = clean_frame(frame.loc[indexes], config["year"], config["coordinate_envelope"])
            db.executemany("INSERT INTO flags VALUES(?,?,?) ON CONFLICT(source_month,flag) DO UPDATE SET count=count+excluded.count",
                           [(month, flag, int(n)) for flag, n in flags.items()])
        aggregate(db, valid, config["grid_sizes_m"])
        db.commit()
        LOG.info("Processed through raw sequence %s", last)


def export_query(db, query, path, batch=50000):
    """Stream SQLite query to a single compressed Parquet without all rows in RAM."""
    cursor = db.execute(query)
    names = [v[0] for v in cursor.description]
    writer = None
    tmp = Path(str(path) + ".part")
    try:
        while True:
            rows = cursor.fetchmany(batch)
            if not rows:
                break
            table = pa.Table.from_pylist([dict(zip(names, r)) for r in rows])
            if writer is None:
                # SQLite all-NULL first batches need a stable type for later parent IDs.
                if "parent_region" in table.column_names:
                    i = table.column_names.index("parent_region")
                    table = table.set_column(i, "parent_region", table.column(i).cast(pa.string()))
                writer = pq.ParquetWriter(tmp, table.schema, compression="zstd")
            table = table.cast(writer.schema)
            writer.write_table(table)
        if writer is None:
            # Empty result still has columns (explicit output schema inferred below).
            integer_names = {"grid_size_m", "ix", "iy", "ride_count", "observations", "hour", "count", "source_row", "seq"}
            real_names = {"lon", "lat", "center_lon", "center_lat", "min_x", "min_y", "max_x", "max_y"}
            schema = pa.schema([(n, pa.int64() if n in integer_names else pa.float64() if n in real_names else pa.string()) for n in names])
            pq.write_table(pa.Table.from_batches([], schema=schema), tmp, compression="zstd")
    finally:
        if writer:
            writer.close()
    tmp.replace(path)


def write_csv(path, rows, columns):
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        out = csv.DictWriter(f, fieldnames=columns)
        out.writeheader()
        out.writerows(rows)


def load_coverage(path, year):
    """Explicit reviewed per-window statuses; blank rows never imply complete."""
    result = {}
    if path is None:
        return result
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["local_date"], row["window"])
            if date.fromisoformat(key[0]).year != year or key[1] not in WINDOWS:
                raise ValueError(f"Invalid coverage key: {key}")
            if key in result or row["status"] not in ("complete", "incomplete", "unknown") or not row.get("reason", "").strip():
                raise ValueError(f"Duplicate/invalid coverage row or missing reason: {row}")
            result[key] = row
    return result


def coverage_rows(db, config, reviewed):
    imported = {r[0] for r in db.execute("SELECT source_month FROM fragments GROUP BY source_month HAVING MIN(status='complete')=1")}
    hourly = {(d, int(h)): int(n) for d, h, n in db.execute("SELECT * FROM hourly")}
    rows = []
    d = date(config["year"], 1, 1)
    while d.year == config["year"]:
        day = d.isoformat()
        row = {"local_date": day, "day_type": "weekday" if d.weekday() < 5 else "weekend",
               "split": "development" if d.month <= 9 else "holdout", "is_holiday": day in config["holidays"],
               "import_complete": day[:7] in imported}
        for w, (lo, hi) in WINDOWS.items():
            review = reviewed.get((day, w))
            status = review["status"] if review else "unknown"
            reason = review["reason"] if review else "Not reviewed: file import does not establish collection completeness"
            if not row["import_complete"]:
                if status == "complete":
                    raise ValueError(f"Cannot mark unimported month complete: {day} {w}")
                status, reason = "unknown", "Source month not fully imported"
            row[w+"_status"], row[w+"_reason"] = status, reason
            row[w+"_observed_rides"] = sum(hourly.get((day, h), 0) for h in range(lo, hi))
            row[w+"_zero_observed_hours"] = ";".join(str(h) for h in range(lo, hi) if not hourly.get((day, h), 0))
        rows.append(row)
        d += timedelta(days=1)
    return rows


def export_outputs(db, out, config, entries, fragments):
    gv = "'" + GRID_VERSION + "' AS grid_version"
    time_cols = f"local_date,day_type,split,window,{gv},grid_size_m,origin_region,destination_region,ride_count"
    for name, where in (("od_windows", "origin_region!=destination_region AND window!='OTHER'"),
                        ("within_region_windows", "origin_region=destination_region AND window!='OTHER'"),
                        ("outside_windows", "window='OTHER'")):
        export_query(db, f"SELECT {time_cols} FROM od WHERE {where} ORDER BY local_date,window,grid_size_m,origin_region,destination_region", out / (name+".parquet"))
    export_query(db, f"SELECT {gv},'EPSG:32618' AS crs,0 AS origin_x,0 AS origin_y,* FROM regions ORDER BY grid_size_m,region_id", out/"regions.parquet")
    export_query(db, f"SELECT {gv},* FROM endpoints ORDER BY grid_size_m,lon,lat", out/"endpoint_region_map.parquet")
    export_query(db, f"SELECT {gv},* FROM stations ORDER BY grid_size_m,station_id,lon,lat", out/"station_region_observations.parquet")
    export_query(db, "SELECT * FROM hourly ORDER BY local_date,hour", out/"hourly_counts.parquet")
    export_query(db, "SELECT r.seq,f.source_month,f.archive,f.member,r.source_row,r.ride_id,d.outcome FROM raw r JOIN fragments f ON f.id=r.fragment_id JOIN disposition d USING(seq) WHERE d.outcome!='valid' ORDER BY r.seq", out/"excluded_records.parquet")
    quality = [dict(zip(("source_month", "outcome", "count"), row)) for row in db.execute("SELECT * FROM quality ORDER BY source_month,outcome")]
    write_csv(out/"quality_monthly.csv", quality, ["source_month", "outcome", "count"])
    flags = [dict(zip(("source_month", "flag", "count"), row)) for row in db.execute("SELECT * FROM flags ORDER BY source_month,flag")]
    write_csv(out/"quality_flags.csv", flags, ["source_month", "flag", "count"])
    rows = coverage_rows(db, config, load_coverage(config.get("coverage_file"), config["year"]))
    write_csv(out/"date_coverage.csv", rows, list(rows[0]))
    write_csv(out/"coverage_review_template.csv",
              [{"local_date": r["local_date"], "window": w, "status": "unknown", "reason": "Not reviewed"} for r in rows for w in WINDOWS],
              ["local_date", "window", "status", "reason"])
    write_csv(out/"coverage_exceptions.csv", [], ["local_date", "window", "grid_version", "grid_size_m", "region_id", "status", "reason"])
    json_write(out/"source_manifest.json", entries)
    json_write(out/"fragment_manifest.json", fragments)
    # Required accounting assertions, per source month and per scale.
    raw = dict(db.execute("SELECT f.source_month,COUNT(*) FROM raw r JOIN fragments f ON f.id=r.fragment_id GROUP BY f.source_month"))
    totals = dict(db.execute("SELECT source_month,SUM(count) FROM quality GROUP BY source_month"))
    if raw != totals or db.execute("SELECT COUNT(*) FROM disposition WHERE outcome='pending'").fetchone()[0]:
        raise AssertionError(f"Quality accounting failed: {raw} != {totals}")
    n_valid = db.execute("SELECT COUNT(*) FROM disposition WHERE outcome='valid'").fetchone()[0]
    for size in config["grid_sizes_m"]:
        total = db.execute("SELECT COALESCE(SUM(ride_count),0) FROM od WHERE grid_size_m=?", (size,)).fetchone()[0]
        if total != n_valid:
            raise AssertionError(f"OD conservation failed at {size}m: {total} != {n_valid}")
    summary = {"raw_rows": sum(raw.values()), "valid_rides": n_valid, "raw_rows_by_source_month": raw,
               "quality": quality, "all_selected_csvs_imported": True,
               "collection_coverage_review_required": not bool(config.get("coverage_file")),
               "local_coverage_exceptions_reviewed": False,
               "scope": "All selected archives; not necessarily full-year coverage"}
    json_write(out/"summary.json", summary)
    return summary


def run_pipeline(args):
    started = time.perf_counter()
    config = {"year": args.year, "months": sorted(set(args.months)), "batch_size": args.batch_size,
              "grid_sizes_m": [500, 1000], "grid_version": GRID_VERSION, "crs": "EPSG:32618",
              "origin": [0, 0], "timezone": "America/New_York", "windows": WINDOWS,
              "coordinate_envelope": args.bbox, "coverage_file": str(args.coverage.resolve()) if args.coverage else None,
              "holidays": args.holiday, "holiday_policy": "Keep weekday/weekend; holidays only flagged",
              "deduplication": "Global string ride_id; identical normalized 13 fields collapse; conflicting ID quarantined entirely",
              "dst_policy": "Naive nonexistent/ambiguous local timestamps quarantined; offset-aware timestamps converted",
              "split": {"development": "01-09", "holdout": "10-12"},
              "sample": False}
    if args.coverage:
        load_coverage(args.coverage, args.year)
        config["coverage_sha256"] = sha256_file(args.coverage)
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    out = args.output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + digest[:8])
    out.mkdir(parents=True, exist_ok=False)
    json_write(out/"config.json", config)
    json_write(out/"run_status.json", {"status": "running", "started_at": utc_now()})
    db = None
    try:
        entries = [download_month(args.year, m, args.raw_dir, args.offline) for m in config["months"]]
        json_write(out/"source_manifest.json", entries)
        download_seconds = time.perf_counter() - started
        db = sqlite3.connect(out/"audit.sqlite")
        init_db(db)
        fragments = ingest(db, entries, args.batch_size)
        json_write(out/"fragment_manifest.json", fragments)
        if args.command == "audit":
            json_write(out/"summary.json", {"mode": "audit_only", "raw_rows": db.execute("SELECT COUNT(*) FROM raw").fetchone()[0]})
        else:
            global_deduplicate(db)
            process(db, config)
            export_outputs(db, out, config, entries, fragments)
        elapsed = time.perf_counter() - started
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        metadata = {"status": "complete", "command": sys.argv, "config_sha256": digest,
                    "script_sha256": sha256_file(__file__), "finished_at": utc_now(),
                    "download_verify_seconds": download_seconds, "processing_seconds": elapsed-download_seconds,
                    "total_seconds": elapsed, "peak_rss_bytes": peak if sys.platform == "darwin" else peak*1024,
                    "python": sys.version, "pandas": pd.__version__, "numpy": np.__version__, "pyarrow": pa.__version__}
        json_write(out/"run_status.json", metadata)
        (out/"README.md").write_text(OUTPUT_README, encoding="utf-8")
        LOG.info("Completed: %s", out.resolve())
        return out
    except Exception as exc:
        if db is not None:
            db.commit()
            rows = [dict(zip(["id", "source_month", "archive", "member", "rows", "status"], r)) for r in db.execute("SELECT * FROM fragments")]
            json_write(out/"fragment_manifest.json", rows)
        json_write(out/"run_status.json", {"status": "failed", "error": repr(exc), "at": utc_now()})
        raise
    finally:
        if db is not None:
            db.close()


OUTPUT_README = """# Citi Bike preprocessing output

Only use a directory whose run_status.json says complete. audit_only outputs are
source audits, not cleaned OD data. These files cover the selected source months;
read summary.json and fragment_manifest.json before claiming full-year coverage.

- od_windows.parquet: sparse positive counts for inter-region AM/MID/PM trips.
- within_region_windows.parquet: same-region counts, excluded from tidal mining.
- outside_windows.parquet: valid trips outside the three study windows.
- date_coverage.csv: all calendar dates, weekday/weekend, split and per-window status.
- regions.parquet: grid IDs, parent IDs, UTM metre bounds, WGS84 centres.
- endpoint_region_map.parquet / station_region_observations.parquet: observed mappings.
- hourly_counts.parquet: all valid rides by local departure date/hour, one scale only.
- source_manifest.json / fragment_manifest.json: archive hash and every CSV's rows.
- quality_monthly.csv: exclusive terminal outcomes; valid + exclusions = raw rows.
- quality_flags.csv: overlapping flags among unique, non-conflicting candidate rows.
- excluded_records.parquet: all excluded raw record source references and reasons.
- audit.sqlite: raw JSON arrays in FIELDS order, source rows, dispositions and aggregates.
- config.json / run_status.json / summary.json: exact settings, resources and checks.

Read Parquet with pandas.read_parquet(). grid_version + grid_size_m + local_date +
window + origin_region + destination_region is the OD key; never sum across scales.
region IDs sort lexicographically, consistently, to define corridor A/B. 500m IDs
have a 1000m parent; map both endpoints before doing cross-scale comparisons.

IMPORTANT: import_complete is not collection completeness. Window statuses default
to unknown. Review hourly_counts, all archive members and external outage evidence;
fill coverage_review_template.csv (local_date,window,status,reason) and rerun using
--coverage PATH. Never mark complete merely because a window contains rides.
coverage_exceptions.csv is an EMPTY TEMPLATE for local coverage review, not evidence
that every region was active. Downstream must apply reviewed local exceptions too.
Only missing OD rows on reviewed observable dates/regions can be interpreted as zero.
Low flow and tied flow remain in the date denominator. No mining thresholds were
applied in this preprocessing. Never infer personal round trips or bike shortages.

Coordinate envelope default [-75,39,-72,42] is a broad anomaly quarantine, NOT the
NYC administrative boundary; adjust only with documented source evidence. Projection
is fixed WGS84 UTM 18N, origin (0,0), 500/1000m half-open cells. IDs never use monthly
minima. The script uses the standard local UTM series, validated in self-test.
Stations are mapped per observed endpoint, not one permanent location per station.
Missing station names/IDs with valid coordinates are retained; coordinates are not
imputed. Naive DST-ambiguous timestamps are quarantined instead of guessing duration.

Source row numbers are CSV record numbers including header, not physical line numbers
when fields contain embedded newlines. Raw IDs are read as strings and stripped only
of outer whitespace. Fingerprints cover all 13 normalized fields; conflicts remove
ALL records of that ID, including the earlier one. Deduplication is global only over
this run's selected months: when adding months, rerun the combined selection. Raw ZIPs
are reused and outputs never append counts to an earlier run.

Trace a count by filtering audit.sqlite's raw rows joined to disposition on seq
(outcome='valid'), parsing payload, and applying config's time/grid mapping. The raw
archive, member and record number are available via fragments. Retain this SQLite
file for provenance. Full-year disk/CPU use depends on data volume and selected input;
the implementation trades disk for bounded batch memory and has not benchmarked a
year on your hardware. Partial failed run directories have status failed and MUST
NOT be handed to mining as complete outputs.
"""


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["download", "audit", "run", "self-test"])
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--months", type=int, nargs="+", default=list(range(1, 13)))
    p.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    p.add_argument("--output", type=Path, default=Path("data/processed"))
    p.add_argument("--offline", action="store_true", help="Use verified local ZIPs only")
    p.add_argument("--batch-size", type=int, default=50000)
    p.add_argument("--coverage", type=Path, help="Reviewed local_date,window,status,reason CSV")
    p.add_argument("--holiday", action="append", default=[], help="ISO date to flag; does not alter weekday definition")
    p.add_argument("--bbox", type=float, nargs=4, default=[-75, 39, -72, 42], metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.command == "self-test":
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(PreprocessingTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.year != 2025 or not args.months or any(m < 1 or m > 12 for m in args.months) or args.batch_size < 1:
        p.error("This project requires year=2025, months 1..12 and positive batch-size")
    w, s, e, n = args.bbox
    if not (-75 <= w < e <= -72 and 39 <= s < n <= 42):
        p.error("Envelope must remain within the supported local UTM domain [-75,39,-72,42]")
    for holiday in args.holiday:
        if date.fromisoformat(holiday).year != args.year:
            p.error("Holiday dates must belong to study year")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "download":
        entries = [download_month(args.year, m, args.raw_dir, args.offline) for m in sorted(set(args.months))]
        json_write(args.raw_dir/"download_manifest.json", entries)
    else:
        run_pipeline(args)
    return 0


# Tests are defined below; self-test never downloads real trip archives.

class PreprocessingTests(unittest.TestCase):
    @staticmethod
    def ride(rid="0001", start="2025-01-02 07:00:00", end="2025-01-02 07:10:00", reverse=False, **changes):
        row = dict(zip(FIELDS, [rid, "classic_bike", start, end, "", "0012", "Destination", "0034",
                               "40.75", "-73.99", "40.78", "-73.95", "member"]))
        if reverse:
            for a, b in (("start_lat", "end_lat"), ("start_lng", "end_lng")):
                row[a], row[b] = row[b], row[a]
        row.update(changes)
        return row

    @staticmethod
    def make_zip(path, members):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, rows in members.items():
                text = io.StringIO(newline="")
                writer = csv.DictWriter(text, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
                z.writestr(name, text.getvalue())
            z.writestr("__MACOSX/._ignored.csv", "metadata")

    def test_time_windows_and_dst(self):
        self.assertEqual([clock_window(h) for h in (6, 7, 9, 10, 11, 13, 14, 16, 18, 19)],
                         ["OTHER", "AM", "AM", "OTHER", "MID", "MID", "OTHER", "PM", "PM", "OTHER"])
        self.assertIsNone(localize("2025-03-09 02:30:00")[0])
        self.assertTrue(localize("2025-11-02 01:30:00")[1])
        self.assertEqual(localize("2025-01-02T12:00:00Z")[0].hour, 7)
        rows = [self.ride(), self.ride("2", start="bad"),
                self.ride("3", end="2025-01-02 06:59:59"),
                self.ride("4", start="2025-09-30 23:59:00", end="2025-10-01 00:10:00"),
                self.ride("5", start="2025-10-01 07:00:00", end="2025-10-01 07:01:00"),
                self.ride("6", start_lat=""), self.ride("7", start_lng="-73.0", start_lat="0"),
                self.ride("8", start_lat="35"),
                self.ride("9", start="2025-11-02 01:30:00", end="2025-11-02 02:30:00"),
                self.ride("10", start="2025-01-02T12:00:00Z", end="2025-01-02T12:10:00Z")]
        valid, reasons, flags = clean_frame(pd.DataFrame(rows), 2025, [-75, 39, -72, 42])
        self.assertEqual(reasons.tolist(), ["valid", "invalid_time", "nonpositive_duration", "valid", "valid",
                                           "invalid_endpoint", "invalid_endpoint", "outside_coordinate_envelope", "ambiguous_time", "valid"])
        self.assertEqual(valid.iloc[1].local_date, "2025-09-30")
        self.assertEqual(valid.iloc[1].split, "development")
        self.assertEqual(valid.iloc[2].split, "holdout")
        self.assertEqual(valid.iloc[-1].window, "AM")
        self.assertEqual(flags["missing_start_station_name"], 10)

    def test_projection_and_nested_grid(self):
        # USGS Bulletin 1532 example: zone 18, lon -73.5°, lat 40.5°.
        x, y = project_utm18([-73.5, -75], [40.5, 0])
        self.assertAlmostEqual(x[0], 627103.087, delta=.02)
        self.assertAlmostEqual(y[0], 4484335.402, delta=.02)
        self.assertAlmostEqual(x[1], 500000, places=6)
        self.assertAlmostEqual(y[1], 0, places=6)
        lon, lat = inverse_utm18(x, y)
        np.testing.assert_allclose(lon, [-73.5, -75], atol=1e-9)
        np.testing.assert_allclose(lat, [40.5, 0], atol=1e-9)
        points = np.array([-1000.01, -1000, -.001, 0, 499.99, 500, 999.99, 1000])
        np.testing.assert_array_equal(grid_index(points, 500)//2, grid_index(points, 1000))
        np.testing.assert_array_equal(grid_index([499.99, 500, 1000], 500), [0, 1, 2])

    def test_download_mocked_http_and_reuse(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            fixture = temp/"fixture.zip"
            self.make_zip(fixture, {"part1.csv": [self.ride()]})
            payload = fixture.read_bytes()
            class Response(io.BytesIO):
                headers = {"Content-Length": str(len(payload)), "ETag": "test-version"}
            calls = []
            def opener(url):
                calls.append(url)
                if not url.endswith("-csv.zip"):
                    raise HTTPError(url, 404, "missing", {}, None)
                return Response(payload)
            result = download_month(2025, 1, temp/"raw", opener=opener)
            self.assertEqual(len(calls), 3)
            self.assertTrue(result["url"].endswith("-csv.zip"))
            self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())
            again = download_month(2025, 1, temp/"raw", offline=True)
            self.assertEqual(again["sha256"], result["sha256"])
            # Invalid content-length retries then fails, never publishing a ZIP.
            class ShortResponse(io.BytesIO):
                headers = {"Content-Length": str(len(payload)+1)}
            with self.assertRaises(OSError), patch("time.sleep"):
                download_month(2025, 2, temp/"raw", retries=2, opener=lambda u: ShortResponse(payload))
            self.assertFalse((temp/"raw/202502-citibike-tripdata.csv.zip").exists())

    def test_schema_rejection_and_jc_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"bad.zip"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("bad.csv", "ride_id,ride_id\na,a\n")
            db = sqlite3.connect(":memory:")
            init_db(db)
            with self.assertRaises(ValueError):
                ingest(db, [{"local_path": str(path), "source_month": "2025-01", "csv_members": ["bad.csv"]}], 2)
            db.close()
            self.make_zip(path, {"JC-202501.csv": [self.ride()]})
            with self.assertRaises(ValueError):
                validate_zip(path)

    def test_end_to_end_multifragment_global_dedup(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            raw = temp/"raw"
            raw.mkdir()
            rows = []
            rid = 0
            for window, counts in (("AM", (18, 6)), ("PM", (5, 15))):
                hour = "07" if window == "AM" else "16"
                for reverse, n in enumerate(counts):
                    for _ in range(n):
                        rid += 1
                        rows.append(self.ride(f"{rid:06d}", start=f"2025-01-02 {hour}:00:00",
                                              end=f"2025-01-02 {hour}:10:00", reverse=bool(reverse)))
            # Region-internal ride, boundary, bad endpoint, exact duplicate and conflicts.
            rows += [self.ride("internal", end_lat="40.75", end_lng="-73.99"),
                     self.ride("other", start="2025-01-02 10:00:00", end="2025-01-02 10:10:00"),
                     self.ride("bad", start_lat="nan"), self.ride("", start_station_id="0007"),
                     self.ride("conflict"), self.ride("monthconflict"), rows[0].copy()]
            jan = raw/"202501-citibike-tripdata.csv.zip"
            self.make_zip(jan, {"nested/part1.csv": rows[:23], "nested/part2.csv": rows[23:] + [self.ride("conflict", member_casual="casual")]})
            july = raw/"202507-citibike-tripdata-csv.zip"
            self.make_zip(july, {"july.csv": [self.ride("monthconflict", member_casual="casual"),
                                              self.ride("july", start="2025-07-03 07:00:00", end="2025-07-03 07:10:00")]})
            coverage = temp/"review.csv"
            write_csv(coverage, [{"local_date": "2025-01-02", "window": w, "status": "complete", "reason": "synthetic fixture has known full coverage"} for w in WINDOWS],
                      ["local_date", "window", "status", "reason"])
            args = parser().parse_args(["run", "--months", "1", "7", "--offline", "--raw-dir", str(raw),
                                       "--output", str(temp/"out"), "--batch-size", "7", "--coverage", str(coverage)])
            out = run_pipeline(args)
            summary = json.loads((out/"summary.json").read_text())
            self.assertEqual(summary["valid_rides"], 47)
            self.assertEqual(summary["raw_rows"], 54)
            quality = pd.read_csv(out/"quality_monthly.csv").groupby("outcome")["count"].sum().to_dict()
            self.assertEqual(quality, {"valid": 47, "conflict_duplicate": 4, "exact_duplicate": 1,
                                       "missing_id": 1, "invalid_endpoint": 1})
            od = pd.read_parquet(out/"od_windows.parquet")
            for size in (500, 1000):
                f = od[(od.grid_size_m == size) & (od.local_date == "2025-01-02")]
                self.assertEqual(sorted(f.ride_count.tolist()), [5, 6, 15, 18])
            self.assertEqual(len(json.loads((out/"fragment_manifest.json").read_text())), 3)
            dates = pd.read_csv(out/"date_coverage.csv")
            self.assertEqual(len(dates), 365)
            self.assertEqual(dates.loc[dates.local_date == "2025-01-02", "AM_status"].iloc[0], "complete")
            self.assertEqual(dates.loc[dates.local_date == "2025-07-03", "AM_status"].iloc[0], "unknown")
            self.assertEqual(dates.loc[dates.local_date == "2025-10-01", "split"].iloc[0], "holdout")
            station = pd.read_parquet(out/"station_region_observations.parquet")
            self.assertIn("0012", station.station_id.values)
            regions = pd.read_parquet(out/"regions.parquet")
            children = regions[regions.grid_size_m == 500]
            for r in children.itertuples():
                self.assertEqual(r.parent_region, region_id(1000, r.ix//2, r.iy//2))
                self.assertIn(r.parent_region, set(regions.region_id))
            # Rerun goes to another output and produces the same positive OD counts.
            args.batch_size = 11
            again = run_pipeline(args)
            pd.testing.assert_frame_equal(od, pd.read_parquet(again/"od_windows.parquet"))
            self.assertNotEqual(out, again)

    def test_empty_valid_output_and_coverage_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            self.make_zip(temp/"202501-citibike-tripdata.csv.zip", {"empty.csv": [self.ride("bad", end_lat="")]})
            args = parser().parse_args(["run", "--months", "1", "--offline", "--raw-dir", str(temp), "--output", str(temp/"out")])
            out = run_pipeline(args)
            self.assertTrue(pd.read_parquet(out/"od_windows.parquet").empty)
            self.assertEqual(pd.read_parquet(out/"od_windows.parquet").ride_count.dtype.kind, "i")
            db = sqlite3.connect(out/"audit.sqlite")
            with self.assertRaises(ValueError):
                coverage_rows(db, {"year": 2025, "holidays": []}, {("2025-02-01", "AM"): {"status": "complete", "reason": "bad"}})
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
