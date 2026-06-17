# wifidb

Log internet speed + your physical location to a SQLite database, one row per
"spot" (café, hotel, etc.). The café name is resolved on demand from Google
Maps — because free/OpenStreetMap data misses most independent venues.

## Requirements (Homebrew)

```sh
brew tap teamookla/speedtest && brew install speedtest   # Ookla CLI
brew install corelocationcli                             # macOS WiFi/GPS fix
npm i -g agent-browser && agent-browser install          # for `resolve`
```

First `speedtest` run accepts Ookla's license automatically. `CoreLocationCLI`
needs Location Services enabled (System Settings → Privacy & Security →
Location Services).

## Usage

```sh
./wifidb                   # list recent records (default)
./wifidb ls                # alias for list
./wifidb record            # speedtest + location → new row (no browser)
./wifidb resolve --last    # name the latest spot via Google Maps
./wifidb resolve --all     # name every pending spot
./wifidb map --last        # open the latest spot's pin in Google Maps
./wifidb stats             # average speeds grouped by place
```

Put `~/code/wifidb` on your PATH (or symlink `wifidb`) to run it anywhere.

## How it works

- **record** — `speedtest -f json` for bandwidth/latency, one `CoreLocationCLI`
  call for coords + accuracy + reverse-geocoded street (offline, no browser),
  and an `ipinfo.io` lookup of the external IP to record whether a VPN was
  active (`is_vpn`) and **where the connection exited** (`exit_loc` +
  coordinates). `place` is left pending.
- **resolve** — drives `agent-browser` → Google Maps centered on the row's
  coords, reads every result's coordinates from its place-link href, and picks
  the venue **nearest** the fix (not Google's top relevance-ranked result).
  Dismisses the EU cookie-consent wall automatically. Defaults to the `cafe`
  category; override with `--query` (e.g. `resolve --last --query restaurant`)
  when the venue isn't a café.

Speed is stored in Mbps (`bandwidth * 8 / 1e6`). The full speedtest JSON is
kept in `raw_json` for forensics. The DB lives at `wifidb.db` (gitignored);
override with `WIFIDB_DB=/path/to.db`.

### Known limitation

The venue still has to appear in the chosen search category. A pure restaurant
won't surface under `--query cafe`; rerun with `--query restaurant`. Distance
ranking then picks the closest match.

## Tests

```sh
python3 test_wifidb.py   # zero-dependency runner (pure parsing + DB logic)
python3 -m pytest -q     # also works if pytest is installed
```
