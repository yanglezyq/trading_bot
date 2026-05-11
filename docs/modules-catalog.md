# 模块清单

> 本文件描述当前仓库的**真实实现状态**，不是历史规划。

## core/ · 基础设施层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| config | `src/trading/core/config.py` | `AppConfig`, `load_config`, `validate_config`, `VaultConfig`, `BinanceConfig`, `ClaudeConfig`, `RiskConfig`, `TradingConfig`, `MonitorConfig`, `LoggingConfig`, `EventsConfig` | 合并 `config.yaml + .env + CLI` 配置；15+校验规则（errors阻止启动 / warnings仅告警） |
| config_patch | `src/trading/core/config_patch.py` | `ConfigPatchManager` | 安全应用 YAML patch 到 `config.yaml`，自动备份并支持回滚 |
| vault | `src/trading/core/vault.py` | `VaultReader` | Obsidian Vault 读写：交易体系、持仓追踪、研究报告、AI 执行报告 |
| logger | `src/trading/core/logger.py` | `TradingLogger`, `SQLiteHandler` | Rich 控制台 + SQLite 双写日志 |
| journal | `src/trading/core/journal.py` | `TradeJournal`, `TradeRecord` | 已平仓交易记录、复盘统计、Rich 表输出；支持 `linked_run_id` 关联某次 pipeline run |
| retry | `src/trading/core/retry.py` | `retry`, `TransientError`, `PermanentError`, `is_transient` | 指数退避重试装饰器 + 异常分类（自动识别 Timeout/429/503） |

**导出入口**：`src/trading/core/__init__.py`

---

## exchange/ · 交易所与市场数据层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| client | `src/trading/exchange/client.py` | `BinanceClient` | Binance Spot + UMFutures 统一封装，支持 testnet 和 dry_run，10s 超时 |
| um_futures | `src/trading/exchange/um_futures.py` | `UMFutures` | `/fapi` 兼容层：账户、订单、标记价格、OI、ticker、K线、杠杆、仓位模式；exchange_info 1h缓存 + 批量 lot_size 查询 |
| account | `src/trading/exchange/account.py` | `AccountManager`, `Balance`, `FuturesBalance`, `AccountPnL` | 现货/合约余额与账户 P&L |
| positions | `src/trading/exchange/positions.py` | `PositionManager`, `FuturesPosition`, `SpotPosition` | 当前现货/合约仓位、清算距离计算与聚合统计 |
| orders | `src/trading/exchange/orders.py` | `OrderManager`, `Order` | 底层下单/撤单能力；当前主流程已切换为人工下单模式 |
| market_data | `src/trading/exchange/market_data.py` | `MarketDataManager`, `MarketSnapshot`, `TTLCache`, `infer_narrative_tag` | 币圈专用公开市场快照 + 分层 TTL 缓存（K线5min/Ticker30s/Funding1min/Depth10s/Mark15s）；叙事标签 79+ symbol 显式映射 + `general_alt` fallback；输出 BTC regime / execution template / narrative tag |
| coingecko | `src/trading/exchange/coingecko.py` | `CoinGeckoData` | CoinGecko 免费 API 封装：market_cap/rank/categories/FDV；10min TTL 缓存，失败静默降级 |

**导出入口**：`src/trading/exchange/__init__.py`

---

## ai/ · AI 研究与计划层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| client | `src/trading/ai/client.py` | `ClaudeClient` | Claude API 调用、JSON 抽取、prompt cache 控制，支持 `model_override` |
| adaptive | `src/trading/ai/adaptive.py` | `build_adaptive_prompt_context` | 基于历史胜率、连亏、方向表现、risk-stats 与 reflections 生成确定性 prompt 自适应提示 |
| schemas | `src/trading/ai/schemas.py` | `ResearchDecision`, `ExecutionPlan`, `RiskDecision`, `ExecutionResult` | AI 研究、执行计划、风控结果、人工下单清单的结构化模型；ResearchDecision 含共识强度字段 |
| advisor | `src/trading/ai/advisor.py` | `TradingAdvisor` | 组织研究、执行计划与反思调用；支持 `generate_combined()` 合并单次调用模式（节省 ~30% token）与 `generate_research_ensemble()` 多模型分歧投票 |
| prompts | `src/trading/ai/prompts/__init__.py` | `build_research_prompt`, `build_execution_prompt`, `build_combined_research_execution_prompt`, `build_reflection_prompt` | 面向币圈的 prompt：价格结构、BTC regime、叙事、Portfolio 上下文、事件快照、Vault 智能截断、Adaptive Guidance |

---

## onchain/ · 链上数据层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| manager | `src/trading/onchain/manager.py` | `OnchainDataManager`, `OnchainSnapshot` | 按交易对查询协议 TVL、链 TVL、稳定币总供应，组装 `OnchainSnapshot` 传入 AI 研究 |
| defillama | `src/trading/onchain/defillama.py` | `DeFiLlamaClient` | DeFiLlama 公开 REST API 封装：`/protocol`, `/tvl`, `/v2/chains`, stablecoins；无需 API key |

