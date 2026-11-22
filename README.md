# 每日看盘 · market-watch

本地运行的每日盯盘面板，**主看 XAUUSD 黄金、WTI/布伦特原油、USD/JPY 日元，并把三者折成人民币价**（元/克、元/桶、元/百日元）；另有「矿业期货」页签（有色 / 黑色 / 铁合金 / 新能源金属 / LME·COMEX 内外盘）、「A股盘面」页签（指数 / 涨跌宽度 / 板块 / 涨停梯队 / 自选股）和「AI 财报」页（Pi Agent 自动分析美股/A股/港股财报）。
页面本身不联网：行情由脚本写进同目录 `data.js`，浏览器直接打开 `index.html` 即可。

## 在线访问

**GitHub Pages（唯一入口）：https://hana-ame.github.io/market-watch/** — Actions 部署；境外 runner 偶发被东财 502，页面有 A股兜底快照。

GitHub 仓库（代码镜像）：https://github.com/Hana-ame/market-watch （PUBLIC）
- GitHub Actions **每天早/中/晚三次**跑 `fetch_market.py` + 财报分析（cron `17 0` / `17 4` / `17 12` UTC = 北京 08:17 / 12:17 / 20:17），把 `index.html` + 生成的 `data.js` + `earnings/` 作为 Pages 产物部署；`data.js` 不入库。
- `data/history.json` 每个交易日只回写一次（要求已收盘、无降级、当日尚未记录），用于「近期节奏」多日累积。
- 境外 runner 偶发被东财接口 502（页面右上角会显示「N 个数据源降级」）；全市场分页已降到 6 并发 + 每页 4 次指数退避，自选股拆两批独立重试，最近一次 run 无降级。

## GitHub Pages 更新保障

目标是**每天更新三次**（用户口径：早/中/晚）。GitHub 的 `schedule` 是 best-effort（可能延迟、合并甚至长时间不触发），所以仍有兜底：

1. **定时**：`17 0` / `17 4` / `17 12`（UTC，即北京 08:17 / 12:17 / 20:17）触发 workflow；
2. **手动/兜底**：workflow 支持 `repository_dispatch`，可随时触发一次：
   ```sh
   gh api -X POST repos/Hana-ame/market-watch/dispatches -f event_type=refresh
   ```
3. **本机看门狗**：`remote_watchdog.sh`（WSL cron）读取线上 `data.js` 的 `generated_ts`，超过 26 小时未更新才 dispatch；线上正常时只做一次 curl。
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

## 美元流动性页（`liquidity.html`）

与「利率 × 周期」互补：那页看货币政策的**价格**（利率、利差、通胀），这页看**数量**（市场上到底有多少活钱）。

- **净流动性主图**：`WALCL − TGA − ON RRP`，单位统一为万亿美元，周频阶梯；给出 13 / 52 周变化与近 5 年分位。ON RRP 接近零时页面会显式标注「减项失效」，此时净流动性 ≈ `WALCL − TGA`。
- **三件套分解**：美联储总资产（扩表注水 / 缩表抽水）、TGA（发债收税抽水、财政支出放水）、ON RRP（货币基金停泊的闲置现金）各自走势。
- **资金价格**：`SOFR − IORB` 利差（回购融资相对准备金的溢价）、高收益债利差 `BAMLH0A0HYM2`、金融压力指数 `STLFSI4`、10 年期实际利率 `DFII10`。
- **黄金 COT**：CFTC 管理基金净多头（黄金 088691）+ 总持仓，含 3 年分位与 4 周变化，用于看筹码是否拥挤。
- **跨资产状态**：按当日方向判定「正常避险 / 流动性挤压 / 风险偏好 / 信号混合」。**股跌 + 债跌 + 金跌 + 美元涨** = 无差别变现换美元；**股跌 + 债涨** = 正常避险轮动。这只是事后分类，不是买卖信号。

数据来自 FRED 公开 CSV（`fredgraph.csv`，无需 API key）与 CFTC Socrata API。频率并不一致，卡片上都标了各自的数据日期：净流动性周频（WALCL 周四更新，TGA/RRP 按周对齐取当时值），SOFR / 高收益利差为 T+1，`STLFSI4` 周频，`DTWEXBGS` 是日值但按周发布、约滞后一周，COT 每周五发布。

更新方式：`python3 fetch_liquidity.py [--if-stale 1]`，产物 `liquidity.js`（`window.LIQ`）不入库，由 CI 生成后随 Pages 一并部署；FRED / CFTC 任一失败会沿用缓存或隐藏对应区块，不影响整站。

## 银行准备金页（`reserves.html`）

与「美元流动性」互补：那页看**钱的总量**（净流动性 = WALCL − TGA − ON RRP），这页看**银行手里还剩多少活钱**（准备金 = 商业银行在美联储账上的结算弹药）。

