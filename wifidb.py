#!/usr/bin/env python3
"""wifidb — log internet speed + location per spot into SQLite.

Subcommands:
  record   run a speedtest + capture location, insert a row (default)
  resolve  fill in the cafe/place name via Google Maps (agent-browser)
  list     show recent records
  map      open a record's location in Google Maps
  stats    average speeds grouped by place
"""
import argparse
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WIFIDB_DB", os.path.join(HERE, "wifi.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT,
    lat           REAL,
    lon           REAL,
    accuracy      REAL,
    address       TEXT,
    ssid          TEXT,
    bssid         TEXT,
    place         TEXT,
    place_lat     REAL,
    place_lon     REAL,
    download_mbps REAL,
    upload_mbps   REAL,
    ping_ms       REAL,
    jitter_ms     REAL,
    loss_pct      REAL,
    isp           TEXT,
    is_vpn        INTEGER,
    ext_ip        TEXT,
    exit_loc      TEXT,
    exit_lat      REAL,
    exit_lon      REAL,
    server        TEXT,
    result_url    TEXT,
    raw_json      TEXT
);
"""

# Columns added after the initial release; applied to existing DBs via _migrate.
_ADDED_COLUMNS = [
    ("exit_loc", "TEXT"), ("exit_lat", "REAL"), ("exit_lon", "REAL"),
    ("ssid", "TEXT"), ("bssid", "TEXT"),
]

COLUMNS = [
    "ts", "lat", "lon", "accuracy", "address", "ssid", "bssid",
    "place", "place_lat", "place_lon",
    "download_mbps", "upload_mbps", "ping_ms", "jitter_ms", "loss_pct",
    "isp", "is_vpn", "ext_ip", "exit_loc", "exit_lat", "exit_lon",
    "server", "result_url", "raw_json",
]


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def db_connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    """Add columns introduced after the first schema to pre-existing DBs."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
    for name, decl in _ADDED_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE records ADD COLUMN {name} {decl}")
    conn.commit()


def insert_record(conn, row):
    cols = [c for c in COLUMNS if c in row]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO records ({', '.join(cols)}) VALUES ({placeholders})"
    cur = conn.execute(sql, [row[c] for c in cols])
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# Pure parsers (unit-tested)
# --------------------------------------------------------------------------- #
def _mbps(bandwidth_bytes_per_sec):
    """Ookla reports bandwidth in bytes/sec; convert to Mbps."""
    if not bandwidth_bytes_per_sec:
        return None
    return round(bandwidth_bytes_per_sec * 8 / 1_000_000, 2)


def parse_speedtest(j):
    """Map `speedtest -f json` output to a row dict (place left unset)."""
    ping = j.get("ping", {})
    server = j.get("server", {})
    iface = j.get("interface", {})
    res = j.get("result", {})
    server_str = " — ".join(
        x for x in (server.get("name"), server.get("location")) if x
    ) or None
    return {
        "ts": j.get("timestamp"),
        "download_mbps": _mbps(j.get("download", {}).get("bandwidth")),
        "upload_mbps": _mbps(j.get("upload", {}).get("bandwidth")),
        "ping_ms": ping.get("latency"),
        "jitter_ms": ping.get("jitter"),
        "loss_pct": j.get("packetLoss"),
        "isp": j.get("isp"),
        "is_vpn": 1 if iface.get("isVpn") else 0,
        "ext_ip": iface.get("externalIp"),
        "server": server_str,
        "result_url": res.get("url"),
        "raw_json": json.dumps(j, separators=(",", ":"), ensure_ascii=False),
    }


def parse_ipinfo(j):
    """Map ipinfo.io JSON to exit-location fields (where traffic egressed)."""
    if not j or j.get("bogon"):
        return {}
    city, cc, org = j.get("city"), j.get("country"), j.get("org")
    label = ", ".join(p for p in (city, cc) if p)
    if org:
        label = f"{label} — {org}" if label else org
    lat = lon = None
    loc = j.get("loc")
    if loc and "," in loc:
        try:
            la, lo = loc.split(",", 1)
            lat, lon = float(la), float(lo)
        except ValueError:
            pass
    return {"exit_loc": label or None, "exit_lat": lat, "exit_lon": lon}


