"""Tests for wifidb pure logic (no network / no browser)."""
import os
import tempfile

import wifidb

SAMPLE_ST = {
    "type": "result",
    "timestamp": "2026-06-17T14:55:26Z",
    "ping": {"jitter": 2.05, "latency": 17.941},
    "download": {"bandwidth": 48540329, "bytes": 539519800},
    "upload": {"bandwidth": 39509088, "bytes": 182057622},
    "packetLoss": 0,
    "isp": "Datacamp",
    "interface": {"isVpn": True, "externalIp": "45.94.208.133"},
    "server": {"id": 1249, "name": "NOS", "location": "Lisboa", "country": "Portugal"},
    "result": {"url": "https://www.speedtest.net/result/c/abc"},
}


def test_mbps_conversion():
    # 48540329 bytes/s * 8 / 1e6 = 388.32 Mbps
    assert wifidb._mbps(48540329) == 388.32
    assert wifidb._mbps(0) is None
    assert wifidb._mbps(None) is None


def test_parse_speedtest():
    row = wifidb.parse_speedtest(SAMPLE_ST)
    assert row["download_mbps"] == 388.32
    assert row["upload_mbps"] == 316.07
    assert row["ping_ms"] == 17.941
    assert row["jitter_ms"] == 2.05
    assert row["loss_pct"] == 0
    assert row["isp"] == "Datacamp"
    assert row["is_vpn"] == 1
    assert row["ext_ip"] == "45.94.208.133"
    assert row["server"] == "NOS — Lisboa"
    assert row["result_url"].endswith("/abc")
    assert '"timestamp"' in row["raw_json"]


def test_parse_location_full():
    out = "38.698074|-9.428265|35.0|Rua José Carvalho Araújo, Cascais"
    loc = wifidb.parse_location(out)
    assert loc["lat"] == 38.698074
    assert loc["lon"] == -9.428265
    assert loc["accuracy"] == 35.0
    assert loc["address"] == "Rua José Carvalho Araújo, Cascais"


def test_parse_location_missing_fields():
    loc = wifidb.parse_location("38.7|-9.4")
    assert loc["lat"] == 38.7
    assert loc["lon"] == -9.4
    assert loc["accuracy"] is None
    assert loc["address"] is None


def test_parse_location_collapses_whitespace():
    loc = wifidb.parse_location("38.7|-9.4|10|Rua A\n2750 Cascais")
    assert loc["address"] == "Rua A 2750 Cascais"


class _FakeTime:
    """Stand-in for the time module that records sleeps instead of waiting."""
    def __init__(self):
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)


def _with_location_stub(once_fn):
    """Run get_location with _location_once and time.sleep stubbed out.

    Returns (result, fake_time) so tests can assert on retries/sleeps without
    touching CoreLocation or wall-clock time.
    """
    fake_time = _FakeTime()
    orig_once, orig_time = wifidb._location_once, wifidb.time
    try:
        wifidb._location_once, wifidb.time = once_fn, fake_time
        result = wifidb.get_location(attempts=5, delay=3.0)
    finally:
        wifidb._location_once, wifidb.time = orig_once, orig_time
    return result, fake_time


def test_get_location_retries_then_succeeds():
    calls = {"n": 0}
    good = {"lat": 38.7, "lon": -9.4, "accuracy": 10.0, "address": "Rua A"}

    def once():
        calls["n"] += 1
        return (good, None) if calls["n"] == 3 else (None, "no fix yet")

    loc, ft = _with_location_stub(once)
    assert loc == good
    assert calls["n"] == 3            # stopped the moment it got a fix
    assert ft.slept == [3.0, 3.0]     # slept only between the two failed tries


def test_get_location_gives_up_after_attempts():
    calls = {"n": 0}

    def once():
        calls["n"] += 1
        return None, "kCLErrorDomain"

    loc, ft = _with_location_stub(once)
    assert loc == {"lat": None, "lon": None, "accuracy": None, "address": None}
    assert calls["n"] == 5            # exhausted all attempts
    assert ft.slept == [3.0, 3.0, 3.0, 3.0]   # no sleep after the final try


