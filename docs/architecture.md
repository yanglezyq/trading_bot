# 系统架构

## 当前真实定位

当前仓库已经不是单纯的账户查看工具，而是一套**面向币圈投资的 AI 研究 + 确定性风控 + 人工执行清单系统**。

它的真实工作方式是：

1. 读取 Binance 账户、仓位与市场快照
2. 结合 Obsidian Vault 中的交易体系做 AI 研究
3. 用币圈专用规则风控过滤研究结果
4. 输出人工下单清单，而不是自动下单
5. 将运行快照、手动执行建议和反思沉淀到 SQLite / Vault

## 设计原则

1. **安全优先**：真实下单默认禁用，主流程输出人工执行清单。
2. **币圈专精**：风险与研究围绕价格结构、funding、OI、basis、BTC regime、相对强弱、叙事标签展开。
3. **知识驱动**：AI 研究使用 Obsidian Vault 中的个人交易体系、报告与持仓上下文。
4. **规则压在模型之后**：AI 负责 thesis 和执行计划，确定性风控负责最终约束。
5. **可追溯**：控制台 + SQLite + Vault 三层留痕。
6. **优先做高质量机会**：`stable_mode` 默认过滤低质量 setup，但高信心信号可以触发 `conviction override` 放宽部分软门槛。

## 当前主链

```text
trading trade
    ↓
load_config()
    ↓
TradePipeline.run()
    ├─ _get_account_summary()
    ├─ _get_positions()
    ├─ _get_market_snapshot()       ← Binance REST（价格/funding/OI/波动/BTC regime）
    ├─ _enrich_with_coingecko()    ← CoinGecko（market_cap/rank/categories/FDV）
    ├─ PortfolioManager.build_snapshot() + recommend_budget()
    ├─ _get_onchain_snapshot()      ← DeFiLlama（协议TVL/链TVL/稳定币供应）
    ├─ _get_event_snapshot()        ← 宏观日历(ForexFactory) + Token解锁(DeFiLlama)
    ├─ _get_vault_context()         ← 智能截断：symbol相关性优先 + token预算
    ├─ _get_trade_history()
    ├─ _get_reflections()
    └─ _get_adaptive_context()      ← 最近胜率 / 连亏 / 方向表现 / 高频风险模式 / lessons
    ↓
TradingAdvisor.generate_combined()  ← 单次调用产出 Research + Execution（可配置回退双调用）
    ↓                                  prompt 含 OnchainSnapshot + Portfolio上下文 + EventSnapshot + Adaptive Guidance
TradingAdvisor.generate_research_ensemble()  ← 可选多模型分歧投票（ResearchDecision 共识）
    ↓
RiskGate.evaluate()                 ← 含 PortfolioBudget 约束 + EventRiskRule 事件压仓
    ↓
_build_manual_order_ticket()
    ↓
_write_vault_report()
    ↓
TradeRunDB.save_run()

Closed trades recorded later
    ↓
TradeJournal.record_trade(linked_run_id=...)
    ↓
TradeRunDB.get_risk_rule_stats() / get_replay_stats()
    ↓
RiskTuningAdvisor.suggest()
    ↓
ConfigPatchManager.apply_patch() / rollback()
```

## 分层结构

```text
CLI
 ├─ account / sync / status / record / journal / trade / reflect
 └─ watch                                # WebSocket 风控守护
 │
core/
 ├─ config
 ├─ vault
 ├─ logger
 └─ journal
 │
exchange/
 ├─ BinanceClient
 ├─ AccountManager
 ├─ PositionManager
 ├─ OrderManager       # 底层保留，但当前主链不自动调用
 ├─ MarketDataManager  # 币圈专用市场快照
 └─ CoinGeckoData      # CoinGecko 免费 API（market_cap/rank/categories）
 │
onchain/
 ├─ OnchainDataManager # 链上数据编排
 └─ DeFiLlamaClient    # DeFiLlama REST API（协议TVL/链TVL/稳定币）
 │
events/
 ├─ MacroCalendarClient  # 宏观经济日历（ForexFactory）
 ├─ TokenUnlocksClient   # Token解锁时间表（DeFiLlama）
 └─ EventManager         # 事件数据源编排 → EventSnapshot
 │
ai/
├─ ClaudeClient
├─ TradingAdvisor
├─ prompts
└─ schemas
 │
risk/
 ├─ RiskGate           # 静态确定性风控
 ├─ PortfolioManager   # 组合快照与仓位预算
 └─ RiskDaemon         # 实时 WebSocket 风控守护
 │
pipeline/
 ├─ TradePipeline
 └─ TradeRunDB
```

## 市场快照层

`MarketDataManager` 会提取：

