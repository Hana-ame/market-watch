#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""银行体系准备金 · FRED 公开 CSV（无需 API key）→ reserves.js。

序列（美联储 H.4.1 周报口径，单位：百万美元）：
  WRESBAL  银行准备金（Reserve Balances with Federal Reserve Banks）——流动性生命线
  WDTGAL   财政部一般账户（Treasury General Account）——发债/支出抽放水
  WLRRAL   隔夜逆回购余额（Overnight Reverse Repos，ON RRP）——回收的闲钱
  GDP      名义 GDP（季度，十亿美元）——用于算准备金占 GDP 的安全线

产物：reserves.js（window.RESERVES = {...}），供 reserves.html 直接 file:// 加载。
用法：python3 fetch_reserves.py [--if-stale DAYS]

注意：本页与 liquidity.html（净流动性 WALCL−TGA−ON RRP）互补——
liquidity 页看「钱的总量变化」，本页看「银行手里还剩多少活钱」。
"""

import csv
import datetime as dt
import io
import json
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "fred")
OUT = os.path.join(ROOT, "reserves.js")

# 注意：不要覆盖 User-Agent。FRED 对自定义 UA 会长时间挂住不返回。
SESSION = requests.Session()
SESSION.trust_env = False

SERIES = {
    "WRESBAL": "银行准备金（百万美元）",
    "WDTGAL": "财政部 TGA（百万美元）",
    "WLRRAL": "隔夜逆回购 ON RRP（百万美元）",
    "GDP": "名义 GDP（十亿美元）",
}
# 准备金占 GDP 安全线（华尔街经验口径）
SAFE_GDP_PCT = 10.0
WEEK_WINDOW = 4


def fetch_csv(series_id, max_age_days=20):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{series_id}.csv")
    if os.path.exists(path):
        age = (time.time() - os.path.getmtime(path)) / 86400
        if age < max_age_days and os.path.getsize(path) > 1000:
            return open(path, encoding="utf-8").read()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=40)
            r.raise_for_status()
            text = r.text
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return text
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    if os.path.exists(path):
        return open(path, encoding="utf-8").read()
    raise RuntimeError(f"FRED {series_id} failed")


def parse(text):
    """返回 (日期, 值) 列表；FRED 用 '.' 表示缺失。"""
    rows = []
    rd = csv.DictReader(io.StringIO(text))
    key = None
    for row in rd:
        if key is None:
            key = list(row.keys())[1]
        d, v = row.get("observation_date") or row.get("DATE"), row.get(key)
        if not d or v in (None, "", "."):
            continue
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue
    return rows


def quarterly_to_daily(gdp_quarterly):
    """季度 GDP（十亿→百万美元）线性插值成日度。"""
    if not gdp_quarterly:
        return []
    pts = [(d, v * 1000) for d, v in gdp_quarterly]
    out = []
    for i in range(len(pts) - 1):
        d0, v0 = pts[i]
        d1, v1 = pts[i + 1]
        t0 = dt.date.fromisoformat(d0)
        t1 = dt.date.fromisoformat(d1)
        span = (t1 - t0).days or 1
        cur = t0
        while cur < t1:
            frac = (cur - t0).days / span
            out.append((cur.isoformat(), round(v0 + (v1 - v0) * frac, 0)))
            cur += dt.timedelta(days=1)
    out.append(pts[-1])
    return out


def main():
    stale_days = 20
    if "--if-stale" in sys.argv:
        i = sys.argv.index("--if-stale")
        if i + 1 < len(sys.argv):
            stale_days = int(sys.argv[i + 1])
    if os.path.exists(OUT) and stale_days > 0:
        age = (time.time() - os.path.getmtime(OUT)) / 86400
        if age < stale_days:
            print(f"[skip] reserves.js {age:.1f} 天前生成（阈值 {stale_days}）")
            return 0

    raw = {sid: parse(fetch_csv(sid)) for sid in SERIES}
    wres, tga, rrp = raw["WRESBAL"], raw["WDTGAL"], raw["WLRRAL"]
    if not wres:
        raise RuntimeError("WRESBAL empty")

    labels, series = [], {"reserves": [], "tga": [], "rrp": []}
    tga_map, rrp_map = dict(tga), dict(rrp)
    last = {"tga": None, "rrp": None}
    for d, v in wres:
        labels.append(d)
        last["tga"] = tga_map.get(d, last["tga"])
        last["rrp"] = rrp_map.get(d, last["rrp"])
        series["reserves"].append(v)
        series["tga"].append(last["tga"])
        series["rrp"].append(last["rrp"])

    gdp_daily = dict(quarterly_to_daily(raw["GDP"]))
    gdp_series = [gdp_daily.get(d) for d in labels]

    safe_line = [round(g * SAFE_GDP_PCT / 100.0, 0) if g else None for g in gdp_series]
    gap = [round(r - s, 0) if r is not None and s is not None else None
           for r, s in zip(series["reserves"], safe_line)]
    pct_gdp = [round(r / g * 100.0, 2) if r is not None and g else None
               for r, g in zip(series["reserves"], gdp_series)]

    def last_valid(arr):
        for v in reversed(arr):
            if v is not None:
                return v
        return None

    def week_chg(arr, n=WEEK_WINDOW):
        cur = None
        for v in reversed(arr):
            if v is not None:
                cur = v
                break
        if cur is None:
            return None
        past, cnt = None, 0
        for v in reversed(arr):
            if v is not None:
                cnt += 1
                if cnt == n:
                    past = v
                    break
        if past is None:
            for v in arr:
                if v is not None:
                    past = v
                    break
        if past is None:
            return None
        return round(cur - past, 0)

    n = len(labels)
    latest = {
        "date": labels[-1] if n else None,
        "reserves": series["reserves"][-1] if n else None,
        "tga": series["tga"][-1] if n else None,
        "rrp": series["rrp"][-1] if n else None,
        "gdp": last_valid(gdp_series),
        "pct_gdp": last_valid(pct_gdp),
        "safe_line": last_valid(safe_line),
        "gap": last_valid(gap),
        "chg_4w_reserves": week_chg(series["reserves"]),
        "chg_4w_tga": week_chg(series["tga"]),
        "chg_4w_rrp": week_chg(series["rrp"]),
    }

    def since(arr, start="2008-01-01"):
        return [(d, v) for d, v in zip(labels, arr) if d >= start and v is not None]

    def extreme(arr, mode):
        if not arr:
            return None
        key = max if mode == "max" else min
        d, v = key(arr, key=lambda x: x[1])
        return {"date": d, "value": v}

    hist = {
        "reserves_max": extreme(since(series["reserves"]), "max"),
        "reserves_min_since_2019": extreme(since(series["reserves"], "2019-01-01"), "min"),
        "rrp_max": extreme(since(series["rrp"]), "max"),
    }

    events = [
        {"date": "2019-09-16", "label": "2019 钱荒"},
        {"date": "2020-03-15", "label": "疫情 QE"},
        {"date": "2021-12-01", "label": "Taper"},
        {"date": "2022-06-01", "label": "QT 缩表"},
        {"date": "2025-10-01", "label": "TGA 重建"},
    ]

    payload = {
        "labels": labels,
        "series": series,
        "gdp_pct": pct_gdp,
        "safe_line": safe_line,
        "gap": gap,
        "latest": latest,
        "hist": hist,
        "events": events,
        "config": {"safe_gdp_pct": SAFE_GDP_PCT, "week_window": WEEK_WINDOW},
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "sources": {
            "WRESBAL": "https://fred.stlouisfed.org/series/WRESBAL",
            "WDTGAL": "https://fred.stlouisfed.org/series/WDTGAL",
            "WLRRAL": "https://fred.stlouisfed.org/series/WLRRAL",
            "GDP": "https://fred.stlouisfed.org/series/GDP",
        },
    }
    js = "window.RESERVES = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(js)
    os.replace(tmp, OUT)

    print(f"[ok] {n} 周（{labels[0]} → {labels[-1]}）· 准备金 {latest['reserves']/1e6:.2f} 万亿 · "
          f"占 GDP {latest['pct_gdp']:.2f}%（安全线 {SAFE_GDP_PCT:.0f}%）· "
          f"TGA {latest['tga']/1e6:.2f} 万亿 · RRP {latest['rrp']/1e6:.2f} 万亿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
