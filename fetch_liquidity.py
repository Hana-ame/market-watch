#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美元流动性 × 黄金持仓：抓 FRED 公开 CSV + CFTC COT，生成 liquidity.js。

净流动性 = 美联储总资产（WALCL）− 财政部一般账户（TGA）− 隔夜逆回购（ON RRP）
  单位统一到「万亿美元」：WALCL/TGA 原始为百万美元（÷1e6），RRP 为十亿美元（÷1e3）。

序列（FRED，无需 API key）：
  WALCL        美联储总资产（周）
  WTREGEN      财政部一般账户 TGA（日）
  RRPONTSYD    隔夜逆回购 ON RRP（日，十亿美元）
  SOFR         担保隔夜融资利率（日）
  IORB         准备金余额利率（日，末值可能为未来生效日，需过滤）
  BAMLH0A0HYM2 高收益债期权调整利差（日）
  STLFSI4      圣路易斯联储金融压力指数（周）
  DFII10       10 年期实际收益率（日）
  DTWEXBGS     美元指数（广义，日值但按周发布，约滞后一周）
  M2SL         M2 货币供应（月）
  SP500        标普 500（日）

COT（CFTC Socrata，黄金 088691，管理基金持仓）：
  https://publicreporting.cftc.gov/resource/72hh-3qpy.json