**数据覆盖：**
- DeFi/DEX/Lending 币种 → 协议 TVL + 24h/7d 变化 + 部署链列表
- L1/L2 币种（ETH/SOL/ARB 等）→ 链总 TVL
- 所有币种 → 全网稳定币总供应（宏观流动性指标）

---

## events/ · 事件源层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| macro_calendar | `src/trading/events/macro_calendar.py` | `MacroCalendarClient`, `MacroEvent` | ForexFactory 宏观经济日历：CPI/FOMC/NFP 等高影响事件；30min TTL 缓存，静默降级 |
| token_unlocks | `src/trading/events/token_unlocks.py` | `TokenUnlocksClient`, `UnlockEvent` | DeFiLlama Token 解锁/归属时间表：30+ 币种映射，1h TTL 缓存 |
| manager | `src/trading/events/manager.py` | `EventManager`, `EventSnapshot` | 事件数据源编排：组装宏观+解锁数据，生成 `EventSnapshot` 含 flags（has_high_impact_soon / has_major_unlock_soon） |

**数据覆盖：**
- 宏观日历：USD/EUR/CNY/JPY 的 High/Medium impact 事件（周度更新）
- Token 解锁：30+ 币种的归属/悬崖解锁计划（7日前瞻）
- 触发条件：High impact 事件 24h 内 / >1% 供应解锁 → 风控压仓 50% + 杠杆上限 10x

---

## risk/ · 确定性风控层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| gate | `src/trading/risk/gate.py` | `RiskGate` | 规则引擎入口（~85行），加载并执行 rules/ 目录下所有规则 |
| rules/ | `src/trading/risk/rules/` | 15个规则文件 | base, position_limits, drawdown, defaults, conflicts, market_conditions, crypto_specific, p0_p1, template, price_geometry, events, stable_common, stable_behavior, stable_setup |
| portfolio | `src/trading/risk/portfolio.py` | `PortfolioManager`, `PortfolioSnapshot`, `PortfolioBudget` | 组合层快照（gross/net exposure、叙事集中度）与动态仓位预算推荐 |
| equity_curve | `src/trading/risk/equity_curve.py` | `EquityCurveProtector` | 资金曲线 EMA 保护：快/慢 EMA 交叉触发仓位压缩 |
| tuning | `src/trading/risk/tuning.py` | `RiskTuningAdvisor`, `RiskTuningSuggestion` | 基于 linked outcomes、risk-stats、replay-stats 生成保守型风控参数调优建议，并支持导出 YAML patch |
| edge_policy | `src/trading/risk/edge_policy.py` | `EdgePolicyAdvisor`, `EdgePolicyDecision` | 基于 `edge-stats` 识别 promoted / blocked slice，并把历史赚钱能力反哺回当前 run |
| ws_daemon | `src/trading/risk/ws_daemon.py` | `RiskDaemon`, `WatchTarget` | 实时 WebSocket 风控守护：订阅 Binance 标记价格流，在 invalidation/止损/止盈/funding 告警时实时推送 |

---

## pipeline/ · AI 交易执行编排层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| backtest | `src/trading/pipeline/backtest.py` | `BacktestRunner`, `BacktestOutcome` | 基于已保存 run 信号回放历史 Binance K 线，模拟 SL / TP / time exit，并输出回测摘要 |
| runner | `src/trading/pipeline/runner.py` | `TradePipeline`, `is_trading_api_configured` | 真实主链：账户/仓位/市场快照 → CoinGecko补充 → 事件源 → Research+Execution（单调用/双调用可配置）→ RiskGate → edge policy overlay → 手动下单清单 / 自动执行闸门 → 持久化；默认手动，显式 `--auto-execute` 时才会调用真实订单链；懒加载 BinanceClient + @retry + SL/TP 补偿 |
| persistence | `src/trading/pipeline/persistence.py` | `TradeRunDB` | 保存完整 pipeline run、adaptive_context、market snapshot、execution result、reflection 与 equity snapshot；提供 `get_risk_rule_stats()`、`get_replay_stats()`、`get_edge_stats()`，可从 linked closed trades 回放并提炼真实 edge |

---

## reports/ · 报告输出层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| *(内嵌于 pipeline)* | `src/trading/pipeline/runner.py::_build_report` | — | 当前没有独立 generator；执行报告由 pipeline 内部构建并写入 Vault |

---

## main.py · CLI 入口

