import os, csv, urllib.request, json

API_KEY = os.environ["FRED_API_KEY"]
BASE = "https://api.stlouisfed.org/fred/series/observations"

# series_id -> divisor converting the FRED native unit into BILLIONS
SERIES = {
    "WALCL":      1000.0,  # USD millions -> USD billions
    "RPONTSYD":   1.0,     # USD billions (FRED unit: Billions of US Dollars)
    "RRPONTSYD":  1.0,     # USD billions (FRED unit: Billions of US Dollars)
    "WTREGEN":    1000.0,  # USD millions -> USD billions  (FRED unit: Millions)
    "ECBASSETSW": 1000.0,  # EUR millions -> EUR billions
    "JPNASSETS":  10.0,    # JPY 100-millions -> JPY billions
}

# FX pairs from FRED (daily). Direction matters:
#   DEXUSEU = USD per EUR  -> MULTIPLY EUR to get USD
#   DEXJPUS = JPY per USD  -> DIVIDE   JPY to get USD
FX = {
    "EURUSD": "DEXUSEU",
    "JPYUSD": "DEXJPUS",
}

FIELDS = ["date",
          # US, USD billions
          "walcl_bn_usd", "rrp_bn_usd", "tga_bn_usd", "rpo_bn_usd",
          "net_liq_bn_usd",
          # balance sheets, local-currency billions
          "ecb_bs_bn_eur", "boj_bs_bn_jpy",
          # balance sheets converted to USD billions
          "ecb_bs_bn_usd", "boj_bs_bn_usd",
          # composites
          "netliq_plus_cb_usd_bn",
          "netliq_plus_rpo_bn"]


def fetch(series_id, limit=400):
    """Return {date: value} newest-first, skipping FRED's '.' placeholders."""
    if not API_KEY or not API_KEY.strip():
        raise SystemExit(
            "FRED_API_KEY is empty. Check that the GitHub secret is named "
            "exactly 'FRED_API_KEY' and that the workflow passes it through."
        )
    url = (f"{BASE}?series_id={series_id}&api_key={API_KEY.strip()}"
           f"&file_type=json&sort_order=desc&limit={limit}")
    try:
        with urllib.request.urlopen(url) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"FRED rejected '{series_id}' with HTTP {e.code}.\n"
            f"URL: {url.replace(API_KEY.strip(), '***')}\n"
            f"Response: {body}"
        )
    if "observations" not in data:
        raise SystemExit(f"Unexpected FRED response for '{series_id}': {data}")
    out = {}
    for obs in data["observations"]:
        if obs["value"] != ".":
            try:
                out[obs["date"]] = float(obs["value"])
            except ValueError:
                pass
    return out


def latest_on_or_before(series, date):
    """Most recent observation at or before `date`.

    This IS the carry-forward: if a series has no print for the anchor week,
    we use its last available value instead of leaving the cell blank.
    """
    candidates = [d for d in series if d <= date]
    return series[max(candidates)] if candidates else None


def main():
    raw = {s: fetch(s) for s in SERIES}
    fx = {name: fetch(sid) for name, sid in FX.items()}

    # Anchor on WALCL — the slowest, most important series.
    if not raw["WALCL"]:
        raise SystemExit("WALCL returned no observations")
    date = max(raw["WALCL"])

    def val(sid):
        """Latest value for sid at/before the anchor date, in BILLIONS."""
        v = latest_on_or_before(raw.get(sid) or {}, date)
        return None if v is None else v / SERIES[sid]

    walcl = val("WALCL")
    rrp   = val("RRPONTSYD")
    tga   = val("WTREGEN")
    rpo   = val("RPONTSYD")

    # US net liquidity = WALCL - RRP - TGA
    net_liq = walcl - rrp - tga if None not in (walcl, rrp, tga) else None

    # Balance sheets in local-currency billions
    ecb_eur = val("ECBASSETSW")
    boj_jpy = val("JPNASSETS")

    # FX, carried forward to the anchor date if the FX market hasn't printed
    eurusd = latest_on_or_before(fx["EURUSD"], date)   # USD per EUR
    jpyusd = latest_on_or_before(fx["JPYUSD"], date)   # JPY per USD

    # Local-currency billions -> USD billions
    ecb_usd = ecb_eur * eurusd if (ecb_eur is not None and eurusd) else None
    boj_usd = boj_jpy / jpyusd if (boj_jpy is not None and jpyusd) else None

    # Composite 1: US net liquidity + the two foreign CB balance sheets (USD bn)
    netliq_plus_cb = (net_liq + ecb_usd + boj_usd
                      if None not in (net_liq, ecb_usd, boj_usd) else None)

    # Composite 2: US net liquidity with the repo leg added back
    netliq_plus_rpo = (net_liq + rpo
                       if None not in (net_liq, rpo) else None)

    def fmt(x):
        return "" if x is None else f"{x:.2f}"

    row = [date,
           fmt(walcl), fmt(rrp), fmt(tga), fmt(rpo),
           fmt(net_liq),
           fmt(ecb_eur), fmt(boj_jpy),
           fmt(ecb_usd), fmt(boj_usd),
           fmt(netliq_plus_cb),
           fmt(netliq_plus_rpo)]

    path = "netliquidity.csv"
    existing = set()
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                existing.add(r["date"])

    with open(path, "a", newline="") as w_f:
        w = csv.writer(w_f)
        if not existing:
            w.writerow(FIELDS)
        if date not in existing:
            w.writerow(row)
        else:
            print(f"row for {date} already present, skipping")


if __name__ == "__main__":
    main()
