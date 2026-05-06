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

## 当前主链

```text
trading trade
    ↓
load_config()
    ↓
TradePipeline.run()
    ├─ _get_account_summary()
    ├─ _get_positions()
    ├─ _get_market_snapshot()
    ├─ _get_vault_context()
    ├─ _get_trade_history()
    └─ _get_reflections()
    ↓
TradingAdvisor.generate_research()
    ↓
TradingAdvisor.generate_execution_plan()
    ↓
RiskGate.evaluate()
    ↓
_build_manual_order_ticket()
    ↓
_write_vault_report()
    ↓
TradeRunDB.save_run()
```

## 分层结构

```text
CLI
 ├─ account / sync / status / record / journal / trade / reflect
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
 └─ MarketDataManager  # 币圈专用市场快照
 │
ai/
 ├─ ClaudeClient
 ├─ TradingAdvisor
 ├─ prompts
 └─ schemas
 │
risk/
 └─ RiskGate
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

- `store_of_value`
- `smart_contract_l1`
- `high_beta_l1`
- `exchange_ecosystem`
- `payments`
- `layer1`
- `meme`
- `oracle`
- `defi`
- `layer2`
- `ai_compute`
- `ai_agent`
- `general_alt`

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
- execution template 专属规则
- 同叙事仓位集中警告

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

Vault 中保留：

- 持仓同步文件
- 交易执行报告

## 当前边界

当前尚未实现：

- 自动真实下单主链
- 链上数据
- 解锁/上下币/治理/监管事件层
- 实时 WebSocket 风控守护
- 组合优化层

所以当前最准确的定义仍然是：

> **币圈投资研究与人工执行操作系统**