- 现货价格
- 合约标记价格
- 24h / 7d 涨跌
- 24h quote volume
- funding rate
- open interest
- basis
- 24h / 7d realized volatility
- EMA21 / EMA55 / EMA144
- 趋势偏向
- 资产层级
- 流动性状态
- 拥挤度
- BTC regime
- 相对 BTC 强弱
- execution template
- narrative tag

`CoinGeckoData` 额外补充（10min TTL 缓存，失败静默降级）：

- market_cap_usd
- market_cap_rank
- categories
- coingecko_volume_24h_usd
- market_cap_change_24h_pct
- fully_diluted_valuation_usd

## BTC 市场状态机

当前 `btc_market_regime`：

- `risk_on_trend`
- `risk_off_trend`
- `panic_flush`
- `rebound`
- `short_squeeze`
- `range`

用途：

- 决定 alt 是否值得参与
- 决定 execution template
- 决定 RiskGate 如何压缩风险

## 资产层级与叙事

### 资产层级

- `core`
- `major_alt`
- `liquid_alt`
- `mid_alt`
- `high_beta_alt`

### 叙事标签

79+ symbol 显式映射 + `general_alt` fallback：

- `store_of_value`
- `smart_contract_l1`
- `high_beta_l1`
- `exchange_ecosystem`
- `payments`
- `layer1`
- `modular_infra`
- `meme`
- `oracle`
- `defi`
- `liquid_staking`
- `restaking`
- `layer2`
- `ai_compute`
- `ai_agent`
- `gaming`
- `rwa`
- `depin`
- `privacy`
- `general_alt`（fallback）

二者共同影响：

- 风控阈值
- execution template
- 手动下单清单
- 组合/叙事集中警告

## execution template

当前模板包括：

- `core_trend_follow`
- `core_reclaim_wait`
- `core_range_trade`
- `alt_follow_with_confirmation`
- `alt_defensive_only`
- `alt_selective_range`
- `mid_alt_staged_entry`
- `high_beta_confirmation_only`

模板会直接映射到手动执行规则：

- `preferred_order_type`
- `staging_plan`
- `max_slippage_bps`
- `confirmation_checklist`
- `cancel_if`
- `template_risk_note`
- `operator_steps`
- `post_fill_protocol`
- `review_after_hours`

## 风控层

`RiskGate` 当前已覆盖：

- 仓位与杠杆上限
- 账户级 drawdown 熔断
- 高波动压杠杆 / 压仓
- funding / basis / OI 拥挤度
- BTC risk-off 对 alt 的影响
- alt 相对 BTC 弱势过滤
- high beta alt 更严格限制
- meme 更严格限制
- stable mode setup 质量闸门（quality score / grade）
- stable mode reward/risk 下限
- stable mode 同币种亏损后冷静期
- conviction override（高信心时放宽部分软门槛）
- execution template 专属规则
- 同叙事仓位集中警告
- 风控规则统计可通过 `trading risk-stats` 聚合查看，并显示 linked closed trades / win rate / avg P&L

## 执行层

当前系统是**人工执行模式**：

- 生成 `manual order ticket`
- 不自动实盘下单

ticket 会给出：

- 下单方向
- 数量
- 名义价值
- 杠杆
- entry zone / trigger / invalidation
- stop / take profit
- execution template
- setup quality score / grade
- operator steps
- review window

## 持久化层

SQLite 中保留：

- `logs`
- `trade_records`
- `trade_runs`
- `trade_reflections`

其中 `trade_runs` 当前已记录：

- research_decision
- execution_plan
- risk_decision
- execution_result
- account_snapshot
- position_snapshot
- market_snapshot
- adaptive_context

`trade_records` 现在还可选记录 `linked_run_id`，用于把手工平仓结果回挂到某次 pipeline 建议。

`trade_runs + trade_records` 还可通过 `replay-stats` 回放成真实样本，按 regime / template / narrative 评估建议质量。

在其之上，`edge-stats` 进一步提炼 expectancy、payoff ratio、profit factor，用来识别最赚钱的 slice。

进一步，`edge policy overlay` 会把这些统计结果反哺回当前 run，对历史强 edge slice 做轻度放大，对历史弱 edge slice 做降级或拦截。

在此之外，`backtest` 会对已保存的 run 信号回放历史 K 线，验证计划在真实价格路径里更常 hit TP 还是 SL。

它现在也支持 rolling windows 汇总，用来观察不同时间窗口下 expectancy / PF / drawdown proxy 是否稳定。

进一步，`walk-forward` 视角会把窗口拆成 train/test，帮助判断 edge 是否只在训练段成立，还是在后续样本里也能延续。