def parse_wifi(text):
    """Parse SSID/BSSID from `ipconfig getsummary <iface>` output.

    The BSSID (access-point MAC) is the grouping key for "same Wi-Fi". macOS
    only fills these when Location Services is authorized.
    """
    ssid = bssid = None
    for line in (text or "").splitlines():
        m = re.match(r"\s*SSID\s*:\s*(.+?)\s*$", line)
        if m:
            ssid = m.group(1)
            continue
        m = re.match(r"\s*BSSID\s*:\s*(.+?)\s*$", line)
        if m:
            bssid = m.group(1)

    def clean(v):
        # macOS returns "<redacted>" unless the process has Location Services
        # authorization, so drop placeholders rather than store/group on them.
        if not v or v.lower() in ("none", "<none>") or "redact" in v.lower():
            return None
        return v

    return {"ssid": clean(ssid), "bssid": clean(bssid)}


def parse_location(out):
    """Parse the `|`-delimited CoreLocationCLI --format output.

    Format string used: '%latitude|%longitude|%h_accuracy|%address'
    """
    parts = (out or "").strip().split("|")

    def at(i):
        return parts[i].strip() if i < len(parts) else ""

    def num(s):
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    addr = at(3)
    return {
        "lat": num(at(0)),
        "lon": num(at(1)),
        "accuracy": num(at(2)),
        "address": " ".join(addr.split()) or None,  # collapse newlines/spaces
    }


# --------------------------------------------------------------------------- #
# agent-browser snapshot parsing (unit-tested)
# --------------------------------------------------------------------------- #
_ARTICLE_RE = re.compile(r'^\s*-?\s*article "(.*?)" \[ref=(e\d+)\]')
_LINK_RE = re.compile(r'link "(.*?)" \[ref=(e\d+)\]')
_BUTTON_RE = re.compile(r'button "(.*?)" \[(?:.*?)ref=(e\d+)\]')
_SPONSORED = ("Patrocinado", "Sponsored", "Anuncio", "Anúncio")


def find_ref(snapshot, labels):
    """Return '@eN' for the first button matching any of `labels` (substring)."""
    for line in snapshot.splitlines():
        m = _BUTTON_RE.search(line)
        if not m:
            continue
        text = m.group(1)
        if any(lbl in text for lbl in labels):
            return "@" + m.group(2)
    return None


def first_organic(snapshot):
    """Return (name, '@eN' link-ref) of the first non-sponsored result article."""
    lines = snapshot.splitlines()
    blocks = []  # (name, start_idx)
    for i, line in enumerate(lines):
        m = _ARTICLE_RE.match(line)
        if m:
            blocks.append((m.group(1), m.group(2), i))
    for bi, (name, art_ref, start) in enumerate(blocks):
        end = blocks[bi + 1][2] if bi + 1 < len(blocks) else len(lines)
        body = "\n".join(lines[start:end])
        if any(s in body for s in _SPONSORED):
            continue
        # prefer the link ref inside the block; fall back to article ref
        link_ref = art_ref
        for line in lines[start:end]:
            lm = _LINK_RE.search(line)
            if lm and lm.group(1) == name:
                link_ref = lm.group(2)
                break
        return name, "@" + link_ref
    return None, None


def parse_place_coords(url):
    """Extract (lat, lon) from a Google Maps place URL (`!3d..!4d..`)."""
    m = re.search(r"!3d(-?[0-9.]+)!4d(-?[0-9.]+)", url or "")
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def parse_place_address(snapshot):
    """Extract the street address from a place panel snapshot."""
    m = re.search(r'(?:Endereço|Address|Adresse|Dirección):\s*(.*?)\s*"', snapshot)
    return " ".join(m.group(1).split()) if m else None


_SEARCH_LINK_RE = re.compile(r'link "(.*?)" \[ref=(e\d+), url=([^\]]+)\]')