def test_get_location_retries_when_exit_ok_but_no_fix():
    # CoreLocationCLI can exit 0 yet yield no coordinates before it acquires.
    empty = {"lat": None, "lon": None, "accuracy": None, "address": None}
    good = {"lat": 1.0, "lon": 2.0, "accuracy": 5.0, "address": None}
    seq = iter([(empty, None), (good, None)])

    loc, ft = _with_location_stub(lambda: next(seq))
    assert loc == good
    assert ft.slept == [3.0]          # one retry after the empty fix


SNAPSHOT = '''Page: specialty coffee - Google Maps
- combobox [expanded=false, ref=e2]: specialty coffee
- button "Pesquisar" [ref=e3]
- heading "Resultados" [level=1, ref=e20]
- article "Grupeto Bike Café" [ref=e13]
  - link "Grupeto Bike Café" [ref=e21]
  - heading "Patrocinado" [level=1, ref=e29]
- article "Asante Boutique Coffee Roasters" [ref=e14]
  - link "Asante Boutique Coffee Roasters" [ref=e22]
  - heading "Patrocinado" [level=1, ref=e30]
- article "Vroom Specialty Coffee & Brunch" [ref=e15]
  - link "Vroom Specialty Coffee & Brunch" [ref=e23]
- article "Unity Coffee Roasters" [ref=e16]
  - link "Unity Coffee Roasters" [ref=e24]
'''

CONSENT = '''- button "Rejeitar tudo" [ref=e10]
- button "Aceitar tudo" [ref=e11]
'''


def test_find_ref():
    assert wifidb.find_ref(CONSENT, ["Rejeitar tudo", "Reject all"]) == "@e10"
    assert wifidb.find_ref(CONSENT, ["Accept all", "Aceitar tudo"]) == "@e11"
    assert wifidb.find_ref(CONSENT, ["nope"]) is None


def test_first_organic_skips_sponsored():
    name, ref = wifidb.first_organic(SNAPSHOT)
    assert name == "Vroom Specialty Coffee & Brunch"
    assert ref == "@e23"


def test_parse_place_coords():
    url = ("https://www.google.com/maps/place/Vroom/@38.698,-9.437,16z/"
           "data=!3m6!1s0xd1ec5c3:0xd9b28!8m2!3d38.6981185!4d-9.4281209!16s")
    lat, lon = wifidb.parse_place_coords(url)
    assert lat == 38.6981185
    assert lon == -9.4281209
    assert wifidb.parse_place_coords("https://x/none") == (None, None)


def test_parse_place_address():
    snap = '- button "Endereço: R. José Carvalho Araújo 262, 2750-396 Cascais " [ref=e72]'
    assert wifidb.parse_place_address(snap) == "R. José Carvalho Araújo 262, 2750-396 Cascais"


def test_haversine_known_distances():
    # #3 fix → 7 Seas Bistro is metres away; → Happiest Coffee is ~520 m.
    near = wifidb.haversine(38.706684, -9.421122, 38.7066857, -9.4210818)
    far = wifidb.haversine(38.706684, -9.421122, 38.7020387, -9.4219057)
    assert near < 10
    assert 480 < far < 560


def test_parse_search_candidates_skips_non_places():
    snap = (
        '  - link "Café Pérola" [ref=e42, url=https://www.google.com/maps/place/Cafe/'
        'data=!8m2!3d38.7092853!4d-9.4209443!16s]\n'
        '  - link "7 Seas Bistro" [ref=e44, url=https://www.google.com/maps/place/7+Seas/'
        'data=!8m2!3d38.7066857!4d-9.4210818!16s]\n'
        '  - link "Pedir online" [ref=e45, url=https://order.example.com/x]\n'
    )
    cands = wifidb.parse_search_candidates(snap)
    assert [c["name"] for c in cands] == ["Café Pérola", "7 Seas Bistro"]
    assert cands[1]["ref"] == "@e44"
    assert cands[1]["lat"] == 38.7066857