在 `batch-trade --auto-execute` 场景下，系统也已经改成“两段式”：

1. 全量分析并排序
2. 仅让 Top-N 候选池进入自动执行阶段

`replay-stats + risk-stats` 还可进一步驱动 `tune-risk`，输出保守型参数调优建议和 YAML patch。

若人工确认后需要真正修改配置，则通过 `ConfigPatchManager` 先备份、再 merge patch，并支持 `rollback-config` 回退。

Vault 中保留：

- 持仓同步文件
- 交易执行报告

## WebSocket 风控守护链路

```text
trading watch
    ↓
RiskDaemon.run()
    ├─ 从 Binance REST 加载当前持仓（entry_price / direction）
    ├─ 从 SQLite trade_runs 加载 invalidation_price / stop_loss_price
    └─ 启动 WebSocket 线程（wss://fstream.binance.com）
           │
           │  markPriceUpdate (1s)
           ▼
       _on_message()
           ├─ 更新 SymbolState（mark_price / funding_rate）
           └─ _check_alerts()
                  ├─ invalidation_price 突破告警
                  ├─ 近止损告警（距 stop < 50% 区间）
                  ├─ 近止盈通知（距 TP < 20% 区间）
                  └─ funding rate 拥挤告警
           │
    Rich Live 仪表盘（主线程，0.5s 刷新）
```

## 基础设施层

系统在 P0-P3 优化后具备以下基础设施特性：

| 特性 | 实现 | 说明 |
|------|------|------|
| 连接复用 | `TradePipeline.live_client` 懒加载 | 单例 BinanceClient，消除重复 TCP 握手 |
| 重试机制 | `core/retry.py` @retry 装饰器 | 3次指数退避，区分 Transient/Permanent 异常 |
| 分层缓存 | `market_data.py` TTLCache | K线5min / Ticker30s / Funding1min / Depth10s / Mark15s |
| 配置验证 | `config.py` validate_config() | 15+规则，errors阻止启动 / warnings仅告警 |
| SL/TP 补偿 | `runner.py` 止损重试逻辑 | 2次尝试 + CRITICAL 告警 |
| 请求超时 | Spot + Futures 客户端 | 10秒默认超时 |
| 批量查询 | `um_futures.py` exchange_info 缓存 | 1h TTL + get_symbols_lot_sizes() |

## 当前边界

当前已实现：

- 币圈市场快照（REST 点查 + TTL 分层缓存 + CoinGecko market_cap/rank 补充）
- AI 研究 + 执行计划（Claude，支持合并单调用模式节省 ~30% token）
- AI Prompt 注入 Portfolio 上下文（gross/net exposure、叙事集中度、仓位预算）
- Adaptive Prompt Guidance（基于历史胜率/连亏/反思/risk-stats 的确定性提示）
- Vault 上下文智能截断（按 symbol 相关性优先 + token 预算）
- 叙事标签映射（79+ 显式映射 + `general_alt` fallback）
- 确定性风控（RiskGate 规则引擎，13+规则文件，含 EventRiskRule）
- 组合快照与仓位预算（PortfolioManager）
- 资金曲线 EMA 保护（EquityCurveProtector）
- 动态杠杆调整（DynamicLeverageRule）
- 手动下单清单生成
- 实时 WebSocket 风控守护（RiskDaemon）
- 链上数据接入（DeFiLlama：协议 TVL / 链 TVL / 稳定币供应）
- CoinGecko 数据扩展（market_cap / rank / categories / FDV）
- 事件源接入（宏观经济日历 + Token解锁时间表，高影响事件自动压仓）
- 飞书通知推送（风控告警 / Pipeline完成 / SL触发 / 批量扫描）
- 多币种批量扫描（batch-trade）
- trade_runs ↔ trade_records 结果关联（`record --run-id` / 自动关联最近 run）
- 风控效果统计（频次 + linked outcome 上下文）
- 历史结果回放（按 regime/template/narrative/action/profile 聚合真实已平仓样本）
- 风控参数调优建议（`tune-risk`，默认只读分析，可导出 YAML patch）
- 风控参数安全应用 / 回滚（`tune-risk --apply --yes` + `rollback-config`）
- 基础设施（连接复用 + 重试 + 缓存 + 配置验证 + 超时）
- 301+ 自动化测试全通过

当前尚未实现：

- 自动真实开仓主链
- 异步化改造

注意：

- `trade` 主链当前不自动开新仓，只输出 manual order ticket
- `rebalance` / `batch-trade` 的自动调仓链仍可在非 `dry_run` 下真实执行

所以当前最准确的定义仍然是：

> **币圈投资研究、实时风控监控与人工执行操作系统**