- **准备金 vs 安全线主图**：`WRESBAL`（美联储 H.4.1 每周三读数）叠加「安全线 = 年化名义 GDP × 10%」（当前 3.30 万亿），低于安全线的区间用紫色带标出。华尔街经验口径：准备金 ≥ GDP 的 10% 才算充裕；另有「固定 3 万亿」整数红线（占当前 GDP 约 9.1%）作第二口径并列显示（红色虚线）。
- **占年化 GDP（%）**：准备金 / 年化 GDP 的百分位曲线，10% 安全线为虚线。
- **TGA + ON RRP**：财政部一般账户 `WTREGEN`（日频，百万美元）与隔夜逆回购 `RRPONTSYD`（日频，十亿美元 → ×1000 转百万美元）走势——TGA 上升抽水、下降放水；ON RRP 是货币基金停泊的闲钱，接近零意味着「蓄水池见底」。H.4.1 周三口径（`WDTGAL` / `WLRRAL`）作对照保留。
- **关键事件标记**：2019-09 钱荒（准备金低点）、2020-03 疫情 QE、2021-12 Taper、2022-06 QT 缩表、2025-10 TGA 重建。
- **怎么读 / 当前读数 / 关键历史节点**：准备金是回购市场的弹药，跌破安全线 → 银行惜贷 → 短期利率飙涨 → 股债金无差别抛售（2019-09 前车之鉴）。
- **银行存放美联储的真实无风险收益率（IORB）**：`IORB`（准备金余额利率）即银行把准备金存在美联储账上赚到的利息；自 2021-07 起精确等于「联邦基金目标区间上限 − 10bp」（`DFEDTARU − 0.10`），此前 2008-10 至 2021-07 由旧口径 `IORR` 衔接。卡片给出 IORB、目标区间上限 − 0.10%（对照验证）、SOFR − IORB 利差（回购融资相对准备金的溢价）、目标区间；下方是 IORB / 上限−0.10 / SOFR 的 2008 年至今曲线。

数据来自 FRED 公开 CSV（`fredgraph.csv`，无需 API key）：`WRESBAL`（百万美元，周频）+ `WTREGEN` / `RRPONTSYD`（日频，TGA 百万美元 / RRP 十亿美元）+ `WDTGAL` / `WLRRAL`（H.4.1 周三对照）+ `GDP`（季度，插值成日度后取年化）+ `IORB` / `IORR` / `DFEDTARU` / `DFEDTARL` / `SOFR`（利率，日频）。安全线 = 年化 GDP × 10%（当前 3.30 万亿），固定「3T」整数红线（占当前 GDP 约 9.1%）作第二口径。

更新方式：`python3 fetch_reserves.py [--if-stale 20]`，产物 `reserves.js`（`window.RESERVES`）不入库，由 CI 生成后随 Pages 一并部署。

## AI 财报分析页（`earnings/`）

- **入口**：主站导航「AI 财报」→ `earnings/index.html`（GitHub Pages 部署到 `/market-watch/earnings/`）。
- **数据**：`fetch_earnings.py` 确定性抓取三市场——美股 SEC EDGAR XBRL `companyfacts`（AAPL/MSFT/NVDA 等）、A股东方财富 `RPT_LICO_FN_CPD` 业绩报表、港股东方财富 `RPT_HKF10_FN_MAININDICATOR`（腾讯/阿里/美团），输出 `data/earnings_raw.json`。
- **分析**：CI 里用 Pi Coding Agent（`pi --provider google --model gemini-3.1-flash-lite -p "$(cat prompt.md)"`）读 raw 数据、生成含市场总览/三市场卡片/风险提示的 `dist/earnings/index.html`；LLM 密钥走 GitHub Secret `GEMINI_API_KEY`。模型选 **gemini-3.1-flash-lite**（Free Tier 独立配额桶；`gemini-3-flash` 仅 20 次/天且 `-preview` 会被归一化，易撞 429）。pi 三次重试全失败时，部署仓库兜底页 `earnings/index.html`，保证 `/earnings/` 始终可访问。
- **调度**：`.github/workflows/update.yml` 每天**早/中/晚**三次（北京 08:17 / 12:17 / 20:17）跑完整站点 + 财报分析并部署 Pages。
- **免责声明**：页面底部固定「数据来自公开渠道（SEC EDGAR / 东方财富），分析由 AI 自动生成，仅供参考，不构成投资建议」。
- **部署**：仅 GitHub Pages（`/market-watch/earnings/`），由 CI 生成并发布；不依赖自托管。

## 数据与刷新

