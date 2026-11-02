#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史利率 × 经济周期：抓 FRED 公开 CSV（无需 API key），计算周期指标与共同点，生成 rates.js。

序列：
  FEDFUNDS  联邦基金有效利率（月）
  DGS10     10 年期美债收益率（日 → 月均）
  T10Y3M    10Y-3M 期限利差（日 → 月均，<0 为倒挂）
  CPIAUCSL  CPI（月 → 同比）
  USREC     NBER 衰退指标（月，1=衰退）

产物：rates.js（window.RATES = {...}），供 cycle.html 直接 file:// 加载。
用法：python3 fetch_history.py [--if-stale DAYS]
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
OUT = os.path.join(ROOT, "rates.js")
SESSION = requests.Session()
SESSION.trust_env = False
SESSION.headers.update({"User-Agent": "Mozilla/5.0 market-watch/1.0"})

SERIES = {
    "FEDFUNDS": "联邦基金有效利率",
    "DGS10": "10 年期美债收益率",
    "T10Y3M": "10Y-3M 期限利差",
    "CPIAUCSL": "CPI（季调）",
    "USREC": "NBER 衰退指标",
}
START = "1954-07-01"


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


def month_key(d):
    return d[:7]


def main():
    stale_days = 20
    if "--if-stale" in sys.argv:
        i = sys.argv.index("--if-stale")
        if i + 1 < len(sys.argv):
            stale_days = int(sys.argv[i + 1])
    if os.path.exists(OUT) and stale_days > 0:
        age = (time.time() - os.path.getmtime(OUT)) / 86400
        if age < stale_days:
            print(f"[skip] rates.js {age:.1f} 天前生成（阈值 {stale_days}）")
            return 0

    raw = {sid: parse(fetch_csv(sid)) for sid in SERIES}

    # 月度聚合：利率/利差取月均，衰退取月末值，CPI 取月末值
    def monthly_avg(rows):
        acc, out = {}, []
        for d, v in rows:
            m = month_key(d)
            acc.setdefault(m, []).append(v)
        for m in sorted(acc):
            out.append((m, round(sum(acc[m]) / len(acc[m]), 4)))
        return out

    def monthly_last(rows):
        box = {}
        for d, v in rows:
            box[month_key(d)] = v
        return [(m, box[m]) for m in sorted(box)]

    ffr = monthly_avg(raw["FEDFUNDS"])
    dgs10 = monthly_avg(raw["DGS10"])
    t10y3m = monthly_avg(raw["T10Y3M"])
    cpi = monthly_last(raw["CPIAUCSL"])
    usrec = monthly_last(raw["USREC"])

    cpi_map = dict(cpi)
    months = [m for m, _ in ffr]
    cpi_yoy = {}
    for m in months:
        try:
            y, mo = int(m[:4]), int(m[5:7])
            prev = f"{y-1:04d}-{mo:02d}"
            if prev in cpi_map:
                cpi_yoy[m] = round((cpi_map[m] / cpi_map[prev] - 1) * 100, 2)
        except (ValueError, KeyError):
            pass
    dgs10_map, t_map, rec_map = dict(dgs10), dict(t10y3m), dict(usrec)

    # 统一时间轴：FFR 起始月 → 最新月（缺失月份用前值）
    series = {"ffr": [], "real": [], "dgs10": [], "slope": [], "cpi": [], "rec": []}
    labels = []
    last = {k: None for k in series}
    for m in months:
        labels.append(m)
        last["ffr"] = next((v for mm, v in ffr if mm == m), last["ffr"])
        last["dgs10"] = dgs10_map.get(m, last["dgs10"])
        last["slope"] = t_map.get(m, last["slope"])
        last["cpi"] = cpi_yoy.get(m, last["cpi"])
        last["rec"] = rec_map.get(m, last["rec"] or 0)
        series["ffr"].append(last["ffr"])
        series["dgs10"].append(last["dgs10"])
        series["slope"].append(last["slope"])
        series["cpi"].append(last["cpi"])
        series["real"].append(round(last["ffr"] - last["cpi"], 2)
                              if last["ffr"] is not None and last["cpi"] is not None else None)
        series["rec"].append(int(last["rec"] or 0))

    # 衰退区间（USREC 连续 1）
    recessions = []
    cur = None
    for i, m in enumerate(labels):
        if series["rec"][i] == 1 and cur is None:
            cur = m
        elif series["rec"][i] == 0 and cur is not None:
            recessions.append({"start": cur, "end": labels[i - 1]})
            cur = None
    if cur:
        recessions.append({"start": cur, "end": labels[-1]})

    def idx(m):
        return labels.index(m) if m in labels else None

    # 每轮衰退：FFR 峰值、倒挂起点、实际利率
    cycles = []
    for rec in recessions:
        i0 = idx(rec["start"])
        if i0 is None or i0 < 12:
            continue
        look = max(0, i0 - 36)   # 只看衰退前 3 年，取本轮加息周期的峰值（72 个月会误抓上一轮高点）
        ffr_win = [(labels[j], series["ffr"][j]) for j in range(look, i0)
                   if series["ffr"][j] is not None]
        peak_m, peak_v = max(ffr_win, key=lambda x: x[1]) if ffr_win else (None, None)
        # 倒挂：衰退前 72 个月内首次 slope<0
        inv_m = None
        for j in range(look, i0):
            if series["slope"][j] is not None and series["slope"][j] < 0:
                inv_m = labels[j]
                break
        def months_between(a, b):
            ya, ma = int(a[:4]), int(a[5:7])
            yb, mb = int(b[:4]), int(b[5:7])
            return (yb - ya) * 12 + (mb - ma)
        cycles.append({
            "start": rec["start"],
            "end": rec["end"],
            "ffr_peak": peak_m,
            "ffr_peak_v": peak_v,
            "peak_lead": months_between(peak_m, rec["start"]) if peak_m else None,
            "inv_start": inv_m,
            "inv_lead": months_between(inv_m, rec["start"]) if inv_m else None,
            "real_at_start": series["real"][i0],
            "cpi_at_start": series["cpi"][i0],
        })

    def median(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        n = len(vals)
        return vals[n // 2] if n % 2 else round((vals[n // 2 - 1] + vals[n // 2]) / 2, 1)

    stats = {
        "n_recessions": len(cycles),
        "peak_lead_median": median([c["peak_lead"] for c in cycles]),
        "peak_lead_min": min([c["peak_lead"] for c in cycles]) if cycles else None,
        "peak_lead_max": max([c["peak_lead"] for c in cycles]) if cycles else None,
        "inv_lead_median": median([c["inv_lead"] for c in cycles]),
        "inv_count": sum(1 for c in cycles if c["inv_start"]),
        "real_median": median([c["real_at_start"] for c in cycles]),
        "cycle_count": len(cycles),
    }

    # 当前状态
    def last_valid(arr):
        for v in reversed(arr):
            if v is not None:
                return v
        return None
    current = {
        "as_of": labels[-1],
        "ffr": last_valid(series["ffr"]),
        "real": last_valid(series["real"]),
        "cpi": last_valid(series["cpi"]),
        "slope": last_valid(series["slope"]),
        "in_recession": series["rec"][-1] == 1,
    }

    # 名义/实际利率峰值与衰退的关系（共同点文字用的读数）
    payload = {
        "labels": labels,
        "series": series,
        "recessions": [{"start": r["start"], "end": r["end"]} for r in recessions],
        "cycles": cycles,
        "stats": stats,
        "current": current,
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "sources": {
            "FEDFUNDS": "https://fred.stlouisfed.org/series/FEDFUNDS",
            "DGS10": "https://fred.stlouisfed.org/series/DGS10",
            "T10Y3M": "https://fred.stlouisfed.org/series/T10Y3M",
            "CPIAUCSL": "https://fred.stlouisfed.org/series/CPIAUCSL",
            "USREC": "https://fred.stlouisfed.org/series/USREC",
        },
    }
    js = "window.RATES = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(js)
    os.replace(tmp, OUT)

    print(f"[ok] {len(labels)} 个月（{labels[0]} → {labels[-1]}）· 衰退 {len(recessions)} 次 · "
          f"FFR 峰值→衰退中位 {stats['peak_lead_median']} 个月 · "
          f"倒挂→衰退中位 {stats['inv_lead_median']} 个月 · 当前 FFR {current['ffr']}% / 实际 {current['real']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())