def parse_search_candidates(snapshot):
    """Parse {name, lat, lon, ref} for each place link in a `-u` search snapshot.

    Google embeds each result's coordinates in its place-link href, so the whole
    candidate set (with locations) comes from a single snapshot — no clicking.
    """
    out, seen = [], set()
    for line in snapshot.splitlines():
        m = _SEARCH_LINK_RE.search(line)
        if not m:
            continue
        name, ref, url = m.group(1), m.group(2), m.group(3)
        if "/maps/place/" not in url:
            continue
        plat, plon = parse_place_coords(url)
        if plat is None:
            continue
        key = (round(plat, 6), round(plon, 6))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "lat": plat, "lon": plon, "ref": "@" + ref})
    return out


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points, in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_candidate(lat, lon, candidates):
    """Return the candidate closest to (lat, lon) by real distance, or None.

    This is the fix for picking Google's top *relevance*-ranked result instead
    of the venue actually at the fix: rank by distance, not list order.
    """
    best = None
    for c in candidates:
        d = haversine(lat, lon, c["lat"], c["lon"])
        if best is None or d < best[0]:
            best = (d, c)
    return best[1] if best else None


# --------------------------------------------------------------------------- #
# External tool wrappers
# --------------------------------------------------------------------------- #
def run_speedtest():
    proc = subprocess.run(
        ["speedtest", "-f", "json", "--accept-license", "--accept-gdpr"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"speedtest failed:\n{proc.stderr.strip()}")
    # speedtest prints license banners before the JSON; grab the JSON line.
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError("speedtest produced no JSON output")


def get_location():
    fmt = "%latitude|%longitude|%h_accuracy|%address"
    proc = subprocess.run(
        ["CoreLocationCLI", "--format", fmt],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(
            "warning: location unavailable (" + proc.stderr.strip() + ")\n"
        )
        return {"lat": None, "lon": None, "accuracy": None, "address": None}
    return parse_location(proc.stdout)


def geolocate_ip(ip, timeout=8):
    """Look up the geographic location of an IP (the connection's exit point)."""
    if not ip:
        return {}
    try:
        req = urllib.request.Request(
            f"https://ipinfo.io/{ip}/json", headers={"User-Agent": "wifidb/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse_ipinfo(json.loads(r.read().decode()))
    except Exception as e:  # noqa: BLE001 - best-effort enrichment
        sys.stderr.write(f"warning: IP geolocation failed: {e}\n")
        return {}


def get_wifi(iface="en0"):
    proc = subprocess.run(
        ["ipconfig", "getsummary", iface], capture_output=True, text=True
    )
    if proc.returncode != 0:
        return {"ssid": None, "bssid": None}
    return parse_wifi(proc.stdout)


def _ab(*args):
    """Run an agent-browser command, return stdout (stripped)."""
    proc = subprocess.run(
        ["agent-browser", *args], capture_output=True, text=True,
    )
    return proc.stdout.strip()


def rank_candidates(lat, lon, candidate_lists, limit=10):
    """Merge candidate lists, dedupe by location, sort by distance to the fix."""
    by_key = {}
    for lst in candidate_lists:
        for c in lst:
            key = (round(c["lat"], 6), round(c["lon"], 6))
            if key in by_key:
                continue
            by_key[key] = {**c, "dist": round(haversine(lat, lon, c["lat"], c["lon"]))}
    return sorted(by_key.values(), key=lambda c: c["dist"])[:limit]


def fetch_candidates(lat, lon, queries=("cafe", "restaurant"), settle=3.0, limit=10):
    """Search Google Maps across categories near the fix; return nearest venues.

    Coordinates are read straight from each result's place-link href, so the
    whole candidate set comes from snapshots — no per-result clicking. Multiple
    categories are merged so cafés, bistros and restaurants all surface.
    """
    lists = []
    for i, q in enumerate(queries):
        url = f"https://www.google.com/maps/search/{q}/@{lat},{lon},18z"
        _ab("open", url)
        time.sleep(settle)
        snap = _ab("snapshot", "-i", "-u")
        if i == 0 and ("consent.google.com" in _ab("get", "url")
                       or find_ref(snap, ["Rejeitar tudo", "Reject all"])):
            ref = find_ref(snap, ["Rejeitar tudo", "Reject all",
                                  "Aceitar tudo", "Accept all"])
            if ref:
                _ab("click", ref)
                time.sleep(settle)
                _ab("wait", "--load", "networkidle")
            _ab("open", url)
            time.sleep(settle)
            _ab("wait", "--load", "networkidle")
            snap = _ab("snapshot", "-i", "-u")
        lists.append(parse_search_candidates(snap))
    return rank_candidates(lat, lon, lists, limit=limit)


def resolve_place(lat, lon, queries=("cafe", "restaurant")):
    """Auto-pick the venue nearest the coords (non-interactive)."""
    cands = fetch_candidates(lat, lon, queries=queries)
    if not cands:
        return None
    best = cands[0]
    return {
        "place": best["name"], "place_lat": best["lat"], "place_lon": best["lon"],
        "address": None, "distance_m": best["dist"],
    }


def _fmt_candidate(c, width=30):
    dist = c.get("dist")
    dtxt = f"{dist} m" if dist is not None else ""
    return f"{c['name'][:width]:<{width}} {dtxt:>8}"


def _render_candidates(candidates, idx, typed):
    w = sys.stdout
    if typed:
        w.write(f"\x1b[2KWhere are you?  name: {typed}█  "
                "(Enter = use typed · Backspace = edit)\r\n")
    else:
        w.write("\x1b[2KWhere are you?  ↑/↓ move · Enter confirm · "
                "or type a name\r\n")
    for i, c in enumerate(candidates):
        marker = "❯" if (i == idx and not typed) else " "
        w.write(f"\x1b[2K {marker} {_fmt_candidate(c)}\r\n")
    w.flush()


def select_place(candidates, default_idx=0):
    """Arrow-key picker over nearby places.

    Returns the chosen candidate dict, a {'name','lat','lon'} dict for a typed
    custom name, or None if aborted. When not attached to a TTY, prints the list
    and auto-selects the default (nearest) so non-interactive runs still name it.
    """
    if not candidates:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("Nearby places (auto-selecting nearest — run in a terminal to pick):")
        for i, c in enumerate(candidates):
            print(f"  {i + 1:>2}. {_fmt_candidate(c)} {'<- default' if i == default_idx else ''}")
        return candidates[default_idx]

    import termios
    import tty
    idx, typed, first = default_idx, "", True
    nlines = len(candidates) + 1
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            if not first:
                sys.stdout.write(f"\x1b[{nlines}A")
            first = False
            _render_candidates(candidates, idx, typed)
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                if typed.strip():
                    return {"name": typed.strip(), "lat": None, "lon": None}
                return candidates[idx]
            if ch == "\x03":              # Ctrl-C
                return None
            if ch == "\x1b":              # arrow keys / bare ESC
                seq = sys.stdin.read(2)
                if seq == "[A" and not typed:
                    idx = (idx - 1) % len(candidates)
                elif seq == "[B" and not typed:
                    idx = (idx + 1) % len(candidates)
                elif seq not in ("[A", "[B"):
                    return None           # bare ESC aborts
            elif ch in ("\x7f", "\b"):    # backspace
                typed = typed[:-1]
            elif ch.isprintable():
                typed += ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\n")
        sys.stdout.flush()


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_record(args):
    conn = db_connect()
    sys.stderr.write("running speedtest…\n")
    st = run_speedtest()
    row = parse_speedtest(st)
    loc = get_location()
    row.update({k: loc.get(k) for k in ("lat", "lon", "accuracy", "address")})
    row["place"] = None
    if row.get("ext_ip"):
        row.update(geolocate_ip(row["ext_ip"]))
    row.update(get_wifi())
    rid = insert_record(conn, row)  # write immediately — never lose the data
    coords = (
        f"{row['lat']:.6f},{row['lon']:.6f}" if row["lat"] is not None else "n/a"
    )
    sys.stderr.write(
        f"recorded #{rid} — {row['download_mbps']}↓/{row['upload_mbps']}↑ Mbps "
        f"@ {coords}\n"
    )

    # Name the spot inline: fetch nearby places, let the user pick, update the row.
    if row["lat"] is not None:
        try:
            cands = fetch_candidates(row["lat"], row["lon"])
        except Exception as e:  # noqa: BLE001 - enrichment, must not lose the row
            cands = []
            sys.stderr.write(f"(couldn't fetch nearby places: {e})\n")
        finally:
            _ab("close", "--all")
        choice = None
        if cands:
            try:
                choice = select_place(cands)
            except Exception as e:  # noqa: BLE001 - picker must never lose the row
                sys.stderr.write(f"(picker failed: {e}; name left unset)\n")
        else:
            sys.stderr.write("no nearby places found — name left unset.\n")
        if choice:
            conn.execute(
                "UPDATE records SET place=?, place_lat=?, place_lon=? WHERE id=?",
                (choice["name"], choice.get("lat"), choice.get("lon"), rid),
            )
            conn.commit()
            sys.stderr.write(f"#{rid}: → {choice['name']}\n")

    _print_rows(conn, where="id = ?", params=(rid,))
    return 0


def cmd_resolve(args):
    conn = db_connect()
    if args.id:
        ids = [args.id]
    elif args.all:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM records WHERE place IS NULL AND lat IS NOT NULL ORDER BY id"
        )]
    else:  # --last (default)
        r = conn.execute(
            "SELECT id FROM records WHERE lat IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        ids = [r["id"]] if r else []
    if not ids:
        sys.stderr.write("nothing to resolve.\n")
        return 1
    for rid in ids:
        row = conn.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone()
        if not row or row["lat"] is None:
            sys.stderr.write(f"#{rid}: no coordinates, skipping.\n")
            continue
        sys.stderr.write(f"#{rid}: resolving via Google Maps…\n")
        qs = (args.query,) if getattr(args, "query", None) else ("cafe", "restaurant")
        try:
            res = resolve_place(row["lat"], row["lon"], queries=qs)
        except Exception as e:  # noqa: BLE001 - best-effort browser step
            sys.stderr.write(f"#{rid}: resolve error: {e}\n")
            continue
        if not res:
            sys.stderr.write(f"#{rid}: no result found.\n")
            continue
        conn.execute(
            "UPDATE records SET place=?, place_lat=?, place_lon=?, "
            "address=COALESCE(?, address) WHERE id=?",
            (res["place"], res["place_lat"], res["place_lon"], res["address"], rid),
        )
        conn.commit()
        sys.stderr.write(f"#{rid}: → {res['place']} ({res['distance_m']} m from fix)\n")
    _ab("close", "--all")
    _print_rows(conn, where="id IN (%s)" % ",".join("?" * len(ids)), params=ids)
    return 0


def cmd_list(args):
    conn = db_connect()
    if getattr(args, "raw", False):
        # fastest connection first (NULL downloads sort last under DESC)
        _print_rows(conn, limit=getattr(args, "n", 15),
                    order_by="download_mbps DESC", reverse=False)
    else:
        _print_groups(conn, limit=getattr(args, "n", 15))
    return 0


def cmd_map(args):
    conn = db_connect()
    if args.id:
        row = conn.execute("SELECT * FROM records WHERE id=?", (args.id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM records WHERE lat IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        sys.stderr.write("no record found.\n")
        return 1
    lat = row["place_lat"] if row["place_lat"] is not None else row["lat"]
    lon = row["place_lon"] if row["place_lon"] is not None else row["lon"]
    if lat is None:
        sys.stderr.write(f"#{row['id']} has no coordinates.\n")
        return 1
    url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    subprocess.run(["open", url])
    print(url)
    return 0


def cmd_stats(args):
    conn = db_connect()
    rows = conn.execute(
        "SELECT COALESCE(place, '(pending)') AS place, COUNT(*) n, "
        "ROUND(AVG(download_mbps),1) dl, ROUND(AVG(upload_mbps),1) ul, "
        "ROUND(AVG(ping_ms),1) ping FROM records GROUP BY place ORDER BY n DESC"
    ).fetchall()
    if not rows:
        print("no records yet.")
        return 0
    print(f"{'place':<30} {'n':>3} {'avg↓':>8} {'avg↑':>8} {'ping':>6}")
    for r in rows:
        print(f"{(r['place'] or '')[:30]:<30} {r['n']:>3} "
              f"{r['dl'] or 0:>8} {r['ul'] or 0:>8} {r['ping'] or 0:>6}")
    return 0


def _print_groups(conn, limit=15):
    """One aggregated entry per location: by place name, else Wi-Fi BSSID, else
    a coarse GPS cell. So all measurements at a named spot collapse together."""
    rows = conn.execute(
        """
        SELECT MAX(bssid) bssid, MAX(ssid) ssid, COUNT(*) n,
               MAX(place) place, MAX(address) address,
               ROUND(AVG(download_mbps),1) dl_avg, ROUND(MAX(download_mbps),1) dl_best,
               ROUND(AVG(upload_mbps),1) ul_avg,
               ROUND(MIN(ping_ms)) ping_min, ROUND(MAX(ping_ms)) ping_max,
               SUM(is_vpn) vpn_n, MAX(ts) last_ts
        FROM records
        GROUP BY COALESCE(NULLIF(place, ''), bssid,
                          'gps:' || ROUND(lat, 4) || ',' || ROUND(lon, 4))
        ORDER BY dl_avg DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        print("(no records)")
        return
    print(f"{'place':<30} {'n':>2} {'↓avg':>6} {'↓best':>6} "
          f"{'↑avg':>6} {'ping':>9} {'vpn':>5} {'last':<11}")
    for r in rows:
        place = r["place"] or r["address"] or "(pending)"
        pmin, pmax = r["ping_min"] or 0, r["ping_max"] or 0
        ping = f"{pmin:.0f}" if pmin == pmax else f"{pmin:.0f}–{pmax:.0f}"
        vpn = f"{r['vpn_n']}/{r['n']}"
        last = (r["last_ts"] or "")[5:16].replace("T", " ")
        print(f"{place[:30]:<30} {r['n']:>2} "
              f"{r['dl_avg'] or 0:>6} {r['dl_best'] or 0:>6} {r['ul_avg'] or 0:>6} "
              f"{ping:>9} {vpn:>5} {last:<11}")


def _print_rows(conn, where="1=1", params=(), limit=15,
                order_by="id DESC", reverse=True):
    # `reverse` flips the fetched order for display: with the default id-DESC
    # selection it prints the newest N oldest-first (newest at the bottom).
    sql = (
        "SELECT id, ts, place, address, lat, lon, download_mbps, upload_mbps, "
        "ping_ms, isp, is_vpn, exit_loc FROM records WHERE " + where +
        " ORDER BY " + order_by + " LIMIT ?"
    )
    rows = conn.execute(sql, (*params, limit)).fetchall()
    if not rows:
        print("(no records)")
        return
    print(f"{'id':>3} {'when':<16} {'place':<22} {'↓Mbps':>7} {'↑Mbps':>7} "
          f"{'ping':>5} {'vpn':>3} {'exit (where it connected)':<30}")
    for r in (reversed(rows) if reverse else rows):
        when = (r["ts"] or "")[:16].replace("T", " ")
        place = r["place"] or (r["address"] or "(pending)")
        vpn = "yes" if r["is_vpn"] else "no"
        exit_loc = r["exit_loc"] or "—"
        print(f"{r['id']:>3} {when:<16} {place[:22]:<22} "
              f"{r['download_mbps'] or 0:>7} {r['upload_mbps'] or 0:>7} "
              f"{r['ping_ms'] or 0:>5.0f} {vpn:>3} {exit_loc[:30]:<30}")


def build_parser():
    p = argparse.ArgumentParser(prog="wifidb", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("record", help="run a speedtest + capture location")
    rs = sub.add_parser("resolve", help="name the place via Google Maps")
    rs.add_argument("id", nargs="?", type=int, help="record id")
    rs.add_argument("--last", action="store_true", help="resolve the latest record (default)")
    rs.add_argument("--all", action="store_true", help="resolve all pending records")
    rs.add_argument("--query", default=None,
                    help="single Google Maps category to search (default: cafe + restaurant)")
    ls = sub.add_parser("list", aliases=["ls"], help="show records grouped by Wi-Fi")
    ls.add_argument("-n", type=int, default=15, help="how many rows/groups")
    ls.add_argument("--raw", action="store_true",
                    help="show individual measurements instead of grouping by Wi-Fi")
    mp = sub.add_parser("map", help="open a record's location in Google Maps")
    mp.add_argument("id", nargs="?", type=int, help="record id (default: latest)")
    mp.add_argument("--last", action="store_true", help="latest record (default)")
    sub.add_parser("stats", help="average speeds grouped by place")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.cmd or "list"
    return {
        "record": cmd_record,
        "resolve": cmd_resolve,
        "list": cmd_list,
        "ls": cmd_list,
        "map": cmd_map,
        "stats": cmd_stats,
    }[cmd](args)


if __name__ == "__main__":
    sys.exit(main())