- **新浪财经**：`hq.sinajs.cn`（伦敦金 XAU、伦敦银 XAG、WTI、布伦特、沪金主连 AU0、上海原油 SC0、黄金 T+D、在岸 USD/CNY、USD/JPY、JPY/CNY）；`stock2.finance.sina.com.cn`（外盘与国内期货日线）。该接口必须带 `Referer: https://finance.sina.com.cn`，否则返回空。
- **东方财富**：`push2`（A股、指数、美元指数 100.UDI、离岸人民币 133.USDCNH、自选股）、`push2his`（指数与外汇日线、国内期货主连日线：沪金 113.aum / 原油 142.scm / 沪铜 113.cum / 铁矿 114.im / 碳酸锂 225.lcm 等）、`push2ex`（涨停/跌停/炸板池）、`np-listapi`（7×24 快讯）。
- **矿业期货**：国内 19 个品种用新浪 `nf_CU0`/`nf_RB0`/`nf_LC0`… 报价 + 东财主连日线；国际用新浪 `hf_CAD`（伦铜）/`hf_AHD`（伦铝）/`hf_ZSD`（伦锌）/`hf_HG`（COMEX 铜，美分/磅）。
- 刷新节奏（已装）：WSL cron 工作日日盘 `09:00–14:55` 每 5 分钟、`15:00/15:05` 收盘；**夜间 `16:00–23:50` 与 `00:00–02:50`（周二至周六）每 10 分钟**覆盖国内夜盘与 LME/COMEX；Windows 计划任务 `MarketWatch-Refresh` 工作日 `09:20 / 15:10` 兜底。全量刷新约 2–4 秒（脚本显式绕过本机 HTTP 代理直连；走代理约 10 秒/请求）。
- **FRED + CFTC（流动性页）**：`fredgraph.csv?id=...` 取 WALCL / WTREGEN / RRPONTSYD / SOFR / IORB / BAMLH0A0HYM2 / STLFSI4 / DFII10 / DTWEXBGS / M2SL / SP500 / DGS10；CFTC `publicreporting.cftc.gov` Socrata 取黄金管理基金持仓（合约 088691）。日内不刷新，随每日 CI 一次。
- **FRED（准备金页）**：`fredgraph.csv?id=...` 取 WRESBAL / WTREGEN / RRPONTSYD / WDTGAL / WLRRAL / GDP / IORB / IORR / DFEDTARU / DFEDTARL / SOFR；周频，随 update.sh 每轮跑（`--if-stale 20`，即最多每 20 天强制刷新一次）。
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
| `cycle.html` | 利率 × 周期页：FFR / 实际利率 / 10Y / 期限利差 / CPI / NBER 衰退 |
| `rates.js` | 脚本生成的历史利率月度序列（`window.RATES`），`file://` 即可加载 |
| `liquidity.html` | 美元流动性页：净流动性 / 三件套 / 资金价格 / 黄金 COT / 跨资产状态 |
| `liquidity.js` | 脚本生成的流动性快照（`window.LIQ`），CI 生成不入库 |
| `fetch_liquidity.py` | 取 FRED + CFTC，生成 `liquidity.js` |
| `reserves.html` | 银行准备金页：准备金 vs 安全线 / 占 GDP / TGA + ON RRP / 关键事件 |
| `reserves.js` | 脚本生成的准备金快照（`window.RESERVES`），CI 生成不入库 |
| `fetch_reserves.py` | 取 FRED WRESBAL / WTREGEN / RRPONTSYD / WDTGAL / WLRRAL + GDP + IORB/IORR/DFEDTARU/DFEDTARL/SOFR，生成 `reserves.js` |
| `earnings/` | AI 财报分析页（Pi Agent 生成，含免责声明），部署到 `site/earnings/index.html` |
| `fetch_earnings.py` | 取 SEC EDGAR（美股）+ 东方财富（A股/港股）财报，生成 `data/earnings_raw.json` |
| `prompt.md` | 给 Pi Agent 的财报分析指令（读 raw → 分析 → 写 `dist/earnings/index.html`） |
| `fetch_market.py` | 取数 + 统计 + 生成 `data.js` / `data/latest.json` / 历史 |
| `update.sh` | 带文件锁的单次刷新入口（cron、计划任务、手动共用） |
| `update.bat` | Windows 双击刷新（转调 WSL 的 update.sh） |
| `install_schedule.ps1` / `uninstall_schedule.ps1` | 注册/卸载 Windows 计划任务 |
| `data/update.log` | 最近 600 行运行日志 |
| `data/history.json` | 线上用的按交易日累积历史（CI 每日回写）；本地运行写同目录 `history.local.json` |
| `.github/workflows/update.yml` | GitHub Actions：每天早/中/晚三次取数 + Pi 财报分析并部署 Pages |
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
- **流动性页频率不一致**：净流动性周频、TGA/RRP 日频、SOFR 与高收益利差 T+1、STLFSI4 周频、美元指数滞后约一周。跨资产判定用的是各自最新可得值，所以四条信号的日期可能不同（卡片上已标）。
- **CFTC 有网络限制**：部分网络访问 `publicreporting.cftc.gov` 会 403（境外 runner 正常）。本地取不到时沿用 `data/cot_gold.json` 缓存，无缓存则隐藏 COT 区块。

## 卸载定时任务

```
powershell.exe -ExecutionPolicy Bypass -File E:\market-watch\uninstall_schedule.ps1
crontab -l | grep -v market-watch | crontab -
```

数据过期与否看页面右上角“更新 HH:MM:SS”；超过 1 小时会黄字提示检查定时任务。