def test_nearest_candidate_beats_relevance_order():
    # Regression: Google lists the popular Happiest Coffee first, but the venue
    # at the fix is 7 Seas Bistro. Old code took the first; the fix takes the
    # nearest. With the fix this passes; with "take first" it would fail.
    fix_lat, fix_lon = 38.706684, -9.421122
    cands = [
        {"name": "The Happiest Coffee", "lat": 38.7020387, "lon": -9.4219057},
        {"name": "7 Seas Bistro", "lat": 38.7066857, "lon": -9.4210818},
    ]
    assert cands[0]["name"] == "The Happiest Coffee"  # relevance/list order
    best = wifidb.nearest_candidate(fix_lat, fix_lon, cands)
    assert best["name"] == "7 Seas Bistro"


def test_rank_candidates_dedupes_and_sorts():
    fix = (38.706684, -9.421122)
    a = [{"name": "Far Cafe", "lat": 38.7020387, "lon": -9.4219057},
         {"name": "7 Seas Bistro", "lat": 38.7066857, "lon": -9.4210818}]
    b = [{"name": "7 Seas Bistro", "lat": 38.7066857, "lon": -9.4210818},  # dup loc
         {"name": "Mid", "lat": 38.7078644, "lon": -9.4194233}]
    out = wifidb.rank_candidates(*fix, [a, b], limit=10)
    names = [c["name"] for c in out]
    assert names[0] == "7 Seas Bistro"        # nearest first
    assert names.count("7 Seas Bistro") == 1   # deduped across lists
    assert out[0]["dist"] < 10
    assert len(out) == 3


RESULTS_SNAP = (
    '- link "Nikoletta Ristorante" [ref=e30, url=https://www.google.com/maps/place/'
    'Nikoletta/data=!8m2!3d38.692100!4d-9.418700!16s]\n'
    '- link "The Coffee" [ref=e31, url=https://www.google.com/maps/place/'
    'TheCoffee/data=!8m2!3d38.691900!4d-9.418600!16s]\n'
)
CONSENT_SNAP = '- button "Aceitar tudo" [ref=e11]\n- button "Rejeitar tudo" [ref=e12]\n'
EMPTY_SNAP = "Page: Google Maps\n- combobox [ref=e2]: cafe\n"


class _FakeAB:
    """Scripted stand-in for wifidb._ab driving the consent/results state machine.

    `script` lists the page each successive `open` lands on ("consent", "empty",
    or "results"); past the end of the script every `open` lands on results. A
    "consent" entry serves the consent page regardless of any click — that models
    the cold-start race where one dismissal attempt doesn't take, which is what
    the fix must survive.
    """
    def __init__(self, script):
        self.script = list(script)
        self.opens = 0
        self.on_consent = False
        self.cur = EMPTY_SNAP
        self.clicks = 0

    def __call__(self, *args):
        cmd = args[0]
        if cmd == "open":
            page = self.script[self.opens] if self.opens < len(self.script) else "results"
            self.opens += 1
            self.on_consent = page == "consent"
            self.cur = {"consent": CONSENT_SNAP, "empty": EMPTY_SNAP}.get(
                page, RESULTS_SNAP)
            return ""
        if cmd == "snapshot":
            return self.cur
        if cmd == "get":                                  # ("get", "url")
            return ("https://consent.google.com/m?continue=x" if self.on_consent
                    else "https://www.google.com/maps/search/cafe/@1,2,18z")
        if cmd == "click":
            self.clicks += 1
            return ""
        return ""                                         # wait, close, …


def _with_ab_stub(fake):
    """Run fetch_candidates for one query with _ab and time.sleep stubbed out."""
    orig_ab, orig_time = wifidb._ab, wifidb.time
    try:
        wifidb._ab, wifidb.time = fake, _FakeTime()
        return wifidb.fetch_candidates(38.6921, -9.4187, queries=("cafe",))
    finally:
        wifidb._ab, wifidb.time = orig_ab, orig_time


def test_fetch_candidates_clears_consent_and_recovers():
    # Regression: on the 2nd+ `record`, the cold browser launch lands on Google's
    # consent page, and on a cold start one dismissal attempt often doesn't take
    # (the click races the page). Old code re-opened exactly once, so the snapshot
    # stayed on the consent page → no/garbage candidates. The fix retries until
    # the page actually leaves consent.
    fake = _FakeAB(["consent", "consent"])
    cands = _with_ab_stub(fake)
    assert fake.clicks >= 1                                # consent was dismissed
    assert [c["name"] for c in cands] == ["Nikoletta Ristorante", "The Coffee"]
    assert cands[0]["dist"] < 20                           # actually near the fix


