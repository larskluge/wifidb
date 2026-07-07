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
./wifidb                   # list, one entry per location (default)
./wifidb ls --raw          # individual measurements instead of grouped
./wifidb record            # speedtest + location, then pick the place
./wifidb resolve --last    # (re-)name a row non-interactively (auto-nearest)
./wifidb map --last        # open the latest spot's pin in Google Maps
./wifidb stats             # average speeds grouped by place
./wifidb delete            # pick a spot with ↑/↓, Enter to delete it
```

Put `~/code/wifidb` on your PATH (or symlink `wifidb`) to run it anywhere.

## How it works

- **record** — `speedtest -f json` for bandwidth/latency, one `CoreLocationCLI`
  call for coords + accuracy + reverse-geocoded street (offline), and an
  `ipinfo.io` lookup of the external IP to record whether a VPN was active
  (`is_vpn`) and **where the connection exited** (`exit_loc` + coordinates).
  The row is written immediately, then it fetches the ~10 nearest venues from
  Google Maps (merging `cafe` + `restaurant`) and shows an **arrow-key picker**
  with the nearest pre-selected — ↑/↓ to move, Enter to confirm, or type a name
  for a custom entry. Your choice updates the row. Non-interactive runs (piped)
  auto-pick the nearest. Nothing is lost if you skip the picker — name it later
  with `resolve`.
- **list** — aggregates into **one entry per location**, keyed by place name
  (falling back to BSSID, then a GPS cell), **ordered fastest first** by average
  download speed. Shows count, avg/best speeds, ping range, and the VPN split
  (e.g. `4/5` = four of five runs on VPN). `--raw` for the full per-measurement
  table (also fastest first, by each measurement's download speed).
- **resolve** `[--last|--all|<id>]` — names rows non-interactively by picking
  the venue **nearest** the fix from Google Maps. Mainly for backfilling rows
  recorded when the picker was skipped/unavailable. `--query <cat>` overrides
  the default `cafe`+`restaurant` search.
- **delete** (aliases `del`, `rm`, `remove`) — shows the grouped list as an
  **arrow-key picker**, **most-recently-recorded first**; ↑/↓ to a spot,
  **Enter deletes it** — *every* measurement grouped under it — immediately (no
  confirmation), Esc/Ctrl-C cancels. Refuses to run outside an interactive
  terminal so a piped/redirected invocation never deletes anything.

Note: macOS only discloses the Wi-Fi `ssid`/`bssid` to processes authorized for
Location Services, so an unprivileged CLI gets `<redacted>` — those are stored
as NULL. Grouping therefore relies on the resolved place name, not the network.

Speed is stored in Mbps (`bandwidth * 8 / 1e6`). The full speedtest JSON is
kept in `raw_json` for forensics. The DB lives at `wifi.db` (gitignored);
override with `WIFIDB_DB=/path/to.db`.

### Known limitation

The right venue has to appear in the Google Maps search. `record` merges
`cafe` + `restaurant` to cover most spots, but an unusual venue type might be
missing from the list — just type its name in the picker instead.

## Tests

```sh
python3 test_wifidb.py   # zero-dependency runner (pure parsing + DB logic)
python3 -m pytest -q     # also works if pytest is installed
```
