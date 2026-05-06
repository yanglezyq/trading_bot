# 模块清单

> 本文件描述当前仓库的**真实实现状态**，不是历史规划。

## core/ · 基础设施层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| config | `src/trading/core/config.py` | `AppConfig`, `load_config`, `VaultConfig`, `BinanceConfig`, `ClaudeConfig`, `RiskConfig`, `TradingConfig`, `MonitorConfig`, `LoggingConfig` | 合并 `config.yaml + .env + CLI` 配置，承载币圈风险、叙事与执行模板参数 |
| vault | `src/trading/core/vault.py` | `VaultReader` | Obsidian Vault 读写：交易体系、持仓追踪、研究报告、AI 执行报告 |
| logger | `src/trading/core/logger.py` | `TradingLogger`, `SQLiteHandler` | Rich 控制台 + SQLite 双写日志 |
| journal | `src/trading/core/journal.py` | `TradeJournal`, `TradeRecord` | 已平仓交易记录、复盘统计、Rich 表输出 |

**导出入口**：`src/trading/core/__init__.py`

---

## exchange/ · 交易所与市场数据层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| client | `src/trading/exchange/client.py` | `BinanceClient` | Binance Spot + UMFutures 统一封装，支持 testnet 和 dry_run |
| um_futures | `src/trading/exchange/um_futures.py` | `UMFutures` | `/fapi` 兼容层：账户、订单、标记价格、open interest、24h ticker、K 线、杠杆、仓位模式 |
| account | `src/trading/exchange/account.py` | `AccountManager`, `Balance`, `FuturesBalance`, `AccountPnL` | 现货/合约余额与账户 P&L |
| positions | `src/trading/exchange/positions.py` | `PositionManager`, `FuturesPosition`, `SpotPosition` | 当前现货/合约仓位与聚合统计 |
| orders | `src/trading/exchange/orders.py` | `OrderManager`, `Order` | 底层下单/撤单能力；当前主流程已切换为人工下单模式，不自动调用真实下单 |
| market_data | `src/trading/exchange/market_data.py` | `MarketDataManager`, `MarketSnapshot`, `infer_narrative_tag` | 币圈专用公开市场快照：价格、波动、funding、OI、basis、BTC regime、叙事、执行模板 |

**导出入口**：`src/trading/exchange/__init__.py`

---

## ai/ · AI 研究与计划层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| client | `src/trading/ai/client.py` | `ClaudeClient` | Claude API 调用、JSON 抽取、prompt cache 控制 |
| schemas | `src/trading/ai/schemas.py` | `ResearchDecision`, `ExecutionPlan`, `RiskDecision`, `ExecutionResult` | AI 研究、执行计划、风控结果、人工下单清单的结构化模型 |
| advisor | `src/trading/ai/advisor.py` | `TradingAdvisor` | 组织研究、执行计划与反思调用 |
| prompts | `src/trading/ai/prompts/__init__.py` | `build_research_prompt`, `build_execution_prompt`, `build_reflection_prompt` | 面向币圈的 prompt：价格结构、BTC regime、叙事、execution template |

---

## risk/ · 确定性风控层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| gate | `src/trading/risk/gate.py` | `RiskGate` | 币圈专用规则风控：仓位/杠杆上限、波动过滤、funding/basis/OI 拥挤度、BTC risk regime、叙事与模板差异化风险覆盖 |

---

## pipeline/ · AI 交易执行编排层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| runner | `src/trading/pipeline/runner.py` | `TradePipeline`, `is_trading_api_configured` | 真实主链：账户/仓位/市场快照 → ResearchDecision → ExecutionPlan → RiskGate → 手动下单清单 → Vault/SQLite 持久化 |
| persistence | `src/trading/pipeline/persistence.py` | `TradeRunDB` | 保存完整 pipeline run、market snapshot、execution result 与 reflection |

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
| `trading record` | `record()` | 录入已平仓交易 |
| `trading journal` | `journal()` | 查看交易日志与统计 |
| `trading trade` | `trade()` | 运行完整币圈 AI 交易流程，输出**手动下单清单**，不自动下单 |
| `trading reflect` | `reflect()` | 基于历史平仓交易生成 AI 反思 |

---

## 运行链速记

`trading trade` 的当前真实链路：

1. `load_config()` 载入配置
2. `TradePipeline.run()` 收集账户、仓位、`MarketSnapshot`、Vault 上下文、历史交易、历史反思
3. `TradingAdvisor.generate_research()` 生成 `ResearchDecision`
4. `TradingAdvisor.generate_execution_plan()` 生成 `ExecutionPlan`
5. `RiskGate.evaluate()` 应用币圈专用规则风控
6. `TradePipeline._build_manual_order_ticket()` 产出人工执行清单
7. `_build_report()` 写入 Vault
8. `TradeRunDB.save_run()` 写入 SQLite

---

## 重点数据类速查

| 类名 | 所在模块 | 用途 |
|------|---------|------|
| `AppConfig` | `core.config` | 顶层配置聚合对象 |
| `RiskConfig` | `core.config` | 币圈风险、叙事和模板阈值 |
| `MarketSnapshot` | `exchange.market_data` | 价格、结构、拥挤度、BTC regime、叙事、模板 |
| `ResearchDecision` | `ai.schemas` | AI 研究结论 |
| `ExecutionPlan` | `ai.schemas` | AI 执行计划 |
| `RiskDecision` | `ai.schemas` | 确定性风控裁决 |
| `ExecutionResult` | `ai.schemas` | 最终执行结果（当前为手动下单模式） |
| `TradeRecord` | `core.journal` | 已平仓交易记录 |
