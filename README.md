# 每日看盘 · market-watch

本地运行的每日盯盘面板，**主看 XAUUSD 黄金、WTI/布伦特原油、USD/JPY 日元，并把三者折成人民币价**（元/克、元/桶、元/百日元）；另有「矿业期货」页签（有色 / 黑色 / 铁合金 / 新能源金属 / LME·COMEX 内外盘）和「A股盘面」页签（指数 / 涨跌宽度 / 板块 / 涨停梯队 / 自选股）。
页面本身不联网：行情由脚本写进同目录 `data.js`，浏览器直接打开 `index.html` 即可。

## 在线版（GitHub Pages）

**https://hana-ame.github.io/market-watch/** — 手机/任何设备直接打开，不用开电脑。

- 仓库：https://github.com/Hana-ame/market-watch （PUBLIC）
- GitHub Actions 每 15 分钟跑一次 `fetch_market.py`，把 `index.html` + 生成的 `data.js` 作为 Pages 产物部署；`data.js` 不入库（否则每 15 分钟一个提交会把仓库撑大）。
- `data/history.json` 每个交易日只回写一次（要求已收盘、无降级、当日尚未记录），用于「近期节奏」多日累积。
- 境外 runner 偶发被东财接口 502（页面右上角会显示「N 个数据源降级」）；全市场分页已降到 6 并发 + 每页 4 次指数退避，自选股拆两批独立重试，最近一次 run 无降级。

## 在线更新保障

GitHub 的 `schedule` 是 best-effort（可能延迟 5–20 分钟，冷启动/高负载时更久），所以：

1. **定时**：`*/15 * * * *`（UTC）触发 workflow；
2. **手动/兜底**：workflow 支持 `repository_dispatch`，可随时触发一次：
   ```sh
   gh api -X POST repos/Hana-ame/market-watch/dispatches -f event_type=refresh
   ```
3. **本机看门狗**：`remote_watchdog.sh`（WSL cron 每 15 分钟）读取线上 `data.js` 的 `generated_ts`，超过 30 分钟未更新才 dispatch；线上正常时只做一次 curl，不产生重复构建。
3. **A股兜底快照**：CI 在「已收盘 + 无降级」时把 A股区块（指数/宽度/板块/涨停/自选）写入 `data/fallback.json` 并在每个交易日回写一次；若某次境外取数被东财 502，脚本用该快照填充 A股页，页头标注「N 个数据源降级」，主看板块（金/油/日元/矿业）不受影响。
4. **push 触发**：改代码/文档推送 main 也会立即刷新并部署一次。

## 本地运行

上面的在线版不需要本机；想本地跑（Windows 双击打开、不依赖 GitHub）也行：

```
E:\market-watch\index.html
```

双击即可（Chrome / Edge）。页面每 60 秒自动重载一次 `data.js`，盘中无需手动刷新。

本地运行会把历史写到 `data/history.local.json`（被测仓库忽略），与线上 `data/history.json` 分开，避免弄脏 git 工作区。

## 三个页签

**核心品种**（默认）
1. 三张主卡：黄金（XAU/USD + 人民币折算 + 沪金主力/黄金 T+D/白银）、原油（WTI + 布伦特 + WTI 人民币折算 + 上海原油 SC0）、日元（USD/JPY + 100 日元兑人民币 + 在岸/离岸人民币 + 美元指数）。
2. 指标条：美元指数、金银比、金油比、黄金/WTI/日元 30 日涨跌。
3. 30 日归一化走势图（黄金、沪金、WTI、美元/日元、离岸人民币，基准 = 100）。
4. 折算与内外价差：黄金「国际金价 × USDCNY ÷ 31.1035」对沪金溢价；原油「WTI/布伦特 × USDCNY」对 SC 价差；日元「USDCNY ÷ USDJPY × 100」对官方 JPY/CNY 的 bp 差。
5. 品种一览表（现价 / 今日 / 30 日 / 人民币折算）与 7×24 快讯（黄金、原油、汇率关键词高亮）。

**矿业期货**
- 23 个品种分 5 组：有色（沪铜/铝/锌/镍/锡/铅/氧化铝/不锈钢/国际铜）、黑色（铁矿石/螺纹/热卷/焦炭/焦煤）、铁合金（硅铁/锰硅）、新能源金属（碳酸锂/工业硅/多晶硅）、国际（伦铜/伦铝/伦锌/美铜，附折人民币元/吨）。
- 每个品种：现价 + 今日 + 30 日 + 迷你走势；国际品种显示人民币折算。
- 30 日归一化对比（沪铜/伦铜/沪铝/铁矿石/碳酸锂/螺纹钢）、今日强弱榜、**铜铝内外盘价差**：LME 不含税折算 → ×1.13 含税折算 → 对比沪铜/沪铝溢价；国际铜 BC 保税价直接对 LME 比较；COMEX 美铜（美分/磅）折元/吨。

**A股盘面**
8 大指数（含 30 日走势）、涨跌家数与 13 档分布、情绪分与涨停/跌停/炸板、行业/概念板块、涨停梯队、自选股（A/HK/US）、成交额与情绪历史。

## 数据与刷新

