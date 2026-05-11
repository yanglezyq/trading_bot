<!-- Qoder Rule Type: Model Decision -->
<!-- 场景描述: 需要理解项目结构、数据流或模块关系时 -->

# 项目架构

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.11+ | 使用新式类型语法 |
| CLI 框架 | Typer + Rich | 命令行界面 + 终端渲染 |
| Binance API | binance-connector 3.x | 现货 Spot + 合约 UMFutures |
| AI 分析 | Anthropic Claude API | claude-sonnet-4-6 |
| 配置 | PyYAML + python-dotenv | config.yaml + .env 分离 |
| 数据处理 | pandas + numpy | 行情分析 |
| 日志 | Python logging + SQLite | Rich 控制台 + data/trades.db |
| 测试 | pytest | 204+ 测试用例，dry_run 模式替代 mock |
| 代码质量 | Black + Ruff + mypy strict | CI 必须通过 |

## 分层架构

```
CLI (typer)
    ↓
core/          ← 基础设施层（配置、日志、Vault、重试机制）
exchange/      ← 交易所适配层（Binance 现货 + 合约 + 市场数据 + TTL 缓存）
onchain/       ← 链上数据层（DeFiLlama 协议 TVL / 链 TVL / 稳定币）
ai/            ← AI 分析层（Claude 研究 + 执行计划 + 反思）
risk/          ← 风控层（规则引擎 + 组合管理 + WebSocket 守护）
pipeline/      ← 执行编排层（端到端流程 + 持久化）
reports/       ← 报告生成层（预留）
```

## 数据流

```
trading trade BTCUSDT
    ↓
TradePipeline.run()
 ├── live_client (懒加载连接复用)
 ├── _fetch_account_summary() ← @retry(3次指数退避)
 ├── _fetch_positions()       ← @retry(3次指数退避)
 ├── MarketDataManager        ← TTL 分层缓存(K线5min/Ticker30s/Funding1min/Depth10s)
 ├── OnchainDataManager       ← DeFiLlama REST
 ├── PortfolioManager         ← 组合快照 + 仓位预算
 ├── VaultReader              ← 交易体系知识库
 ├── TradingAdvisor           ← Claude AI 研究 + 执行计划
 ├── RiskGate.evaluate()      ← 规则引擎(12+规则文件)
 └── _build_manual_order_ticket()
    ↓
TradeRunDB.save_run() + Vault 报告写入
```

## 模块清单

| 模块 | 文件 | 责任 |
|------|------|------|
| `core.config` | `src/trading/core/config.py` | 配置加载与验证（15+校验规则，errors/warnings双级） |
| `core.vault` | `src/trading/core/vault.py` | Obsidian Vault 读写 |
| `core.logger` | `src/trading/core/logger.py` | Rich 控制台 + SQLite 双输出日志 |
| `core.journal` | `src/trading/core/journal.py` | 已平仓交易记录、复盘统计 |
| `core.retry` | `src/trading/core/retry.py` | 指数退避重试 + 异常分类(Transient/Permanent) |
| `exchange.client` | `src/trading/exchange/client.py` | Binance Spot + UMFutures 统一封装(10s超时) |
| `exchange.um_futures` | `src/trading/exchange/um_futures.py` | UMFutures 兼容层(exchange_info 1h缓存 + 批量操作) |
| `exchange.account` | `src/trading/exchange/account.py` | 余额查询、P&L 计算 |
| `exchange.positions` | `src/trading/exchange/positions.py` | 合约持仓 + 清算距离计算 |
| `exchange.orders` | `src/trading/exchange/orders.py` | 市价单、限价单、止损止盈(失败重试补偿) |
| `exchange.market_data` | `src/trading/exchange/market_data.py` | 币圈市场快照 + TTLCache 分层缓存 |
| `onchain.manager` | `src/trading/onchain/manager.py` | DeFiLlama 链上数据编排 |
| `ai.client` | `src/trading/ai/client.py` | Claude API 客户端 + Prompt Cache |
| `ai.advisor` | `src/trading/ai/advisor.py` | 研究/执行计划/反思编排 |
| `ai.prompts` | `src/trading/ai/prompts/` | 币圈专用 Prompt 模板 |
| `ai.schemas` | `src/trading/ai/schemas.py` | ResearchDecision, ExecutionPlan, RiskDecision, ExecutionResult |
| `risk.gate` | `src/trading/risk/gate.py` | 规则引擎入口(~85行，委托给 rules/) |
| `risk.rules/` | `src/trading/risk/rules/` | 12个规则文件: base, position_limits, drawdown, defaults, conflicts, market_conditions, crypto_specific, p0_p1, template, price_geometry |
| `risk.portfolio` | `src/trading/risk/portfolio.py` | 组合快照与仓位预算 |
| `risk.equity_curve` | `src/trading/risk/equity_curve.py` | 资金曲线 EMA 保护 |
| `risk.ws_daemon` | `src/trading/risk/ws_daemon.py` | WebSocket 实时风控守护 |
| `pipeline.runner` | `src/trading/pipeline/runner.py` | 端到端 AI 交易流程(连接复用) |
| `pipeline.persistence` | `src/trading/pipeline/persistence.py` | SQLite 持久化(trade_runs + reflections + equity_snapshots) |
| `main` | `src/trading/main.py` | CLI 入口（11个命令） |

