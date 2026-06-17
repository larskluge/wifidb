# wifidb — design

**Date:** 2026-06-17

## Purpose
A small CLI that logs internet speed + physical location to a SQLite database,
one row per "spot" (e.g. a café). Café/place name is resolved on demand via
Google Maps, since free/OSM data misses independent venues.

## Tools (all present on this machine)
- `speedtest` — Ookla CLI (`-f json`)
- `CoreLocationCLI` — WiFi/GPS fix + built-in reverse geocoding (`--format`)
- `sqlite3` / Python `sqlite3`
- `agent-browser` — drives Google Maps for the `resolve` step
- Python 3

## Layout (`~/code/wifidb/`)
- `wifidb.py` — single-file CLI (argparse subcommands)
- `wifidb` — wrapper (`exec python3 .../wifidb.py "$@"`), executable
- `wifidb.db` — SQLite store (gitignored)
- `test_wifidb.py` — pytest for pure parsing + DB logic (external calls mocked)
- `README.md`, `.gitignore`

## Commands
- `record` *(default)* — `speedtest -f json` + one `CoreLocationCLI` call
  (coords + accuracy + reverse-geocoded street, offline). Inserts a row;
  `place` left NULL (pending). **No browser.**
- `resolve [--last | --all | <id>]` — drives agent-browser → Google Maps on the
  row's coords, fills in the café name + exact place coords. **Only browser step.**
- `list` — recent records as a table.
- `map [--last | <id>]` — open that spot's pin in Google Maps.
- `stats` — avg down/up/ping grouped by place.

## Schema (`records`)
`id, ts, lat, lon, accuracy, address, place, place_lat, place_lon,
download_mbps, upload_mbps, ping_ms, jitter_ms, loss_pct, isp, is_vpn,
ext_ip, server, result_url, raw_json`

Speed conversion: Ookla `bandwidth` is bytes/sec → Mbps = `bandwidth * 8 / 1e6`.

## Place resolution (hybrid, no API key)
1. open `…/maps/search/specialty+coffee/@{lat},{lon},17z`
2. dismiss EU cookie consent if shown (Reject all / Rejeitar tudo)
3. take the **top organic** article (skip "Patrocinado"/"Sponsored")
4. click it → parse exact coords from the place URL (`!3d…!4d…`) + address
5. update the row

Heuristic: top organic result ≈ nearest relevant café. Documented as a known
limitation; can be upgraded later to click-and-rank-by-distance.

## Error handling
- speedtest fails → abort, print stderr
- location denied/off → save row with null coords + warning
- resolve consent-wall / no result → leave place NULL, report
- DB path overridable via `WIFIDB_DB` (used by tests)

## Testing
- `parse_speedtest` maps Ookla JSON → row dict (mbps, is_vpn, server string)
- `parse_location` parses the `|`-delimited CoreLocation output incl. missing fields
- DB insert / list / stats against a temp DB
- snapshot parsers (`first_organic`, `find_ref`) against a captured fixture