- **新浪财经**：`hq.sinajs.cn`（伦敦金 XAU、伦敦银 XAG、WTI、布伦特、沪金主连 AU0、上海原油 SC0、黄金 T+D、在岸 USD/CNY、USD/JPY、JPY/CNY）；`stock2.finance.sina.com.cn`（外盘与国内期货日线）。该接口必须带 `Referer: https://finance.sina.com.cn`，否则返回空。
- **东方财富**：`push2`（A股、指数、美元指数 100.UDI、离岸人民币 133.USDCNH、自选股）、`push2his`（指数与外汇日线、国内期货主连日线：沪金 113.aum / 原油 142.scm / 沪铜 113.cum / 铁矿 114.im / 碳酸锂 225.lcm 等）、`push2ex`（涨停/跌停/炸板池）、`np-listapi`（7×24 快讯）。
- **矿业期货**：国内 19 个品种用新浪 `nf_CU0`/`nf_RB0`/`nf_LC0`… 报价 + 东财主连日线；国际用新浪 `hf_CAD`（伦铜）/`hf_AHD`（伦铝）/`hf_ZSD`（伦锌）/`hf_HG`（COMEX 铜，美分/磅）。
- 刷新节奏（已装）：WSL cron 工作日日盘 `09:00–14:55` 每 5 分钟、`15:00/15:05` 收盘；**夜间 `16:00–23:50` 与 `00:00–02:50`（周二至周六）每 10 分钟**覆盖国内夜盘与 LME/COMEX；Windows 计划任务 `MarketWatch-Refresh` 工作日 `09:20 / 15:10` 兜底。全量刷新约 2–4 秒（脚本显式绕过本机 HTTP 代理直连；走代理约 10 秒/请求）。
- 手动刷新：双击 `update.bat`，或 `bash /mnt/e/market-watch/update.sh`。

## 折算公式

| 品种 | 公式 | 说明 |
|---|---|---|
| 黄金 | `XAU(美元/盎司) × USDCNY ÷ 31.1035` | 元/克；沪金主力、T+D 为上海人民币价，溢价 = 沪市价 / 折算价 − 1 |
| 原油 | `WTI 或 布伦特(美元/桶) × USDCNY` | 元/桶；上海原油 SC0 以人民币计价、锚定中质含硫原油，用「SC / 布伦特折算」看内外价差 |
| 日元 | `USDCNY ÷ USDJPY × 100` | 元/100 日元；与官方 JPY/CNY 直盘比较，差以 bp 计 |

## 文件

| 文件 | 作用 |
|---|---|
| `index.html` | 面板本体：纯静态，本地打开即可，无外部依赖/CDN |
| `data.js` | 脚本生成的行情快照（`window.MKT = {...}`），页面每次加载它 |
| `fetch_market.py` | 取数 + 统计 + 生成 `data.js` / `data/latest.json` / 历史 |
| `update.sh` | 带文件锁的单次刷新入口（cron、计划任务、手动共用） |
| `update.bat` | Windows 双击刷新（转调 WSL 的 update.sh） |
| `install_schedule.ps1` / `uninstall_schedule.ps1` | 注册/卸载 Windows 计划任务 |
| `data/update.log` | 最近 600 行运行日志 |
| `data/history.json` | 线上用的按交易日累积历史（CI 每日回写）；本地运行写同目录 `history.local.json` |
| `.github/workflows/update.yml` | GitHub Actions：每 15 分钟取数并部署 Pages |
| `requirements.txt` | CI 依赖（requests） |
| `data/snapshots/YYYY-MM-DD.json` | 当日完整快照，保留 90 天 |

## 改品种 / 自选股

- 宏观品种在 `fetch_market.py` 的 `fetch_macro()` 里：新浪代码表（`hf_XAU`/`hf_CL`/`nf_AU0`/`fx_susdcny` 等）与东财补充 secid（`100.UDI`、`133.USDCNH`、日线 `113.aum`/`142.scm`）。
- 矿业品种在 `fetch_market.py` 的 `METALS` / `INTL_METALS`：`(key, 名称, 新浪报价代码, 东财主连日线 secid, 单位)`，加一行即加一个品种（国际盘另用 `INTL_METALS`）。
- A股自选在 `fetch_market.py` 顶部 `WATCHLIST`（`1.600519` 沪市、`0.002594` 深市、`116.01810` 港股、`105.TSLA` 美股）。
- 改完跑一次 `bash update.sh` 即可。

## 已知限制

- **新浪接口要 Referer**：不带会被拒/返回空，脚本已固定请求头。
- **期货夜盘**：沪金/上海原油 21:00 后的夜盘行情归属下一交易日，日线里会出现“明天”的未完成 K 线；历史序列已过滤掉该行。
- **北向资金**：2024-08 起交易所不再实时披露，面板不显示。
- **美股 / 港股**：北京时间晚间，美股为“隔夜收盘”（上一交易日），港股为当日收盘。
- **30 日近似**：黄金 T+D 的 30 日取沪金近似、在岸人民币取离岸近似、日元/人民币由 USD/CNH 与 USD/JPY 合成（表下已注明）。
- **内外盘口径**：LME 不含税，沪铜/沪铝含 13% 增值税，所以「沪铜 vs LME×汇率×1.13」才是同口径；国际铜 BC 是保税不含税价，直接对 LME×汇率比。COMEX 美铜单位是美分/磅（×2204.62 磅/吨 ÷100）。
- **节假日**：用“指数 K 线日期 + 指数报价更新时间”识别；A股休市时标「休市（今日不开市）」。全球盘按“周一 06:00 – 周六 05:00”近似。
- 页面依赖 `data.js` 与 `index.html` 同目录；单独拷走 HTML 会提示“还没有行情数据”。

## 卸载定时任务

```
powershell.exe -ExecutionPolicy Bypass -File E:\market-watch\uninstall_schedule.ps1
crontab -l | grep -v market-watch | crontab -
```

数据过期与否看页面右上角“更新 HH:MM:SS”；超过 1 小时会黄字提示检查定时任务。