#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_earnings.py — 财报数据确定性抓取，供 Pi Agent 分析。
================================================================
输出 data/earnings_raw.json（结构化，含三市场数据 + 免责声明 + 抓取时间），
供 prompt.md 里的 Pi Agent 读取并生成 dist/earnings/index.html。

数据源（均公开、已验证可达，2026-11-23）：
  - 美股 SEC EDGAR  XBRL companyfacts（https://data.sec.gov/api/xbrl/companyfacts/CIK######.json）
  - A股 东方财富     业绩报表 RPT_LICO_FN_CPD（datacenter-web）
  - 港股 东方财富     财报 RPT_HKF10_FN_*（datacenter-web，腾讯 00700 示例）

用法：python3 fetch_earnings.py [--no-cache] [--max TICKERS]
安全线：SEC 必须带合规 User-Agent；东财需 Referer。失败不阻断，缺失市场记为 degraded。
"""

import datetime as dt
import json
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "earnings_raw.json")
TZ = dt.timezone(dt.timedelta(hours=8))

# 合规 User-Agent（SEC 规定：脚本须自报身份，否则可能 403）
SEC_UA = "research lumin@moonchan.xyz"
EM_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 默认跟踪的标的（可被 --max / 参数覆盖；A股/港股取业绩报表靠前样本，美股用固定龙头）
US_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"]
CN_QUERY = {"pageSize": 6, "pageNumber": 1, "sortColumns": "UPDATE_DATE", "sortTypes": -1}
HK_SECU = ["00700.HK", "09988.HK", "03690.HK"]  # 腾讯 / 阿里 / 美团

# us-gaap 指标别名（按优先级 fallback），key -> (标签, 含义)
GAAP_ALIASES = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "IncomeLossFromContinuingOperations"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "eps": ["EarningsPerShareBasic", "EarningsPerShareDiluted"],
}

SEC_HEADERS = {"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}
EM_HEADERS = {"User-Agent": EM_UA, "Referer": "https://quote.eastmoney.com/"}

sec_s = requests.Session()
sec_s.trust_env = False
sec_s.headers.update(SEC_HEADERS)
em_s = requests.Session()
em_s.trust_env = False
em_s.headers.update(EM_HEADERS)


def get_json(session, url, tries=3, timeout=20):
    last = None
    for i in range(tries):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"GET failed: {url[:110]} -> {last}")


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- SEC 美股

def _cik_map():
    """ticker -> (cik, title)。company_tickers.json 约 1 万条。"""
    d = get_json(sec_s, "https://www.sec.gov/files/company_tickers.json")
    out = {}
    for v in d.values():
        t = (v.get("ticker") or "").upper()
        if t:
            out[t] = (v.get("cik_str"), v.get("title"))
    return out


def _gaap_pick(facts, aliases):
    """从 us-gaap facts 里按别名优先级取最近一期 (val, end, unit)。"""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for key in aliases:
        if key not in gaap:
            continue
        units = gaap[key].get("units", {})
        best = None
        for unit, arr in units.items():
            if not arr:
                continue
            last = arr[-1]
            if best is None or last.get("end", "") > best[0]:
                best = (last.get("end", ""), last.get("val"), unit)
        if best and best[1] is not None:
            return best[2], best[1], best[0]
    return None


def fetch_sec(ticks):
    cmap = _cik_map()
    out = []
    for t in ticks:
        t = t.upper()
        rec = {"ticker": t, "source": "SEC EDGAR", "ok": False, "error": None}
        if t not in cmap:
            rec["error"] = "ticker not found"
            out.append(rec)
            continue
        cik, title = cmap[t]
        rec["name"] = title
        try:
            d = get_json(sec_s, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
            rec["entityName"] = d.get("entityName")
            rec["metrics"] = {}
            for label, aliases in GAAP_ALIASES.items():
                picked = _gaap_pick(d, aliases)
                if picked:
                    unit, val, end = picked
                    rec["metrics"][label] = {"value": val, "unit": unit, "end": end}
            rec["ok"] = bool(rec["metrics"])
            if not rec["metrics"]:
                rec["error"] = "no us-gaap metrics"
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:80]
        out.append(rec)
    return out


# ---------------------------------------------------------------- 东财 A股

def fetch_cn():
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPT_LICO_FN_CPD&columns=ALL"
           f"&pageSize={CN_QUERY['pageSize']}&pageNumber={CN_QUERY['pageNumber']}"
           f"&sortColumns={CN_QUERY['sortColumns']}&sortTypes={CN_QUERY['sortTypes']}")
    d = get_json(em_s, url)
    rows = (d.get("result") or {}).get("data") or []
    out = []
    for r in rows:
        out.append({
            "code": r.get("SECURITY_CODE"),
            "name": r.get("SECURITY_NAME_ABBR"),
            "market": r.get("TRADE_MARKET"),
            "report_date": r.get("REPORTDATE"),
            "update_date": r.get("UPDATE_DATE"),
            "metrics": {
                "revenue": {"value": num(r.get("TOTAL_OPERATE_INCOME")), "unit": "CNY"},
                "net_income": {"value": num(r.get("PARENT_NETPROFIT")), "unit": "CNY"},
                "eps": {"value": num(r.get("BASIC_EPS")), "unit": "CNY"},
                "roe": {"value": num(r.get("WEIGHTAVG_ROE")), "unit": "%"},
                "revenue_yoy": {"value": num(r.get("YSTZ")), "unit": "%"},
                "net_income_yoy": {"value": num(r.get("SJLTZ")), "unit": "%"},
            },
            "source": "东方财富 RPT_LICO_FN_CPD",
            "ok": True,
        })
    return out


# ---------------------------------------------------------------- 东财港股

def fetch_hk(secus):
    out = []
    # 港股主要财务指标报表（含总资产/总负债/营收/净利/毛利率，腾讯示例已验证）
    for secu in secus:
        rec = {"code": secu, "source": "东方财富 RPT_HKF10_FN_MAININDICATOR", "ok": False, "error": None}
        try:
            url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
                   "?reportName=RPT_HKF10_FN_MAININDICATOR&columns=ALL"
                   f"&filter=(SECUCODE%3D%22{secu}%22)"
                   "&pageSize=2&sortColumns=REPORT_DATE&sortTypes=-1")
            d = get_json(em_s, url)
            rows = (d.get("result") or {}).get("data") or []
            if rows:
                r = rows[0]
                rec["name"] = r.get("SECURITY_NAME_ABBR")
                rec["report_date"] = (r.get("REPORT_DATE") or "")[:10]
                rec["metrics"] = {
                    "revenue": {"value": num(r.get("OPERATE_INCOME")), "unit": r.get("CURRENCY")},
                    "net_income": {"value": num(r.get("HOLDER_PROFIT")), "unit": r.get("CURRENCY")},
                    "gross_margin": {"value": num(r.get("GROSS_PROFIT_RATIO")), "unit": "%"},
                    "eps": {"value": num(r.get("BASIC_EPS")), "unit": r.get("CURRENCY")},
                    "assets": {"value": num(r.get("TOTAL_ASSETS")), "unit": r.get("CURRENCY")},
                    "liabilities": {"value": num(r.get("TOTAL_LIABILITIES")), "unit": r.get("CURRENCY")},
                    "revenue_yoy": {"value": num(r.get("OPERATE_INCOME_YOY")), "unit": "%"},
                    "net_income_yoy": {"value": num(r.get("HOLDER_PROFIT_YOY")), "unit": "%"},
                }
                rec["ok"] = True
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:80]
        out.append(rec)
    return out


# ---------------------------------------------------------------- 主流程

def main():
    no_cache = "--no-cache" in sys.argv
    max_n = 6
    if "--max" in sys.argv:
        try:
            max_n = int(sys.argv[sys.argv.index("--max") + 1])
        except (ValueError, IndexError):
            max_n = 6

    payload = {
        "generated_at": dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M %Z"),
        "source_note": ("数据来自公开渠道：SEC EDGAR（美股 XBRL）、东方财富（A股业绩报表 / 港股财报）。"
                        "由 AI 自动分析，仅供参考，不构成投资建议。"),
        "markets": {},
    }

    # 美股
    try:
        us = fetch_sec(US_TICKERS[:max_n])
        payload["markets"]["us"] = us
    except Exception as e:  # noqa: BLE001
        payload["markets"]["us"] = {"degraded": True, "error": str(e)[:120]}

    # A股
    try:
        cn = fetch_cn()
        payload["markets"]["cn"] = cn
    except Exception as e:  # noqa: BLE001
        payload["markets"]["cn"] = {"degraded": True, "error": str(e)[:120]}

    # 港股
    try:
        hk = fetch_hk(HK_SECU)
        payload["markets"]["hk"] = hk
    except Exception as e:  # noqa: BLE001
        payload["markets"]["hk"] = {"degraded": True, "error": str(e)[:120]}

    os.makedirs(DATA, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # 简报
    def count(m):
        return len(m) if isinstance(m, list) else 0
    print(f"[ok] earnings_raw.json 生成：美股 {count(payload['markets'].get('us'))} 家、"
          f"A股 {count(payload['markets'].get('cn'))} 家、港股 {count(payload['markets'].get('hk'))} 家")
    if not no_cache:
        # 写一份到仓库根供本地快速查看（可选）
        pass


if __name__ == "__main__":
    main()