产物：liquidity.js（window.LIQ = {...}），供 liquidity.html 直接加载。
用法：python3 fetch_liquidity.py [--if-stale DAYS] [--no-cot]
"""

import csv
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import time
from urllib.parse import urlencode

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "fred")
OUT = os.path.join(ROOT, "liquidity.js")

SESSION = requests.Session()
SESSION.trust_env = False
# 注意：不要覆盖 User-Agent。FRED 对自定义 UA（含伪装浏览器）会长时间挂住不返回，
# 默认的 python-requests/x 与 curl/x 都能正常拿到 CSV。

SERIES = {
    "WALCL": "美联储总资产（周，百万美元）",
    "WTREGEN": "财政部一般账户 TGA（日，百万美元）",
    "RRPONTSYD": "隔夜逆回购 ON RRP（日，十亿美元）",
    "SOFR": "担保隔夜融资利率（日，%）",
    "IORB": "准备金余额利率（日，%）",
    "BAMLH0A0HYM2": "高收益债利差（日，%）",
    "STLFSI4": "金融压力指数（周）",
    "DFII10": "10 年期实际收益率（日，%）",
    "DGS10": "10 年期名义收益率（日，%）",
    "DTWEXBGS": "美元指数（广义，周发布）",
    "M2SL": "M2（月，十亿美元）",
    "SP500": "标普 500（日）",
}

COT_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
COT_GOLD_CODE = "088691"
COT_CACHE = os.path.join(DATA, "cot_gold.json")

NET_YEARS = 5      # 净流动性曲线窗口
RATE_YEARS = 2     # 资金价格曲线窗口
COT_YEARS = 3      # COT 窗口
TODAY = dt.date.today()

CURL = shutil.which("curl")


def log(msg):
    print(msg, flush=True)


def http_get(url, params=None, timeout=25):
    """优先用 curl 取文本。

    FRED 对自定义 UA 的请求会长时间挂住（实测 Mozilla 伪装与自造 UA 均超时），
    而 curl 默认 UA 稳定，所以 curl 优先、requests（默认 UA）兜底。
    """
    if params:
        url = url + "?" + urlencode(params)
    if CURL:
        try:
            out = subprocess.run(
                [CURL, "-fsSL", "--compressed", "--max-time", str(timeout), url],
                capture_output=True, timeout=timeout + 8)
            if out.returncode == 0 and out.stdout:
                return out.stdout.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
    r = SESSION.get(url, timeout=(6, timeout))
    r.raise_for_status()
    return r.text


def fetch_csv(series_id, max_age_days=1, force=False):
    """抓 FRED CSV，带本地缓存与重试；失败时退回旧缓存。"""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{series_id}.csv")
    if not force and os.path.exists(path):
        age = (time.time() - os.path.getmtime(path)) / 86400
        if age < max_age_days and os.path.getsize(path) > 500:
            return open(path, encoding="utf-8").read()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    for attempt in range(2):
        try:
            text = http_get(url, timeout=25)
            if len(text) < 200:
                raise RuntimeError(f"响应过短 {len(text)}B")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return text
        except Exception as e:  # noqa: BLE001
            log(f"[warn] {series_id} 第 {attempt + 1} 次失败：{e}")
            time.sleep(2 * (attempt + 1))
    if os.path.exists(path):
        log(f"[warn] {series_id} 抓取失败，沿用缓存")
        return open(path, encoding="utf-8").read()
    raise RuntimeError(f"FRED {series_id} failed")


def parse(text):
    """返回 [(date, value)]，按日期升序；FRED 用 '.' 表示缺失。"""
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
            day = dt.date.fromisoformat(d[:10])
            val = float(v)
        except ValueError:
            continue
        if day > TODAY:      # 过滤 FRED 提前给出的未来生效日（如 IORB）
            continue
        rows.append((day, val))
    rows.sort(key=lambda x: x[0])
    return rows


def at_or_before(rows, day):
    """取 day 当天或之前最近一个观测。"""
    lo, hi, ans = 0, len(rows) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid][0] <= day:
            ans = rows[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def within(rows, years):
    if not rows:
        return []
    edge = rows[-1][0] - dt.timedelta(days=int(365.25 * years))
    return [r for r in rows if r[0] >= edge]


def change_over(rows, weeks):
    """周序列的 N 周变化（按日期回溯，避免假期造成的下标错位）。"""
    if not rows:
        return None
    last_d, last_v = rows[-1][0], rows[-1][1]
    ref = at_or_before(rows, last_d - dt.timedelta(weeks=weeks))
    if not ref:
        return None
    return round(last_v - ref[1], 4)


def pct_rank(rows, value):
    """value 在 rows 中的分位（0–1，含等值）。"""
    vals = [row[1] for row in rows]
    if not vals or value is None:
        return None
    below = sum(1 for v in vals if v <= value)
    return round(below / len(vals), 4)


def rnd(v, n=4):
    return None if v is None else round(v, n)


# ---------------------------------------------------------------- COT

def parse_cot(records):
    """把 Socrata 记录整理成 [(date, net, long, short, oi)]，按日期升序。"""
    out = []
    for r in records:
        raw = r.get("report_date_as_yyyy_mm_dd") or ""
        try:
            day = dt.date.fromisoformat(raw[:10])
        except ValueError:
            continue
        try:
            lng = float(r.get("m_money_positions_long_all") or 0)
            sht = float(r.get("m_money_positions_short_all") or 0)
            oi = float(r.get("open_interest_all") or 0)
        except ValueError:
            continue
        out.append((day, lng - sht, lng, sht, oi))
    out.sort(key=lambda x: x[0])
    return out


def fetch_cot():
    """拉黄金管理基金持仓；Socrata → 本地缓存 → 放弃（返回 None）。"""
    params = {
        "$select": ("report_date_as_yyyy_mm_dd,open_interest_all,"
                    "m_money_positions_long_all,m_money_positions_short_all"),
        "$where": f"cftc_contract_market_code='{COT_GOLD_CODE}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "400",
    }
    for attempt in range(2):
        try:
            text = http_get(COT_URL, params=params, timeout=25)
            records = json.loads(text)
            rows = parse_cot(records)
            if len(rows) < 10:
                raise RuntimeError(f"COT 记录过少：{len(rows)}")
            os.makedirs(DATA, exist_ok=True)
            with open(COT_CACHE, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False)
            return rows, "cftc"
        except Exception as e:  # noqa: BLE001
            log(f"[warn] COT 第 {attempt + 1} 次失败：{e}")
            time.sleep(2 * (attempt + 1))
    if os.path.exists(COT_CACHE):
        try:
            rows = parse_cot(json.load(open(COT_CACHE, encoding="utf-8")))
            if len(rows) >= 10:
                log("[warn] COT 沿用本地缓存")
                return rows, "cache"
        except Exception:  # noqa: BLE001
            pass
    log("[warn] COT 不可用，页面将隐藏该区块")
    return None, None


# ---------------------------------------------------------------- main

def main():
    stale_days = 0
    want_cot = "--no-cot" not in sys.argv
    if "--if-stale" in sys.argv:
        i = sys.argv.index("--if-stale")
        if i + 1 < len(sys.argv):
            stale_days = int(sys.argv[i + 1])
    if stale_days > 0 and os.path.exists(OUT):
        age = (time.time() - os.path.getmtime(OUT)) / 86400
        if age < stale_days:
            log(f"[skip] liquidity.js {age:.1f} 天前生成（阈值 {stale_days}）")
            return 0

    raw = {}
    budget_s = 180          # 抓取总预算，超出后剩余序列直接跳过（页面标注降级），避免拖死 CI
    fetch_t0 = time.time()
    for sid in SERIES:
        if time.time() - fetch_t0 > budget_s:
            log(f"[warn] {sid} 跳过：已超出 {budget_s}s 抓取预算")
            raw[sid] = []
            continue
        try:
            raw[sid] = parse(fetch_csv(sid, force=(stale_days == 0)))
        except Exception as e:  # noqa: BLE001
            log(f"[warn] {sid} 完全不可用：{e}")
            raw[sid] = []

    degraded = [f"{k}: 无数据" for k, v in raw.items() if not v]
    missing = [k for k in ("WALCL", "WTREGEN", "RRPONTSYD") if not raw[k]]
    if missing:
        log(f"[err] 净流动性三件套缺失 {missing}，无法生成")
        return 1

    # --- 净流动性（以 WALCL 的周频为时间轴，用当时最新的 TGA / RRP 对齐）
    walcl, tga, rrp = raw["WALCL"], raw["WTREGEN"], raw["RRPONTSYD"]
    net_rows = []
    for day, w in walcl:
        g = at_or_before(tga, day)
        r = at_or_before(rrp, day)
        if not g or not r:
            continue
        net = w / 1e6 - g[1] / 1e6 - r[1] / 1e3      # 单位：万亿美元
        net_rows.append((day, round(net, 4), round(w / 1e6, 4),
                         round(g[1] / 1e6, 4), round(r[1] / 1e3, 4)))

    net_win = within(net_rows, NET_YEARS)
    labels = [d.isoformat() for d, *_ in net_win]
    values = [n for _, n, *_ in net_win]

    last_day, last_net = net_rows[-1][0], net_rows[-1][1]
    stat = {
        "latest": last_net,
        "wow": change_over(net_rows, 1),
        "chg4": change_over(net_rows, 4),
        "chg13": change_over(net_rows, 13),
        "chg52": change_over(net_rows, 52),
        "pct5y": pct_rank(within(net_rows, 5), last_net),
        "min5y": min(values) if values else None,
        "max5y": max(values) if values else None,
    }
    rrp_latest = rrp[-1]
    rrp_drained = {"drained": rrp_latest[1] < 10, "value_bn": round(rrp_latest[1], 3),
                   "as_of": rrp_latest[0].isoformat()}

    # --- 资金价格（以日期并集为轴，各序列取当时最新值）
    rate_keys = ["SOFR", "IORB", "BAMLH0A0HYM2", "STLFSI4", "DFII10", "DTWEXBGS", "SP500", "DGS10"]
    rate_keys = [k for k in rate_keys if raw.get(k)]
    dates = []
    for k in rate_keys:
        dates.extend(d for d, _ in within(raw[k], RATE_YEARS))
    dates = sorted(set(dates))
    edge = dates[-1] - dt.timedelta(days=int(365.25 * RATE_YEARS)) if dates else None
    dates = [d for d in dates if edge and d >= edge]

    rates = {"labels": [d.isoformat() for d in dates]}
    for k in rate_keys:
        series = raw[k]
        vals, cur = [], None
        for d in dates:
            hit = at_or_before(series, d)
            cur = hit[1] if hit else cur
            vals.append(rnd(cur) if cur is not None else None)
        rates[k.lower()] = vals

    # 利差与日变化（取各自最新观测）
    def latest(k):
        return raw.get(k, [])[-1] if raw.get(k) else None

    sofr, iorb = latest("SOFR"), latest("IORB")
    spread_rows = []
    if raw.get("SOFR") and raw.get("IORB"):
        for d, v in raw["SOFR"]:
            ref = at_or_before(raw["IORB"], d)
            if ref:
                spread_rows.append((d, round(v - ref[1], 4)))
    hy = latest("BAMLH0A0HYM2")
    stl = latest("STLFSI4")
    real10 = latest("DFII10")
    dxy = latest("DTWEXBGS")
    spx = raw.get("SP500", [])
    dgs10 = latest("DGS10")

    def delta(rows, n=1):
        if len(rows) < n + 1:
            return None
        return round(rows[-1][1] - rows[-1 - n][1], 4)

    rates["spread"] = []
    if spread_rows:
        s_map = {d: v for d, v in spread_rows}
        cur = None
        for d in dates:
            hit = at_or_before(spread_rows, d)
            cur = hit[1] if hit else cur
            rates["spread"].append(rnd(cur))

    current = {
        "sofr": rnd(sofr[1]) if sofr else None,
        "sofr_as_of": sofr[0].isoformat() if sofr else None,
        "iorb": rnd(iorb[1]) if iorb else None,
        "spread": rnd(sofr[1] - iorb[1]) if (sofr and iorb) else None,
        "hy": rnd(hy[1]) if hy else None,
        "hy_as_of": hy[0].isoformat() if hy else None,
        "hy_chg": delta(raw.get("BAMLH0A0HYM2", [])),
        "stlfsi": rnd(stl[1]) if stl else None,
        "stlfsi_as_of": stl[0].isoformat() if stl else None,
        "real10": rnd(real10[1]) if real10 else None,
        "real10_as_of": real10[0].isoformat() if real10 else None,
        "dxy": rnd(dxy[1]) if dxy else None,
        "dxy_as_of": dxy[0].isoformat() if dxy else None,
        "dxy_chg": delta(raw.get("DTWEXBGS", [])),
        "spx": rnd(spx[-1][1]) if spx else None,
        "spx_as_of": spx[-1][0].isoformat() if spx else None,
        "spx_pct": (round((spx[-1][1] / spx[-2][1] - 1) * 100, 2)
                    if len(spx) >= 2 else None),
        "dgs10": rnd(dgs10[1]) if dgs10 else None,
        "dgs10_bp": (round((dgs10[1] - raw["DGS10"][-2][1]) * 100, 1)
                     if len(raw.get("DGS10", [])) >= 2 else None),
    }

    # --- M2 同比
    m2_rows = raw.get("M2SL", [])
    m2_labels, m2_yoy = [], []
    m2_map = {d.strftime("%Y-%m"): v for d, v in m2_rows}
    for d, v in m2_rows[-36:]:
        key = d.strftime("%Y-%m")
        prev = f"{d.year - 1:04d}-{d.month:02d}"
        if prev in m2_map:
            m2_labels.append(key)
            m2_yoy.append(round((v / m2_map[prev] - 1) * 100, 2))

    # --- COT
    cot_payload = None
    if want_cot:
        cot_rows, cot_src = fetch_cot()
        if cot_rows:
            cot_win = within(cot_rows, COT_YEARS)
            nets = [n for _, n, *_ in cot_win]
            cot_payload = {
                "labels": [d.isoformat() for d, *_ in cot_win],
                "net": [int(n) for n in nets],
                "long": [int(l) for _, _, l, _, _ in cot_win],
                "short": [int(s) for _, _, _, s, _ in cot_win],
                "oi": [int(o) for _, _, _, _, o in cot_win],
                "source": cot_src,
                "latest": {
                    "date": cot_rows[-1][0].isoformat(),
                    "net": int(cot_rows[-1][1]),
                    "long": int(cot_rows[-1][2]),
                    "short": int(cot_rows[-1][3]),
                    "oi": int(cot_rows[-1][4]),
                    "pct3y": pct_rank(cot_win, cot_rows[-1][1]),
                    "chg4w": int(cot_rows[-1][1] - cot_rows[-5][1]) if len(cot_rows) >= 5 else None,
                    "share": round(cot_rows[-1][1] / cot_rows[-1][4] * 100, 1) if cot_rows[-1][4] else None,
                },
            }
        else:
            degraded.append("cot: 获取失败，区块已隐藏")

    payload = {
        "meta": {
            "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
            "net_as_of": last_day.isoformat(),
            "rates_as_of": dates[-1].isoformat() if dates else None,
            "degraded": degraded,
            "sources": {
                "WALCL": "https://fred.stlouisfed.org/series/WALCL",
                "WTREGEN": "https://fred.stlouisfed.org/series/WTREGEN",
                "RRPONTSYD": "https://fred.stlouisfed.org/series/RRPONTSYD",
                "SOFR": "https://fred.stlouisfed.org/series/SOFR",
                "IORB": "https://fred.stlouisfed.org/series/IORB",
                "BAMLH0A0HYM2": "https://fred.stlouisfed.org/series/BAMLH0A0HYM2",
                "STLFSI4": "https://fred.stlouisfed.org/series/STLFSI4",
                "DFII10": "https://fred.stlouisfed.org/series/DFII10",
                "DTWEXBGS": "https://fred.stlouisfed.org/series/DTWEXBGS",
                "M2SL": "https://fred.stlouisfed.org/series/M2SL",
                "SP500": "https://fred.stlouisfed.org/series/SP500",
                "COT": "https://publicreporting.cftc.gov/resource/72hh-3qpy.json",
            },
        },
        "net": {
            "labels": labels,
            "values": values,
            "walcl": [w for _, _, w, _, _ in net_win],
            "tga": [g for _, _, _, g, _ in net_win],
            "rrp": [r for _, _, _, _, r in net_win],
            "stats": stat,
            "rrp_drained": rrp_drained,
        },
        "rates": dict(rates, current=current),
        "m2": {"labels": m2_labels, "yoy": m2_yoy},
        "cot": cot_payload,
    }

    js = "window.LIQ = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(js)
    os.replace(tmp, OUT)

    def fmt_chg(v, unit="T"):
        return "—" if v is None else f"{v:+.3f}{unit}"

    log(f"[ok] 净流动性 ${stat['latest']:.3f}T @ {last_day} · 13 周 {fmt_chg(stat['chg13'])} · "
        f"分位 {(stat['pct5y'] or 0) * 100:.0f}% · RRP ${rrp_drained['value_bn']:.2f}B · "
        f"SOFR-IORB {current['spread'] if current['spread'] is not None else '—'} · "
        f"HY {current['hy']}% · COT {'有' if cot_payload else '无'}")
    if degraded:
        log("[degraded] " + "; ".join(degraded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