def test_fetch_candidates_retries_cold_start_empty():
    # Cold start: the results SPA hadn't painted place links on the first
    # snapshot. Old code parsed the empty page once and returned []. The fix
    # re-opens until real results appear.
    fake = _FakeAB(["empty"])
    cands = _with_ab_stub(fake)
    assert [c["name"] for c in cands] == ["Nikoletta Ristorante", "The Coffee"]


def test_fetch_candidates_no_consent_single_open():
    # Happy path: results on the first try, no consent — one open, no clicks.
    fake = _FakeAB([])
    cands = _with_ab_stub(fake)
    assert fake.opens == 1
    assert fake.clicks == 0
    assert len(cands) == 2


def test_parse_wifi():
    out = "  SSID : CoffeeWiFi\n  BSSID : aa:bb:cc:dd:ee:ff\n  MTU : 1500\n"
    w = wifidb.parse_wifi(out)
    assert w["ssid"] == "CoffeeWiFi"
    assert w["bssid"] == "aa:bb:cc:dd:ee:ff"


def test_parse_wifi_bssid_not_read_as_ssid():
    w = wifidb.parse_wifi("  BSSID : 11:22:33:44:55:66\n")
    assert w["ssid"] is None
    assert w["bssid"] == "11:22:33:44:55:66"


def test_parse_wifi_empty():
    w = wifidb.parse_wifi("")
    assert w == {"ssid": None, "bssid": None}


def test_parse_wifi_redacted_is_none():
    # macOS withholds SSID/BSSID from unprivileged processes as "<redacted>".
    w = wifidb.parse_wifi("  SSID : <redacted>\n  BSSID : <redacted>\n")
    assert w["ssid"] is None
    assert w["bssid"] is None


def test_parse_ipinfo():
    j = {"ip": "45.94.208.133", "city": "Lisbon", "region": "Lisbon",
         "country": "PT", "loc": "38.7167,-9.1333",
         "org": "AS212238 Datacamp Limited"}
    g = wifidb.parse_ipinfo(j)
    assert g["exit_loc"] == "Lisbon, PT — AS212238 Datacamp Limited"
    assert g["exit_lat"] == 38.7167
    assert g["exit_lon"] == -9.1333


def test_parse_ipinfo_empty_and_bogon():
    assert wifidb.parse_ipinfo({}) == {}
    assert wifidb.parse_ipinfo({"bogon": True}) == {}
    g = wifidb.parse_ipinfo({"city": "Berlin", "country": "DE"})
    assert g["exit_loc"] == "Berlin, DE"
    assert g["exit_lat"] is None


def test_migration_adds_exit_columns():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "old.db")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, ts TEXT)")
        c.commit()
        c.close()
        conn = wifidb.db_connect(path)  # should ALTER in the new columns
        cols = {r[1] for r in conn.execute("PRAGMA table_info(records)")}
        assert {"exit_loc", "exit_lat", "exit_lon"} <= cols


def test_db_insert_list_stats():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.db")
        conn = wifidb.db_connect(path)
        row = wifidb.parse_speedtest(SAMPLE_ST)
        row.update({"lat": 38.698, "lon": -9.428, "accuracy": 30.0,
                    "address": "Rua X", "place": None})
        rid = wifidb.insert_record(conn, row)
        assert rid == 1
        got = conn.execute("SELECT * FROM records WHERE id=1").fetchone()
        assert got["download_mbps"] == 388.32
        assert got["is_vpn"] == 1
        assert got["place"] is None
        # resolve-style update
        conn.execute("UPDATE records SET place=? WHERE id=1", ("Vroom",))
        conn.commit()
        stats = conn.execute(
            "SELECT place, COUNT(*) n FROM records GROUP BY place"
        ).fetchone()
        assert stats["place"] == "Vroom"
        assert stats["n"] == 1


if __name__ == "__main__":
    # Runs without pytest: `python3 test_wifidb.py`
    import traceback
    tests = sorted(k for k, v in list(globals().items())
                   if k.startswith("test_") and callable(v))
    failed = 0
    for name in tests:
        try:
            globals()[name]()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
