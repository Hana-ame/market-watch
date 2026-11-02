#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日看盘 · 数据抓取与快照生成

数据源：东方财富公开行情接口（push2 / push2his / push2ex / np-listapi）。
本机实测：绕开 Windows 侧 HTTP 代理（trust_env=False）时延约 0.06s，
走代理约 10s/请求，因此本脚本显式禁用环境代理，60 页全市场分页可并发在数秒内完成。

产物（同目录 data/）：
  latest.json            最新一次快照（原始结构化数据）
  history.json           按交易日累积的情绪/成交额历史（供走势图使用）
  snapshots/YYYY-MM-DD.json  当日快照
  data.js                给 index.html <script> 直接加载的数据文件（file:// 可用）
"""

import datetime as dt
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")
# 本地运行写 history.local.json（不进 git）；CI 用默认 history.json（每日回写仓库）
HISTORY_PATH = os.environ.get("MARKET_HISTORY_PATH", os.path.join(DATA_DIR, "history.json"))
# CI 每日回写一份「A股兜底快照」：境外取数偶发 502 时，页面用上一次好评级的 A股数据填充
FALLBACK_PATH = os.environ.get("MARKET_FALLBACK_PATH", os.path.join(DATA_DIR, "fallback.json"))
TZ = dt.timezone(dt.timedelta(hours=8))
HISTORY_KEEP = 120

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}

session = requests.Session()
session.trust_env = False  # 显式绕过代理：直连东财约 60ms，走代理约 10s
session.headers.update(HEADERS)


# ---------------------------------------------------------------- 基础设施

def get_json(url, tries=3, timeout=15):
    last = None
    for i in range(tries):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - 网络错误类型多，统一兜底
            last = e
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"GET failed: {url[:120]} -> {last}")


def num(v, default=None):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return default
    return default


def fmt_amount(v):
    """元 -> 中文单位字符串"""
    if not v:
        return "-"
    v = float(v)
    if v >= 1e12:
        return f"{v / 1e12:.2f}万亿"
    if v >= 1e8:
        return f"{v / 1e8:,.0f}亿"
    return f"{v / 1e4:,.0f}万"


# ---------------------------------------------------------------- 行情源

INDICES = [
    ("1.000001", "上证指数", "上证"),
    ("0.399001", "深证成指", "深证"),
    ("0.399006", "创业板指", "创业板"),
    ("1.000688", "科创50", "科创50"),
    ("1.000300", "沪深300", "沪深300"),
    ("1.000905", "中证500", "中证500"),
    ("1.000852", "中证1000", "中证1000"),
    ("0.899050", "北证50", "北证50"),
]

WATCHLIST = [
    ("A", [("0.002594", "002594", "比亚迪"), ("0.300750", "300750", "宁德时代"),
           ("1.688981", "688981", "中芯国际"), ("1.688256", "688256", "寒武纪"),
           ("1.600519", "600519", "贵州茅台"), ("0.300059", "300059", "东方财富")]),
    ("HK", [("116.01810", "01810", "小米集团-W"), ("116.00700", "00700", "腾讯控股"),
            ("116.09988", "09988", "阿里巴巴-W"), ("116.03690", "03690", "美团-W"),
            ("116.02015", "02015", "理想汽车-W"), ("116.09868", "09868", "小鹏汽车-W"),
            ("116.01211", "01211", "比亚迪股份")]),
    ("US", [("105.TSLA", "TSLA", "特斯拉"), ("105.NVDA", "NVDA", "英伟达"),
            ("105.AAPL", "AAPL", "苹果"), ("106.TSM", "TSM", "台积电")]),
]

FIELDS_QUOTE = "f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18"


def fetch_indices():
    secids = ",".join(s for s, _, _ in INDICES)
    d = get_json("https://push2.eastmoney.com/api/qt/ulist.np/get"
                 f"?fltt=2&invt=2&secids={secids}&fields={FIELDS_QUOTE}")
    raw = (d.get("data") or {}).get("diff") or []
    by_code = {str(x.get("f12")): x for x in raw}
    out = []
    for secid, name, short in INDICES:
        code = secid.split(".")[1]
        x = by_code.get(code)
        if not x:
            continue
        out.append({
            "code": code, "name": name, "short": short,
            "price": num(x.get("f2")), "pct": num(x.get("f3")),
            "change": num(x.get("f4")), "amount": num(x.get("f6")) or 0,
            "high": num(x.get("f15")), "low": num(x.get("f16")),
            "open": num(x.get("f17")), "prev_close": num(x.get("f18")),
        })
    if not out:
        raise RuntimeError("indices empty")
    return out


def fetch_quote_time():
    """指数报价的最后更新时间：用于识别节假日（休市）——K 线日期会回落到上一交易日，
    但只靠日期无法区分“盘前”与“今日不开市”。"""
    d = get_json("https://push2.eastmoney.com/api/qt/stock/get"
                 "?secid=1.000001&fltt=2&fields=f43,f58,f86")
    ts = (d.get("data") or {}).get("f86")
    if isinstance(ts, (int, float)) and ts > 1e9:
        return dt.datetime.fromtimestamp(ts, TZ)
    return None


def fetch_kline(secid, limit=45):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&klt=101&fqt=1&beg=20200101&end=20500101"
           f"&lmt={limit}&fields1=f1,f2,f3&fields2=f51,f53,f56,f57")
    d = get_json(url)
    data = d.get("data") or {}
    rows = []
    for line in data.get("klines") or []:
        p = line.split(",")
        if len(p) < 4:
            continue
        rows.append({"date": p[0], "close": num(p[1]), "volume": num(p[2]),
                     "amount": num(p[3])})
    return rows


def fetch_index_history():
    """指数 30 日收盘（迷你走势）+ 沪深两市近 45 日成交额"""
    def one(item):
        secid = item[0]
        try:
            return secid, fetch_kline(secid)
        except Exception:  # noqa: BLE001
            return secid, []
    with ThreadPoolExecutor(8) as ex:
        res = dict(ex.map(one, INDICES))
    sparks = {secid: [r["close"] for r in rows[-30:]] for secid, rows in res.items()}
    dates = [r["date"] for r in res.get("1.000001", [])][-30:]
    sh = {r["date"]: r for r in res.get("1.000001", [])}
    sz = {r["date"]: r for r in res.get("0.399001", [])}
    amt_hist = []
    for date in sorted(set(sh) & set(sz)):
        amt_hist.append({"date": date, "amount": (sh[date]["amount"] or 0) + (sz[date]["amount"] or 0)})
    return sparks, dates, amt_hist[-45:]


def fetch_boards():
    base = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&np=1&fltt=2&invt=2"
            "&pz=12&fields=f2,f3,f12,f14,f62,f104,f105,f128,f136&fs=")

    def side(fs, po):
        d = get_json(base + f"{fs}&po={po}&fid=f3")
        rows = (d.get("data") or {}).get("diff") or []
        out = []
        for x in rows:
            out.append({
                "name": x.get("f14"), "pct": num(x.get("f3")),
                "code": x.get("f12"),
                "inflow": num(x.get("f62")) or 0,
                "leader": x.get("f128"), "leader_pct": num(x.get("f136")),
            })
        return out

    result = {}
    tasks = {"industry_top": ("m:90+t:2", 1), "industry_bottom": ("m:90+t:2", 0),
             "concept_top": ("m:90+t:3", 1), "concept_bottom": ("m:90+t:3", 0)}
    with ThreadPoolExecutor(4) as ex:
        futs = {k: ex.submit(side, fs, po) for k, (fs, po) in tasks.items()}
        for k, f in futs.items():
            try:
                result[k] = f.result()
            except Exception:  # noqa: BLE001
                result[k] = []

    def norm(name):
        # 东财行业板块含二级/三级细分（如“游戏Ⅱ/游戏Ⅲ”），按去后缀后的名字去重
        return re.sub(r"[ⅠⅡⅢ]+$", "", str(name or "")).strip()

    if not any(result.get(k) for k in result):
        raise RuntimeError("boards empty")
    for k, rows in result.items():
        seen, uniq = set(), []
        want_bottom = k.endswith("bottom")
        for b in rows:
            key = norm(b["name"])
            if key in seen:
                continue
            seen.add(key)
            b["name"] = key
            uniq.append(b)
        if want_bottom:
            uniq = [b for b in uniq if (b["pct"] or 0) < 0][:8]
        result[k] = uniq[:8]
    return result


def fetch_breadth():
    """全市场分页：涨跌家数、分布、总成交额、中位数（60 页 × 100，10 线程）"""
    base = ("https://push2.eastmoney.com/api/qt/clist/get?np=1&fltt=2&invt=2&fid=f3&pz=100"
            "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048&fields=f3,f6,f12,f14")
    first = None
    for attempt in range(4):
        try:
            first = get_json(base + "&pn=1", tries=2)
            if first:
                break
        except Exception:  # noqa: BLE001
            time.sleep(1.5 * (attempt + 1))
    if not first:
        raise RuntimeError("breadth page1 failed")
    total = int((first.get("data") or {}).get("total") or 0)
    pages = max(1, (total + 99) // 100)

    def page(pn):
        # 单页最多 4 次、指数退避；并发压到 6，境外跑 CI 时明显更稳
        for attempt in range(4):
            try:
                d = get_json(base + f"&pn={pn}", tries=1, timeout=25)
                return ((d.get("data") or {}).get("diff") or [])
            except Exception:  # noqa: BLE001
                time.sleep(1.2 * (attempt + 1))
        return []

    rows = list((first.get("data") or {}).get("diff") or [])
    if pages > 1:
        with ThreadPoolExecutor(6) as ex:
            for chunk in ex.map(page, range(2, pages + 1)):
                rows.extend(chunk)

    pcts, amount, valid = [], 0.0, 0
    for x in rows:
        p = num(x.get("f3"))
        if p is None:
            continue
        valid += 1
        pcts.append(p)
        amount += num(x.get("f6")) or 0
    up = sum(1 for p in pcts if p > 0.001)
    down = sum(1 for p in pcts if p < -0.001)
    flat = valid - up - down
    pcts.sort()
    median = pcts[len(pcts) // 2] if pcts else 0.0

    edges = [-11, -9, -7, -5, -3, -1, 0, 1, 3, 5, 7, 9, 11]
    labels = ["≤-9", "-9~-7", "-7~-5", "-5~-3", "-3~-1", "-1~0",
              "0", "0~1", "1~3", "3~5", "5~7", "7~9", "≥9"]
    counts = [0] * len(labels)
    for p in pcts:
        if p <= edges[0]:
            counts[0] += 1
        elif p >= edges[-1]:
            counts[-1] += 1
        else:
            for i in range(1, len(labels)):
                if edges[i - 1] < p <= edges[i]:
                    counts[i] += 1
                    break
    return {"up": up, "down": down, "flat": flat, "total": valid, "universe": total,
            "amount": amount, "median": round(median, 2),
            "dist": {"labels": labels, "counts": counts}}


def fetch_limit_pools(trade_date):
    d8 = trade_date.replace("-", "")
    ut = "7eea3edcaed734bea9cbfc24409ed989"

    def pool(ep, sort):
        url = (f"https://push2ex.eastmoney.com/{ep}?ut={ut}&dpt=wz.ztzt"
               f"&Pageindex=0&pagesize=200&sort={sort}&date={d8}")
        d = get_json(url)
        data = d.get("data") or {}
        return data

    out = {}
    try:
        zt = pool("getTopicZTPool", "fbt%3Aasc")
        items = []
        for x in (zt.get("pool") or []):
            t = x.get("fbt") or 0
            items.append({
                "code": x.get("c"), "name": x.get("n"), "pct": round(num(x.get("zdp")) or 0, 2),
                "lbc": x.get("lbc") or 1, "fund": num(x.get("fund")) or 0,
                "fbt": f"{t // 10000:02d}:{(t // 100) % 100:02d}",
                "zbc": x.get("zbc") or 0, "industry": x.get("hybk") or "",
            })
        items.sort(key=lambda i: (-i["lbc"], -i["fund"]))
        out["limit_up"] = {"count": zt.get("tc") or len(items), "items": items[:12],
                           "max_lbc": max([i["lbc"] for i in items], default=0)}
    except Exception:  # noqa: BLE001
        out["limit_up"] = {"count": None, "items": [], "max_lbc": 0}
    try:
        dt_ = pool("getTopicDTPool", "fund%3Aasc")
        out["limit_down"] = {"count": dt_.get("tc") or 0,
                             "items": [{"code": x.get("c"), "name": x.get("n")}
                                       for x in (dt_.get("pool") or [])][:8]}
    except Exception:  # noqa: BLE001
        out["limit_down"] = {"count": None, "items": []}
    try:
        zb = pool("getTopicZBPool", "fbt%3Aasc")
        out["zhaban"] = {"count": zb.get("tc") or 0,
                         "items": [{"code": x.get("c"), "name": x.get("n"),
                                    "pct": round(num(x.get("zdp")) or 0, 2)}
                                   for x in (zb.get("pool") or [])][:8]}
    except Exception:  # noqa: BLE001
        out["zhaban"] = {"count": None, "items": []}
    return out


def fetch_watchlist():
    """东财批量报价；17 个 secid 一次请求偶发 502（境外 IP 更明显），拆两批 + 多试。"""
    secids = [s for _, group in WATCHLIST for s, _, _ in group]
    by_code = {}
    for chunk in (secids[:9], secids[9:]):
        if not chunk:
            continue
        for attempt in range(3):
            try:
                d = get_json("https://push2.eastmoney.com/api/qt/ulist.np/get"
                             f"?fltt=2&invt=2&secids={','.join(chunk)}&fields={FIELDS_QUOTE}",
                             tries=1, timeout=25)
                for x in ((d.get("data") or {}).get("diff") or []):
                    by_code[str(x.get("f12"))] = x
                break
            except Exception:  # noqa: BLE001
                time.sleep(1.5 * (attempt + 1))
    groups = []
    for market, items in WATCHLIST:
        rows = []
        for secid, code, name in items:
            x = by_code.get(code)
            if not x:
                continue
            rows.append({"code": code, "name": name,
                         "price": num(x.get("f2")), "pct": num(x.get("f3")),
                         "change": num(x.get("f4")), "amount": num(x.get("f6")) or 0})
        groups.append({"market": market, "items": rows})
    if not any(g["items"] for g in groups):
        raise RuntimeError("watchlist empty")
    return groups


def fetch_news(n=12):
    url = ("https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web"
           f"&biz=web_news_col&column=347&order=1&needInteractData=0&page_index=1&page_size={n}&req_trace=1")
    d = get_json(url)
    items = []
    for x in ((d.get("data") or {}).get("list") or []):
        items.append({"time": (x.get("showTime") or "")[11:16],
                      "title": x.get("title") or "",
                      "url": x.get("uniqueUrl") or x.get("url") or "",
                      "media": x.get("mediaName") or ""})
    return items


# ---------------------------------------------------------------- 宏观品种（黄金/原油/日元 + CNY 折算）

SINA_HQ = "https://hq.sinajs.cn/list="
SINA_REFERER = {"Referer": "https://finance.sina.com.cn"}
OZ_TO_GRAM = 31.1034768
MACRO_HIST_DAYS = 30


def fetch_sina(codes):
    """新浪 hq 接口：一次拿多品种；返回 {code: [fields]}。"""
    r = session.get(SINA_HQ + ",".join(codes), headers=SINA_REFERER, timeout=15)
    r.raise_for_status()
    r.encoding = "gbk"
    out = {}
    for line in r.text.splitlines():
        m = re.match(r'var hq_str_([A-Za-z0-9_]+)="(.*)";\s*$', line.strip())
        if m and m.group(2):
            out[m.group(1)] = [x.strip() for x in m.group(2).split(",")]
    return out


def _num(seq, i):
    try:
        return float(seq[i])
    except (IndexError, TypeError, ValueError):
        return None


def quote_sina_hf(f, name):
    """伦敦金/银、纽约原油、布伦特：0现价 4高 5低 6时间 7昨收 8开 12日期 13名称"""
    price, prev = _num(f, 0), _num(f, 7)
    return {"name": name, "price": price, "prev": prev,
            "pct": round((price / prev - 1) * 100, 2) if price and prev else None,
            "high": _num(f, 4), "low": _num(f, 5), "open": _num(f, 8),
            "time": f[6] if len(f) > 6 else "", "date": f[12] if len(f) > 12 else ""}


def quote_sina_nf(f, name):
    """国内期货连续：0名称 2开 3高 4低 8最新 10昨结算 17日期"""
    price, prev = _num(f, 8), _num(f, 10)
    if not prev:
        prev = _num(f, 5)
    return {"name": name, "price": price, "prev": prev,
            "pct": round((price / prev - 1) * 100, 2) if price and prev else None,
            "high": _num(f, 3), "low": _num(f, 4), "open": _num(f, 2),
            "time": f[1] if len(f) > 1 else "", "date": f[17] if len(f) > 17 else ""}


def quote_sina_fx(f, name):
    """外汇即期：3昨收 8最新 10涨跌幅 0时间"""
    price, prev = _num(f, 8), _num(f, 3)
    pct = _num(f, 10)
    if pct is None and price and prev:
        pct = (price / prev - 1) * 100
    return {"name": name, "price": price, "prev": prev,
            "pct": round(pct, 2) if pct is not None else None,
            "time": f[0] if f else "", "date": ""}


def fetch_sina_hist_global(symbol):
    url = ("https://stock2.finance.sina.com.cn/futures/api/json.php/"
           f"GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}")
    d = get_json(url)
    rows = []
    for x in d or []:
        rows.append({"date": x.get("date"), "close": num(x.get("close"))})
    return rows[-MACRO_HIST_DAYS:]


def fetch_em_fut_hist(secid, today):
    """东财国内期货主连日线；过滤掉夜盘形成的“下一交易日”未完成 K 线。
    （新浪 inner 接口实测停在 2024-07-17，不可用，故换东财：沪金 113.aum / 原油 142.scm）"""
    rows = fetch_kline(secid, limit=MACRO_HIST_DAYS + 10)
    rows = [r for r in rows if r["date"] <= today]
    return [{"date": r["date"], "close": r["close"]} for r in rows[-MACRO_HIST_DAYS:]]


def fetch_em_hist(secid):
    rows = fetch_kline(secid, limit=MACRO_HIST_DAYS + 5)
    return [{"date": r["date"], "close": r["close"]} for r in rows[-MACRO_HIST_DAYS:]]


def fetch_macro():
    codes = ["hf_XAU", "hf_XAG", "hf_CL", "hf_OIL", "nf_AU0", "nf_SC0",
             "gds_AUTD", "fx_susdcny", "fx_susdjpy", "fx_sjpycny"]
    raw = fetch_sina(codes)
    q = {}
    if "hf_XAU" in raw:
        q["xau"] = quote_sina_hf(raw["hf_XAU"], "伦敦金 XAU/USD")
    if "hf_XAG" in raw:
        q["xag"] = quote_sina_hf(raw["hf_XAG"], "伦敦银 XAG/USD")
    if "hf_CL" in raw:
        q["wti"] = quote_sina_hf(raw["hf_CL"], "WTI 原油")
    if "hf_OIL" in raw:
        q["brent"] = quote_sina_hf(raw["hf_OIL"], "布伦特原油")
    if "nf_AU0" in raw:
        q["au0"] = quote_sina_nf(raw["nf_AU0"], "沪金主力 AU0")
    if "nf_SC0" in raw:
        q["sc"] = quote_sina_nf(raw["nf_SC0"], "上海原油 SC0")
    if "gds_AUTD" in raw:
        q["autd"] = quote_sina_hf(raw["gds_AUTD"], "黄金延期 Au(T+D)")
    if "fx_susdcny" in raw:
        q["usdcny"] = quote_sina_fx(raw["fx_susdcny"], "美元/人民币 在岸")
    if "fx_susdjpy" in raw:
        q["usdjpy"] = quote_sina_fx(raw["fx_susdjpy"], "美元/日元")
    if "fx_sjpycny" in raw:
        q["jpycny"] = quote_sina_fx(raw["fx_sjpycny"], "日元/人民币")

    if "xau" not in q:
        raise RuntimeError("macro quotes empty (sina)")
    # 东财补充：离岸人民币、美元指数（新浪无）
    try:
        d = get_json("https://push2.eastmoney.com/api/qt/ulist.np/get"
                     "?fltt=2&invt=2&secids=133.USDCNH,100.UDI&fields=f2,f3,f12,f14,f60")
        for x in ((d.get("data") or {}).get("diff") or []):
            if str(x.get("f12")) == "USDCNH":
                q["usdcnh"] = {"name": "离岸人民币 USD/CNH", "price": num(x.get("f2")),
                               "pct": num(x.get("f3"))}
            elif str(x.get("f12")) == "UDI":
                q["dxy"] = {"name": "美元指数 DXY", "price": num(x.get("f2")),
                            "pct": num(x.get("f3"))}
    except Exception:  # noqa: BLE001
        pass
    for k in ("usdcnh", "dxy"):
        it = q.get(k)
        if it and it.get("pct") is not None and it.get("price"):
            it["prev"] = round(it["price"] / (1 + it["pct"] / 100), 4)

    # 30 日历史（分源并发）
    hist = {}

    def job(key, fn, arg):
        try:
            return key, fn(arg)
        except Exception:  # noqa: BLE001
            return key, []
    today = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    jobs = [("xau", fetch_sina_hist_global, "XAU"), ("xag", fetch_sina_hist_global, "XAG"),
            ("wti", fetch_sina_hist_global, "CL"), ("brent", fetch_sina_hist_global, "OIL"),
            ("au0", lambda sid: fetch_em_fut_hist(sid, today), "113.aum"),
            ("sc", lambda sid: fetch_em_fut_hist(sid, today), "142.scm"),
            ("usdjpy", fetch_em_hist, "119.USDJPY"), ("usdcnh", fetch_em_hist, "133.USDCNH"),
            ("dxy", fetch_em_hist, "100.UDI")]
    with ThreadPoolExecutor(9) as ex:
        for key, rows in ex.map(lambda t: job(*t), jobs):
            hist[key] = [r["close"] for r in rows if r.get("close")]
    # 沪金/沪油历史兜底：新浪 inner 接口偶尔为空
    if not hist.get("au0") and "au0" in q and q["au0"]["price"]:
        hist["au0"] = [q["au0"]["price"]]
    if not hist.get("sc") and "sc" in q and q["sc"]["price"]:
        hist["sc"] = [q["sc"]["price"]]

    # CNY 折算
    usdcny = (q.get("usdcny") or {}).get("price")
    usdjpy = (q.get("usdjpy") or {}).get("price")
    xau = (q.get("xau") or {}).get("price")
    wti = (q.get("wti") or {}).get("price")
    brent = (q.get("brent") or {}).get("price")
    cny = {}
    if usdcny and xau:
        cny["gold_per_g"] = round(xau * usdcny / OZ_TO_GRAM, 2)
        if (q.get("au0") or {}).get("price"):
            cny["au0_premium"] = round((q["au0"]["price"] / cny["gold_per_g"] - 1) * 100, 2)
        if (q.get("autd") or {}).get("price"):
            cny["autd_premium"] = round((q["autd"]["price"] / cny["gold_per_g"] - 1) * 100, 2)
    if usdcny and wti:
        cny["wti_per_bbl"] = round(wti * usdcny, 1)
    if usdcny and brent:
        cny["brent_per_bbl"] = round(brent * usdcny, 1)
        if (q.get("sc") or {}).get("price"):
            cny["sc_premium_brent"] = round((q["sc"]["price"] / cny["brent_per_bbl"] - 1) * 100, 2)
    if usdcny and usdjpy:
        cny["jpy100_calc"] = round(usdcny / usdjpy * 100, 4)
    if (q.get("jpycny") or {}).get("price"):
        cny["jpy100_official"] = round(q["jpycny"]["price"] * 100, 4)
    if cny.get("jpy100_calc") and cny.get("jpy100_official"):
        cny["jpy_diff_bp"] = round((cny["jpy100_official"] / cny["jpy100_calc"] - 1) * 10000, 2)

    ratios = {}
    if xau and wti:
        ratios["gold_oil"] = round(xau / wti, 1)
    if xau and (q.get("xag") or {}).get("price"):
        ratios["gold_silver"] = round(xau / q["xag"]["price"], 1)

    def ret(key):
        h = hist.get(key) or []
        if len(h) >= 2 and h[0]:
            return round((h[-1] / h[0] - 1) * 100, 2)
        return None
    for key in ("xau", "xag", "wti", "brent", "au0", "sc", "usdjpy", "usdcnh", "dxy"):
        if key in q:
            q[key]["hist"] = hist.get(key) or []
            q[key]["ret30"] = ret(key)

    # 近似补齐（页面脚注注明口径）：T+D≈沪金、在岸≈离岸、日元交叉由两条序列合成
    if q.get("autd") and q.get("au0", {}).get("ret30") is not None:
        q["autd"]["ret30"] = q["au0"]["ret30"]
    if q.get("usdcny") and q.get("usdcnh", {}).get("ret30") is not None:
        q["usdcny"]["ret30"] = q["usdcnh"]["ret30"]
    h_cnh, h_jpy = hist.get("usdcnh") or [], hist.get("usdjpy") or []
    if len(h_cnh) > 1 and len(h_jpy) > 1 and h_jpy[0] and h_jpy[-1]:
        q["jpycny"] = q.get("jpycny") or {}
        q["jpycny"]["ret30"] = round(((h_cnh[-1] / h_jpy[-1]) / (h_cnh[0] / h_jpy[0]) - 1) * 100, 2)

    metals = None
    try:
        metals = fetch_metals(usdcny)
    except Exception:  # noqa: BLE001
        metals = None

    return {"quotes": q, "cny": cny, "ratios": ratios, "hist": hist, "metals": metals}


def macro_state(now):
    """全球盘（金/油/汇）交易时段近似：周一 06:00 – 周六 05:00，每日 05:00–06:00 结算。"""
    wd, t = now.weekday(), now.time()
    if wd == 5:  # 周六
        return "交易中" if t < dt.time(5, 0) else "休市"
    if wd == 6:
        return "休市"
    if dt.time(5, 0) <= t < dt.time(6, 0):
        return "结算中"
    return "交易中"


def build_macro_summary(macro):
    q = macro["quotes"]
    cny = macro["cny"]
    ratios = macro["ratios"]
    parts = []

    def d(key):
        v = (q.get(key) or {}).get("pct")
        return "" if v is None else f"（{v:+.2f}%）"

    if q.get("xau"):
        parts.append(f"现货黄金 {q['xau']['price']:.2f} 美元/盎司{d('xau')}")
    if q.get("wti"):
        parts.append(f"WTI 原油 {q['wti']['price']:.2f} 美元/桶{d('wti')}")
    if q.get("brent"):
        parts.append(f"布伦特 {q['brent']['price']:.2f} 美元/桶{d('brent')}")
    if q.get("usdjpy"):
        parts.append(f"美元兑日元 {q['usdjpy']['price']:.2f}{d('usdjpy')}")
    line1 = "，".join(parts) + "。"

    parts2 = []
    if cny.get("gold_per_g"):
        t = f"黄金折人民币 {cny['gold_per_g']:.2f} 元/克"
        if q.get("au0"):
            t += f"（沪金主力 {q['au0']['price']:.2f}，溢价 {cny.get('au0_premium'):+.2f}%）"
        parts2.append(t)
    if cny.get("wti_per_bbl"):
        t = f"WTI 折人民币 {cny['wti_per_bbl']:.1f} 元/桶"
        if q.get("sc"):
            t += f"（上海原油 {q['sc']['price']:.1f}，对布油折算 {cny.get('sc_premium_brent'):+.2f}%）"
        parts2.append(t)
    if cny.get("jpy100_calc"):
        parts2.append(f"100 日元 = {cny['jpy100_calc']:.4f} 元")
    line2 = "，".join(parts2) + "。"

    extra = []
    if ratios.get("gold_oil"):
        extra.append(f"金油比 {ratios['gold_oil']:.1f}")
    if ratios.get("gold_silver"):
        extra.append(f"金银比 {ratios['gold_silver']:.1f}")
    if q.get("usdcnh"):
        extra.append(f"离岸人民币 {q['usdcnh']['price']:.4f}")
    if q.get("dxy"):
        extra.append(f"美元指数 {q['dxy']['price']:.2f}")
    return line1 + line2 + ("、".join(extra) + "。" if extra else "")


# ---------------------------------------------------------------- 矿业期货（有色/黑色/铁合金/新能源金属）

# (key, 名称, 新浪报价代码, 东财主连日线 secid, 单位)；国际盘另标 unit
METALS = [
    ("有色", [
        ("cu", "沪铜", "nf_CU0", "113.cum", "元/吨"),
        ("al", "沪铝", "nf_AL0", "113.alm", "元/吨"),
        ("zn", "沪锌", "nf_ZN0", "113.znm", "元/吨"),
        ("ni", "沪镍", "nf_NI0", "113.nim", "元/吨"),
        ("sn", "沪锡", "nf_SN0", "113.snm", "元/吨"),
        ("pb", "沪铅", "nf_PB0", "113.pbm", "元/吨"),
        ("ao", "氧化铝", "nf_AO0", "113.aom", "元/吨"),
        ("ss", "不锈钢", "nf_SS0", "113.ssm", "元/吨"),
        ("bc", "国际铜", "nf_BC0", "142.bcm", "元/吨"),
    ]),
    ("黑色", [
        ("i", "铁矿石", "nf_I0", "114.im", "元/吨"),
        ("rb", "螺纹钢", "nf_RB0", "113.rbm", "元/吨"),
        ("hc", "热卷", "nf_HC0", "113.hcm", "元/吨"),
        ("j", "焦炭", "nf_J0", "114.jm", "元/吨"),
        ("jm", "焦煤", "nf_JM0", "114.jmm", "元/吨"),
    ]),
    ("铁合金", [
        ("sf", "硅铁", "nf_SF0", "115.sfm", "元/吨"),
        ("sm", "锰硅", "nf_SM0", "115.smm", "元/吨"),
    ]),
    ("新能源金属", [
        ("lc", "碳酸锂", "nf_LC0", "225.lcm", "元/吨"),
        ("si", "工业硅", "nf_SI0", "225.sim", "元/吨"),
        ("ps", "多晶硅", "nf_PS0", "225.psm", "元/吨"),
    ]),
]

# 国际盘：新浪 hf_ 报价 + Sina 全球期货日线 symbol；单位 usd=美元/吨，cent_lb=美分/磅
INTL_METALS = [
    ("lme_cu", "伦铜", "hf_CAD", "CAD", "美元/吨", "usd"),
    ("lme_al", "伦铝", "hf_AHD", "AHD", "美元/吨", "usd"),
    ("lme_zn", "伦锌", "hf_ZSD", "ZSD", "美元/吨", "usd"),
    ("comex_cu", "美铜", "hf_HG", "HG", "美分/磅", "cent_lb"),
]

LB_PER_TONNE = 2204.6226


def fetch_metals(usdcny):
    """矿业期货：新浪取报价（国内连续 + LME/COMEX），东财取国内主连日线（过滤夜盘次日 K 线）。"""
    today = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    domestic = [(k, n, c, em, u) for _, items in METALS for (k, n, c, em, u) in items]
    intl = [(k, n, c, sym, u, kind) for (k, n, c, sym, u, kind) in INTL_METALS]
    raw = fetch_sina([c for _, _, c, _, _ in domestic] + [c for _, _, c, _, _, _ in intl])
    usdcny = usdcny or 1.0

    groups = []
    hist = {}
    flat = []
    for gname, items in METALS:
        rows = []
        for k, n, code, em, unit in items:
            f = raw.get(code)
            if not f:
                continue
            q = quote_sina_nf(f, n) if code.startswith("nf_") else quote_sina_hf(f, n)
            if q.get("price") is None:
                continue
            q.update({"key": k, "unit": unit, "group": gname})
            rows.append(q)
            flat.append({"key": k, "name": n, "price": q["price"], "pct": q["pct"], "unit": unit})
        if rows:
            groups.append({"name": gname, "items": rows})

    intl_rows = []
    for k, n, code, sym, unit, kind in intl:
        f = raw.get(code)
        if not f:
            continue
        q = quote_sina_hf(f, n)
        if q.get("price") is None:
            continue
        if kind == "usd":
            cny_t = q["price"] * usdcny
        else:  # 美分/磅 -> 元/吨
            cny_t = q["price"] / 100 * LB_PER_TONNE * usdcny
        q.update({"key": k, "unit": unit, "group": "国际", "cny_per_t": round(cny_t, 0)})
        intl_rows.append(q)
        flat.append({"key": k, "name": n, "price": q["price"], "pct": q["pct"], "unit": unit})
    if intl_rows:
        groups.append({"name": "国际（折人民币）", "items": intl_rows})

    # 30 日历史（国内主连日线并发；国际用新浪全球期货日线）
    def job(k, em):
        try:
            return k, [r["close"] for r in fetch_em_fut_hist(em, today)]
        except Exception:  # noqa: BLE001
            return k, []
    with ThreadPoolExecutor(10) as ex:
        for k, rows in ex.map(lambda t: job(t[0], t[1]), [(k, em) for (k, _, _, em, _) in domestic]):
            hist[k] = rows
    def ijob(k, sym):
        try:
            return k, [r["close"] for r in fetch_sina_hist_global(sym)]
        except Exception:  # noqa: BLE001
            return k, []
    with ThreadPoolExecutor(4) as ex:
        for k, rows in ex.map(lambda t: ijob(t[0], t[1]), [(k, sym) for (k, _, _, sym, _, _) in intl]):
            hist[k] = rows

    for g in groups:
        for it in g["items"]:
            h = hist.get(it["key"]) or []
            it["hist"] = h
            it["ret30"] = round((h[-1] / h[0] - 1) * 100, 2) if len(h) > 1 and h[0] else None
            if "cny_per_t" in it and it["cny_per_t"]:
                it["ret30"] = it["ret30"]

    # 内外盘：LME 不含税折算 ×1.13(增值税) 对比沪铜；BC 为保税/不含税
    by = {it["key"]: it for g in groups for it in g["items"]}
    cross = {}
    def gv(k):
        return (by.get(k) or {}).get("price")

    if gv("lme_cu") and usdcny:
        lme_cny = gv("lme_cu") * usdcny
        cross["lme_cu_cny"] = round(lme_cny, 0)
        cross["lme_cu_tax"] = round(lme_cny * 1.13, 0)
        if gv("cu"):
            cross["cu"] = gv("cu")
            cross["cu_premium"] = round((gv("cu") / (lme_cny * 1.13) - 1) * 100, 2)
        if gv("bc"):
            cross["bc"] = gv("bc")
            cross["bc_premium"] = round((gv("bc") / lme_cny - 1) * 100, 2)
        if gv("comex_cu"):
            comex_cny = gv("comex_cu") / 100 * LB_PER_TONNE * usdcny
            cross["comex_cu_cny"] = round(comex_cny, 0)
            cross["comex_vs_lme"] = round((comex_cny / lme_cny - 1) * 100, 2)
    if gv("lme_al") and usdcny:
        lme_al_cny = gv("lme_al") * usdcny
        cross["lme_al_cny"] = round(lme_al_cny, 0)
        cross["lme_al_tax"] = round(lme_al_cny * 1.13, 0)
        if gv("al"):
            cross["al"] = gv("al")
            cross["al_premium"] = round((gv("al") / (lme_al_cny * 1.13) - 1) * 100, 2)

    rank = sorted([f for f in flat], key=lambda x: (x["pct"] if x["pct"] is not None else -999), reverse=True)
    return {"groups": groups, "hist": hist, "rank": rank, "cross": cross}

# ---------------------------------------------------------------- 组装

def market_state(now):
    if now.weekday() >= 5:
        return "休市"
    t = now.time()
    if t < dt.time(9, 15):
        return "盘前"
    if t < dt.time(9, 30):
        return "集合竞价"
    if t <= dt.time(11, 30) or dt.time(13, 0) <= t <= dt.time(15, 0):
        return "盘中"
    if dt.time(11, 30) < t < dt.time(13, 0):
        return "午间休市"
    return "已收盘"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def pct_text(v):
    return f"{v:+.2f}%" if isinstance(v, (int, float)) else "-"


def build_summary(snap):
    idx = snap["indices"]
    holiday_note = None
    if snap["meta"].get("trading_today") is False:
        holiday_note = f"今日休市，以下为上一交易日（{snap['meta']['trade_date']}）收盘数据。"
    breadth = snap["breadth"]
    limit_up = snap["limit_up"]["count"]
    limit_down = snap["limit_down"]["count"]
    boards = snap["boards"]
    parts = []
    if holiday_note:
        parts.append(holiday_note)
    ups = [i for i in idx if (i["pct"] or 0) > 0]
    heads = "、".join(f"{i['name']} {pct_text(i['pct'])}" for i in idx[:3])
    if len(ups) >= 6:
        tone = "指数普涨"
    elif len(ups) <= 2:
        tone = "指数普跌"
    else:
        tone = "指数分化"
    strongest = max(idx, key=lambda i: i["pct"] or -99)
    weakest = min(idx, key=lambda i: i["pct"] or 99)
    parts.append(f"{tone}：{heads}；最强 {strongest['name']} {pct_text(strongest['pct'])}，"
                 f"最弱 {weakest['name']} {pct_text(weakest['pct'])}。")
    if breadth:
        up, down = breadth["up"], breadth["down"]
        ratio = up / max(1, up + down) * 100
        parts.append(f"全市场 {up:,} 涨 / {down:,} 跌（上涨占比 {ratio:.0f}%，"
                     f"涨跌中位数 {pct_text(breadth['median'])}），涨停 {limit_up} 家、跌停 {limit_down} 家。")
    amt = snap.get("amount", {}).get("today")
    prev = snap.get("amount", {}).get("prev")
    if amt:
        tail = ""
        if prev and snap["meta"]["state"] != "盘中":
            chg = (amt / prev - 1) * 100
            tail = f"，较上日 {chg:+.1f}%"
        elif snap["meta"]["state"] == "盘中":
            tail = "（盘中累计）"
        parts.append(f"沪深两市成交 {fmt_amount(amt)}{tail}。")
    top = boards.get("industry_top") or []
    bottom = boards.get("industry_bottom") or []
    if top and bottom:
        parts.append(f"板块领涨 {'、'.join(b['name'] for b in top[:2])}，"
                     f"领跌 {'、'.join(b['name'] for b in bottom[:2])}。")
    return "".join(parts)


def main():
    now = dt.datetime.now(TZ)
    os.makedirs(SNAP_DIR, exist_ok=True)
    degraded = []
    # 先载入「上次快照 + 兜底快照」，供各源失败时回退（必须在取数之前）
    prev = load_json(os.path.join(DATA_DIR, "latest.json"), {})
    fb = load_json(FALLBACK_PATH, {})

    def safe(name, fn, fallback=None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            degraded.append(f"{name}: {e}")
            print(f"[warn] {name} failed: {e}", file=sys.stderr)
            return fallback

    indices = safe("indices", fetch_indices)
    sparks, spark_dates, amt_hist = safe("kline", fetch_index_history, ({}, [], []))
    boards = safe("boards", fetch_boards, {})
    if not boards:
        boards = fb.get("boards") or {}
    breadth = safe("breadth", fetch_breadth)
    wl = safe("watchlist", fetch_watchlist, [])
    if not wl:
        wl = fb.get("watchlist") or []
    news = safe("news", fetch_news, [])

    if not indices:
        indices = prev.get("indices") or []
    if not indices:
        print("[fatal] 无指数数据，保留旧 data.js 不变", file=sys.stderr)
        return 2

    # 交易日：指数 K 线最后一根（盘中当根即当日）
    # 交易日一律以指数 K 线最后一根为准：盘中当根即当日，盘前/周末/节假日自动回落到上一交易日，
    # 避免 09:00 盘前把“昨天收盘”伪装成今天的行情。
    trade_date = spark_dates[-1] if spark_dates else now.strftime("%Y-%m-%d")

    qtime = safe("quote_time", fetch_quote_time)
    # 拿不到报价时间时不判断休市（避免接口 502 把正常交易日误标成“今日不开市”）
    trading_today = True if qtime is None else (qtime.strftime("%Y-%m-%d") == now.strftime("%Y-%m-%d"))
    state = market_state(now)
    if now.weekday() < 5 and not trading_today and now.time() >= dt.time(9, 30):
        state = "休市（今日不开市）"

    macro = safe("macro", fetch_macro)
    pools = safe("limit_pools", lambda: fetch_limit_pools(trade_date), {}) or {}
    if (not pools) or (pools.get("limit_up", {}).get("count") is None
                       and pools.get("limit_down", {}).get("count") is None
                       and pools.get("zhaban", {}).get("count") is None):
        pools = {k: fb.get(k) for k in ("limit_up", "limit_down", "zhaban") if fb.get(k)}
    limit_up = pools.get("limit_up") or {"count": None, "items": [], "max_lbc": 0}
    limit_down = pools.get("limit_down") or {"count": None, "items": []}
    zhaban = pools.get("zhaban") or {"count": None, "items": []}
    if limit_up.get("count") is None:
        limit_up["count"] = prev.get("limit_up", {}).get("count")
    if limit_down.get("count") is None:
        limit_down["count"] = prev.get("limit_down", {}).get("count")

    if not breadth:
        breadth = prev.get("breadth") or fb.get("breadth")

    # 成交额：今日（沪+深 K 线）与上一交易日
    today_amt = None
    prev_amt = None
    if amt_hist:
        today_amt = amt_hist[-1]["amount"]
        prev_amt = amt_hist[-2]["amount"] if len(amt_hist) > 1 else None
    amount = {"today": today_amt, "prev": prev_amt,
              "history": [{"date": a["date"], "amount": a["amount"]} for a in amt_hist[-20:]]}

    # 情绪分（0-100，50 中性）：上涨占比 40% + 涨停/跌停比 30% + 中位数 30%
    score = None
    lu, ld = limit_up.get("count"), limit_down.get("count")
    if breadth:
        up, down = breadth["up"], breadth["down"]
        ratio = up / max(1, up + down)
        lu = lu or 0
        ld = ld or 0
        pool_ratio = lu / max(1, lu + ld + 5)
        med = max(-5.0, min(5.0, breadth["median"] or 0))
        score = round(max(0, min(100, 100 * (0.4 * ratio + 0.3 * pool_ratio + 0.3 * (0.5 + med / 10)))))

    # 历史累积（按交易日）
    hist_path = HISTORY_PATH
    history = load_json(hist_path, [])
    if breadth and score is not None:
        entry = {
            "date": trade_date,
            "up": breadth["up"], "down": breadth["down"], "flat": breadth["flat"],
            "amount": amount["today"] or 0, "median": breadth["median"],
            "limit_up": lu, "limit_down": ld, "score": score,
            "idx": {i["code"]: i["pct"] for i in indices[:4]},
        }
        days = {e["date"]: e for e in history}
        days[trade_date] = entry
        history = [days[k] for k in sorted(days)][-HISTORY_KEEP:]

    snap = {
        "meta": {
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "generated_ts": int(now.timestamp()),
            "trade_date": trade_date,
            "state": state,
            "trading_today": trading_today,
            "degraded": degraded,
            "sources": ["东方财富 push2/push2his/push2ex", "东方财富快讯"],
        },
        "indices": indices,
        "sparks": sparks,
        "breadth": breadth,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "zhaban": zhaban,
        "boards": boards,
        "watchlist": wl,
        "news": news,
        "amount": amount,
        "score": score,
        "history": history[-20:],
        "macro": macro,
    }
    # A股兜底快照：仅在数据全绿时刷新，供后续失败运行取用
    if not degraded and breadth and wl and boards:
        save_json(FALLBACK_PATH, {k: snap.get(k) for k in (
            "meta", "indices", "sparks", "breadth", "limit_up", "limit_down", "zhaban",
            "boards", "watchlist", "amount", "score", "history")})
    snap["summary_ashare"] = build_summary(snap)
    snap["summary"] = build_macro_summary(macro) if macro else snap["summary_ashare"]
    snap["macro"]["state"] = macro_state(now)
    snap["macro"]["as_of"] = now.strftime("%Y-%m-%d %H:%M:%S")

    save_json(os.path.join(DATA_DIR, "latest.json"), snap)
    save_json(hist_path, history)
    save_json(os.path.join(SNAP_DIR, f"{trade_date}.json"), snap)

    # 清理 90 天前快照
    cutoff = (now.date() - dt.timedelta(days=90)).strftime("%Y-%m-%d")
    for f in os.listdir(SNAP_DIR):
        if f.endswith(".json") and f[:10] < cutoff:
            try:
                os.remove(os.path.join(SNAP_DIR, f))
            except OSError:
                pass

    # data.js：file:// 下 <script src> 可直接加载
    js = "window.MKT = " + json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + ";\n"
    tmp = os.path.join(ROOT, "data.js.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(js)
    os.replace(tmp, os.path.join(ROOT, "data.js"))

    if macro and macro.get("metals") and macro["metals"].get("rank"):
        r0 = macro["metals"]["rank"][0]
        print(f"[metals] {len(macro['metals']['rank'])} 个品种 · 领涨 {r0['name']} {r0['pct']}%")
    if macro and macro.get("quotes", {}).get("xau"):
        q = macro["quotes"]
        print(f"[macro] XAU {q['xau']['price']:.2f} {q['xau'].get('pct')}% | "
              f"WTI {q['wti']['price']:.2f} | USDJPY {q['usdjpy']['price']:.2f} | "
              f"CNY金 {macro['cny'].get('gold_per_g')}/克 JPY100 {macro['cny'].get('jpy100_calc')}")
    print(f"[ok] {snap['meta']['trade_date']} {snap['meta']['state']} | "
          f"{len(indices)} 指数 | 涨{breadth['up'] if breadth else '?'}/跌{breadth['down'] if breadth else '?'} | "
          f"涨停{lu}/跌停{ld} | 情绪{score} | 成交{fmt_amount(amount['today'])} | "
          f"自选{sum(len(g['items']) for g in wl)} | 快讯{len(news)}" if breadth else "[ok] partial")
    if degraded:
        print("[degraded] " + "; ".join(degraded), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())