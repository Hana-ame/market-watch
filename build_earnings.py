#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_earnings.py — 财报页确定性渲染（不使用 LLM）
==================================================
读 data/earnings_raw.json（真实抓取数据），渲染 dist/earnings/index.html。

为什么不走 LLM：原先用 Pi Agent(Gemini) 生成点评，实测会编造——把真实数字
丢掉、写成「现金流极其强劲」「结构性调整」等无根据套话，甚至对缺失数据的
公司凭空点评。改为此脚本：数字直接来自抓取，点评用规则从数字推导，杜绝编造。
"""

import html
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "data", "earnings_raw.json")
OUT_DIR = os.path.join(ROOT, "dist", "earnings")
OUT = os.path.join(OUT_DIR, "index.html")

OZ = None  # 占位，保持结构清晰


def yi(v):
    """金额转「亿/万亿」中文单位；负数保留负号。"""
    if v is None:
        return "—"
    neg = v < 0
    a = abs(v)
    if a >= 1e12:
        s = f"{a/1e12:.2f} 万亿"
    elif a >= 1e8:
        s = f"{a/1e8:.0f} 亿"
    elif a >= 1e4:
        s = f"{a/1e4:.0f} 万"
    else:
        s = f"{a:.2f}"
    return ("-" + s) if neg else s


def pct(v):
    if v is None:
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def cls(v):
    if v is None:
        return ""
    return "up" if v > 0 else ("down" if v < 0 else "")


def esc(s):
    return html.escape(str(s)) if s is not None else ""


# ------------------------------------------------------ 点评规则（纯数字推导）

def company_comment(rec):
    """从真实数字推导点评。只用数字能支撑的事实，不编造定性描述。"""
    m = rec.get("metrics") or {}
    parts = []
    # 营收同比
    ryoy = (m.get("revenue_yoy") or {}).get("value")
    if ryoy is not None:
        if ryoy > 0:
            parts.append(f"营收同比 +{ryoy:.1f}%")
        elif ryoy < 0:
            parts.append(f"营收同比 {ryoy:.1f}%")
    # 净利同比
    nyoy = (m.get("net_income_yoy") or {}).get("value")
    if nyoy is not None:
        tag = "+" if nyoy > 0 else ""
        parts.append(f"净利同比 {tag}{nyoy:.1f}%")
    # 毛利率
    gm = (m.get("gross_margin") or {}).get("value")
    if gm is not None:
        parts.append(f"毛利率 {gm:.1f}%")
    # ROE
    roe = (m.get("roe") or {}).get("value")
    if roe is not None:
        parts.append(f"ROE {roe:.1f}%")
    # 净利率（营收与净利都在时算）
    rev = (m.get("revenue") or {}).get("value")
    ni = (m.get("net_income") or {}).get("value")
    if rev and ni and rev > 0:
        parts.append(f"净利率 {ni/rev*100:.1f}%")
    # 亏损标记
    if ni is not None and ni < 0:
        parts.append("本期净亏损")
    return " · ".join(parts) if parts else "数据项不足，暂无同比/利润率指标"


def card(rec):
    """单家公司卡片。缺失指标显示 —，不编造。"""
    m = rec.get("metrics") or {}
    name = rec.get("name") or rec.get("entityName") or rec.get("code") or "—"
    code = rec.get("ticker") or rec.get("code") or ""
    end = rec.get("report_end") or rec.get("report_date") or ""
    ok = rec.get("ok")

    if not ok:
        err = rec.get("error") or "数据缺失"
        return f'<div class="card"><div class="cname">{esc(name)} <span class="ccode">{esc(code)}</span></div><div class="noerr">数据缺失：{esc(err)}</div></div>'

    def row(label, key, unit=""):
        mv = m.get(key) or {}
        v = mv.get("value")
        if v is None:
            return f'<div class="mrow"><span class="ml">{label}</span><span class="mv">—</span></div>'
        u = mv.get("unit", unit)
        if key in ("revenue", "net_income") and u in ("USD", "CNY", "HKD"):
            shown = yi(v) + " " + u
        elif u == "%":
            shown = f"{v:.2f}%"
        else:
            shown = f"{v:,.2f}"
        c = cls(v) if key in ("net_income", "revenue_yoy", "net_income_yoy") else ""
        return (f'<div class="mrow"><span class="ml">{label}</span>'
                f'<span class="mv {c}">{shown}</span></div>')

    rows = "".join([
        row("营收", "revenue"),
        row("净利润", "net_income"),
        row("毛利率", "gross_margin"),
        row("营收同比", "revenue_yoy"),
        row("净利同比", "net_income_yoy"),
        row("ROE", "roe"),
        row("EPS", "eps"),
    ])
    return (
        f'<div class="card"><div class="cname">{esc(name)} '
        f'<span class="ccode">{esc(code)}</span></div>'
        f'<div class="cend">报告期 {esc(end)}</div>'
        f'<div class="mgrid">{rows}</div>'
        f'<div class="ccomment">{esc(company_comment(rec))}</div></div>'
    )


def section(title, market, note):
    if not isinstance(market, list) or not market:
        return (f'<h2>{esc(title)}</h2><p class="degraded">该市场数据抓取失败：'
                f'{esc((market or {}).get("error", "无数据") if isinstance(market, dict) else "无数据")}</p>')
    cards = "".join(card(r) for r in market)
    return f'<h2>{esc(title)}</h2><div class="grid">{cards}</div>'


def build():
    d = json.load(open(RAW, encoding="utf-8"))
    gen = d.get("generated_at", "")
    mk = d.get("markets", {})
    us, cn, hk = mk.get("us"), mk.get("cn"), mk.get("hk")

    body = section("美股 (US)", us, "")
    body += section("A股 (CN)", cn, "")
    body += section("港股 (HK)", hk, "")

    html_ = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>财报数据 · market-watch</title>
<style>
body{{margin:0;background:#0b1220;color:#d7e0ea;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.6}}
.wrap{{max-width:1000px;margin:0 auto;padding:20px}}
h1{{font-size:1.5rem;color:#e8eef6}}
.meta{{color:#8496ab;font-size:.85rem;margin-bottom:18px}}
.note{{background:#121a2b;border-left:3px solid #c9a227;padding:10px 14px;border-radius:6px;font-size:.9rem;margin:14px 0 22px}}
h2{{font-size:1.15rem;color:#c9a227;margin:26px 0 12px;padding-bottom:6px;border-bottom:1px solid #1f2a3d}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}}
.card{{background:#121a2b;border:1px solid #1f2a3d;border-radius:10px;padding:14px 16px}}
.cname{{font-size:1.05rem;font-weight:700;color:#e8eef6}}
.ccode{{color:#8496ab;font-size:.8rem;font-family:ui-monospace,monospace}}
.cend{{color:#8496ab;font-size:.78rem;margin:2px 0 10px}}
.mgrid{{margin:8px 0}}
.mrow{{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px dashed #1a2434;font-size:.88rem}}
.ml{{color:#8496ab}}
.mv{{font-family:ui-monospace,monospace;color:#d7e0ea}}
.mv.up{{color:#ff6b78}}.mv.down{{color:#0ecb81}}
.ccomment{{margin-top:10px;font-size:.82rem;color:#a9b8c9}}
.noerr{{color:#e0a458;font-size:.88rem}}
.degraded{{color:#e0a458}}
.risk{{background:#121a2b;border-radius:8px;padding:12px 16px;font-size:.85rem;color:#a9b8c9;margin-top:22px}}
footer{{margin-top:26px;padding-top:14px;border-top:1px solid #1f2a3d;color:#6b7c90;font-size:.78rem}}
</style></head><body><div class="wrap">
<h1>财报数据 · market-watch</h1>
<div class="meta">数据截至 {esc(gen)} · 数据来自公开渠道，本页不含 AI 生成内容</div>
<div class="note">以下数字直接来自 SEC EDGAR（美股 XBRL）与东方财富（A股业绩报表 / 港股财报）抓取，未做任何 AI 加工或推测。<b>点评由数字规则推导</b>（同比/利润率），不包含主观判断。</div>
{body}
<div class="risk"><b>口径提示：</b>美股 XBRL 指标已对齐同一报告期；A股为业绩报表快照、港股为累计财报，跨市场披露频率与口径不同，不宜直接横向比较。数据存在时滞。</div>
<footer>数据来源：SEC EDGAR（美股 XBRL）、东方财富（A股业绩报表 / 港股主要财务指标）。仅供参考与学习交流，不构成任何投资建议或买卖依据。</footer>
</div></body></html>"""
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_)
    n_us = len(us) if isinstance(us, list) else 0
    n_cn = len(cn) if isinstance(cn, list) else 0
    n_hk = len(hk) if isinstance(hk, list) else 0
    print(f"[ok] {OUT} 渲染：美股 {n_us}、A股 {n_cn}、港股 {n_hk} 家（确定性，无 AI）")


if __name__ == "__main__":
    build()