| 命令 | 函数 | 描述 |
|------|------|------|
| `trading account` | `account()` | 查看账户余额、持仓与 P&L，支持 dry-run mock |
| `trading sync` | `sync()` | 同步持仓到 Vault；`--dry-run` 现在也走安全 mock 预览 |
| `trading status` | `status()` | 检查 Vault / Binance / Claude 状态 |
| `trading record` | `record()` | 录入已平仓交易；支持 `--run-id` 或自动关联最近一次同 symbol pipeline run |
| `trading journal` | `journal()` | 查看交易日志与统计 |
| `trading trade` | `trade()` | 运行完整币圈 AI 交易流程；默认输出**手动下单清单**，显式 `--auto-execute` 时可走真实自动执行链 |
| `trading reflect` | `reflect()` | 基于历史平仓交易生成 AI 反思 |
| `trading risk-stats` | `risk_stats()` | 聚合显示最近 pipeline 中最常触发的 warning / blocking 规则，并输出 linked trades / win rate / avg P&L 上下文 |
| `trading replay-stats` | `replay_stats()` | 回放已关联样本，按 BTC regime / template / narrative / action / adaptive profile 聚合真实结果 |
| `trading edge-stats` | `edge_stats()` | 从 linked 样本计算 expectancy / avg win-loss / payoff ratio / profit factor，并按主要 slice 找 edge |
| `trading tune-edge` | `tune_edge()` | 基于 edge-stats 生成 `edge_policy_allowlist / edge_policy_denylist` 建议，并可导出 YAML patch |
| `trading backtest` | `backtest()` | 按已保存 run 信号回放历史 Binance K 线，模拟 SL / TP / time exit 并汇总结果 |
| `trading tune-risk` | `tune_risk()` | 基于 linked 样本给出保守型风控参数调优建议，可导出 YAML patch |
| `trading rollback-config` | `rollback_config()` | 列出配置备份，或将 `config.yaml` 回滚到某个先前备份 |
| `trading watch` | `watch()` | 启动实时 WebSocket 风控守护，监控标记价格、资金费率与风险价位 |
| `trading batch-trade` | `batch_trade()` | 批量扫描多个币种，汇总信号；显式 `--auto-execute` 时允许逐币种经过自动执行闸门后发单 |
| `trading rebalance` | `rebalance()` | 计算组合偏差并执行/预览调仓单 |

---

## 运行链速记

`trading trade` 的当前真实链路：

1. `load_config()` 载入配置
2. `TradePipeline.run()` 收集账户、仓位、`MarketSnapshot`（Binance REST）、CoinGecko 补充、`OnchainSnapshot`（DeFiLlama）、`EventSnapshot`（宏观日历+Token解锁）、Vault 上下文（智能截断）、历史交易、历史反思
3. `PortfolioManager.build_snapshot()` + `recommend_budget()` 计算全账户 exposure 与仓位预算
4. `TradePipeline._get_adaptive_context()` 汇总最近胜率、连亏、方向表现、反思 lessons、risk-stats 高频模式
5. `TradingAdvisor.generate_combined()` 单次 Claude 调用产出 Research + Execution（prompt 含 Portfolio上下文 + 链上数据 + 事件快照 + Adaptive Guidance）
6. `RiskGate.evaluate()` 应用币圈专用规则风控（含 `PortfolioBudget` 约束 + `EventRiskRule` 事件压仓）
7. `TradePipeline._build_manual_order_ticket()` 产出人工执行清单
8. `_build_report()` 写入 Vault
9. `TradeRunDB.save_run()` 写入 SQLite

---

## 重点数据类速查

| 类名 | 所在模块 | 用途 |
|------|---------|------|
| `AppConfig` | `core.config` | 顶层配置聚合对象 |
| `RiskConfig` | `core.config` | 币圈风险、叙事和模板阈值 |
| `TransientError` | `core.retry` | 可重试的暂时性异常 |
| `PermanentError` | `core.retry` | 不可重试的永久性异常 |
| `TTLCache` | `exchange.market_data` | 带过期时间的 LRU 缓存 |
| `CoinGeckoData` | `exchange.coingecko` | CoinGecko 免费 API：market_cap/rank/categories/FDV |
| `MarketSnapshot` | `exchange.market_data` | 价格、结构、拥挤度、BTC regime、叙事、模板 |
| `OnchainSnapshot` | `onchain.manager` | DeFiLlama 链上快照：协议 TVL / 链 TVL / 稳定币供应 |
| `PortfolioSnapshot` | `risk.portfolio` | 全账户 gross/net exposure 与叙事集中度快照 |
| `PortfolioBudget` | `risk.portfolio` | 针对单次交易的仓位预算建议与硬上限 |
| `EquityCurveProtector` | `risk.equity_curve` | EMA 资金曲线保护器 |
| `EventSnapshot` | `events.manager` | 事件快照：宏观日历 + Token 解锁 + 高影响标志 |
| `EventsConfig` | `core.config` | 事件源配置：开关 + 压仓参数 |
| `ResearchDecision` | `ai.schemas` | AI 研究结论 |
| `supporting_model_count / consensus_strength / disagreement_note` | `ai.schemas.ResearchDecision` | 多模型投票的共识元数据 |
| `ExecutionPlan` | `ai.schemas` | AI 执行计划 |
| `RiskDecision` | `ai.schemas` | 确定性风控裁决 |
| `ExecutionResult` | `ai.schemas` | 最终执行结果（当前为手动下单模式） |
| `TradeRecord` | `core.journal` | 已平仓交易记录 |
| `WatchTarget` | `risk.ws_daemon` | 单个持仓的监控参数 |
| `RiskDaemon` | `risk.ws_daemon` | WebSocket 风控守护进程主体 |
| `BaseRule` | `risk.rules.base` | 风控规则基类（applies + evaluate） |
| `RuleResult` | `risk.rules.base` | 规则执行结果（action + reason + adjustments） |
