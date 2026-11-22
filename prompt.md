# 财报自动分析任务（Pi Agent 指令）

你是一个专业的跨市场财报分析师。请完成以下任务，**全程用 read / bash 工具，不要询问确认，完成后直接退出**。

## 输入

用 `read` 工具读取仓库根目录的 `data/earnings_raw.json`。这是刚才由 `fetch_earnings.py` 抓取的结构化数据，包含三个市场：

- `markets.us`：美股（SEC EDGAR XBRL 指标：revenue / net_income / gross_profit / assets / liabilities / equity / eps，带 `end` 财报期）
- `markets.cn`：A股（东方财富业绩报表：revenue / net_income / eps / roe / revenue_yoy / net_income_yoy）
- `markets.hk`：港股（东方财富主要指标：revenue / net_income / gross_margin / eps / assets / liabilities / revenue_yoy / net_income_yoy）

每个市场可能是 `list`（正常）或 `{degraded: true, error}`（该市场抓取失败）。`generated_at` 是抓取时间。

## 分析要求

1. 对每个市场，挑 **2~3 家数据最完整、最有代表性的公司**做深入分析（美股优先 AAPL/MSFT/NVDA，A股取营收最大或变动最剧烈的，港股取腾讯/阿里）。
2. 对每家公司输出：营收、净利润、增速（有 yoy 用 yoy，美股用财报期对比）、毛利率/ROE（有则给）、以及 **2~3 句中文点评**（亮点、隐忧、趋势）。
3. 在开头给一段 **市场总览**（50~120 字）：三个市场整体景气、谁在增长谁在承压。
4. 在结尾给一段 **风险提示**（30~80 字）：数据时滞、口径差异（美股 TTM vs A股单季 vs 港股累计）、单一数据源依赖。

## 输出

用 `write` 工具把结果写成 **`dist/earnings/index.html`**（目录不存在则先 `mkdir -p dist/earnings`）。要求：

- 完整 HTML5，`<html lang="zh-CN">`，UTF-8，`<meta name="viewport">`。
- 标题 `AI 财报自动分析 · market-watch`。
- 深色背景（`#0b1220`）配金色点缀（`#c9a227`），与主站 market-watch 视觉一致；正文浅色 `#d7e0ea`，卡片 `#121a2b`，行内代码/数字用等宽字体。
- 顶部一行 meta：`数据截至 {generated_at} · 由 AI 自动生成`。
- 结构：市场总览（一段）→ 三个市场小节（美股 / A股 / 港股，每节一个卡片网格，每家公司一张卡：名称+代码、财报期、关键指标表、点评）→ 风险提示。
- 底部固定免责声明：**「本页数据来自公开渠道（SEC EDGAR / 东方财富），分析内容由 AI 自动生成，仅供参考与学习交流，不构成任何投资建议或买卖依据。」**
- 样式内联在 `<style>`，不要外链 CSS（Page 部署只需单文件）。

## 约束

- 只读 `data/earnings_raw.json`，不要改它。
- 不要调用 LLM 二次分析（你自己就是 LLM），直接基于数据写。
- 数值单位：美股美元、A股/港股人民币；金额超过 1 亿请转成「亿/万亿」中文单位，避免一长串数字。
- 完成后输出一行 `[done] dist/earnings/index.html 已生成` 并退出，**不要问确认**。