## CLI 命令

| 命令 | 功能 | 默认模式 |
|------|------|--------|
| `trading account` | 账户余额 + 合约持仓 + P&L | 读数据（安全） |
| `trading sync` | 同步持仓到 Vault | dry_run=True（默认预览） |
| `trading status` | 检查系统组件状态 | 读数据（安全） |
| `trading trade` | 运行 AI 研究 + 风控 → 输出手动下单清单 | 不自动下单 |
| `trading reflect` | 基于历史交易生成 AI 反思 | — |
| `trading record` | 手工录入已平仓交易 | — |
| `trading journal` | 查看交易日志与统计 | — |
| `trading watch` | WebSocket 实时风控守护 | — |

## 风控规则引擎

架构：`BaseRule.applies()` → `BaseRule.evaluate()` → `RuleResult`

| 规则文件 | 包含规则 |
|---------|----------|
| `position_limits.py` | MaxSizeRule, MaxLeverageRule, EventDrivenWarningRule |
| `drawdown.py` | DrawdownRule, EquityCurveRule |
| `defaults.py` | DefaultStopLossRule, DefaultTakeProfitRule |
| `conflicts.py` | PositionConflictRule, LiquidationDistanceRule, ProfitLockRule, NarrativeConcentrationRule |
| `market_conditions.py` | VolatilityRule, FundingRateRule, BasisRule, TrendBiasRule, CrowdingRule, EntryDriftRule |
| `crypto_specific.py` | AssetTierRule, MemeRule, BtcRegimeRule, AiNarrativeRule |
| `p0_p1.py` | FundingVelocityRule, DepthRule, MultiTfRule, DynamicLeverageRule |
| `template.py` | TemplateExecutionRule |
| `price_geometry.py` | PriceGeometryRule |

## 基础设施特性

| 特性 | 实现位置 | 说明 |
|------|---------|------|
| 连接复用 | `runner.py` live_client property | 懒加载单例，消除重复 TCP 握手 |
| 指数退避重试 | `core/retry.py` @retry | 区分 Transient/Permanent 异常 |
| 分层 TTL 缓存 | `market_data.py` TTLCache | K线5min / Ticker30s / Funding1min / Depth10s |
| 配置验证 | `config.py` validate_config() | 15+规则，返回 errors + warnings |
| SL/TP 补偿 | `runner.py` _do_execute() | 止损失败自动重试+CRITICAL告警 |
| 请求超时 | `client.py` + `um_futures.py` | 10秒默认超时 |
| 批量查询 | `um_futures.py` | exchange_info 1h缓存 + get_symbols_lot_sizes() |

## 配置加载优先级

```
CLI flag（最高）> 环境变量（.env）> config.yaml（最低）
```

敏感字段（API key）只存 .env，不写 config.yaml。
