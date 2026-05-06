# Trading Bot

面向**区块链 / 币圈投资**的 AI 研究、风控与人工执行系统。

当前系统的核心定位不是“自动化下单机器人”，而是：

1. 读取 Binance 账户、仓位与公开市场数据
2. 结合 Obsidian Vault 中的个人交易体系做 AI 研究
3. 用确定性风控过滤研究结果
4. 输出**手动下单清单（manual order ticket）**
5. 把运行结果、市场快照与反思沉淀到 SQLite / Vault

---

## 当前能力

### 1. 账户与仓位

- Binance Spot + USDT-M Futures 账户读取
- 现货 / 合约余额查询
- 当前持仓与未实现 P&L 查询
- `account --dry-run` mock 预览

### 2. 币圈市场快照

每次 `trading trade` 会生成 `MarketSnapshot`，包括：

- 现货价 / 合约标记价
- 24h / 7d 涨跌
- 24h 成交额
- funding rate
- open interest
- basis
- 24h / 7d realized volatility
- EMA21 / EMA55 / EMA144
- 1h 趋势偏向
- 资产层级（`core / major_alt / liquid_alt / mid_alt / high_beta_alt`）
- 叙事标签（`meme / defi / layer2 / ai_agent ...`）
- 流动性状态
- 拥挤度
- BTC 市场状态机
- 相对 BTC 强弱
- execution template

### 3. AI 研究与执行计划

AI 会输出：

- `ResearchDecision`
  - thesis
  - market_structure
  - evidence
  - catalysts / risks
  - invalidation
  - time_horizon
  - preferred_market

- `ExecutionPlan`
  - action
  - size_pct / leverage
  - entry_style
  - entry zone
  - trigger / invalidation price
  - stop / take profit
  - thesis_window_hours

### 4. 币圈专用 RiskGate

当前风控覆盖：

- 仓位上限 / 杠杆上限
- 账户 drawdown 熔断
- 高波动压杠杆 / 压仓
- funding / basis / OI 拥挤度
- BTC risk-on / risk-off / panic flush / rebound / short squeeze 过滤
- alt 相对 BTC 强弱过滤
- `high_beta_alt` 更严格限制
- `meme` 更严格限制
- execution template 专属规则
- 同叙事持仓集中警告

### 5. 手动下单清单

当前 `trading trade` 默认**不自动下单**，而是输出：

- side / quantity / notional / leverage
- entry zone / trigger / invalidation
- stop / take profit
- execution template
- preferred order type
- staging plan
- max slippage
- operator steps
- confirmation checklist
- cancel conditions
- review window

也就是更像“币圈交易执行操作手册”，而不是直接代替你按下单按钮。

---

## 主要命令

```bash
# 系统状态
trading status

# 账户与持仓
trading account
trading account --dry-run

# 同步持仓到 Vault
trading sync --dry-run
trading sync --no-dry-run

# 运行完整币圈 AI 交易流程（输出手动下单清单）
trading trade BTCUSDT
trading trade ETHUSDT --dry-run

# 录入已平仓交易
trading record CHZUSDT --side long --entry 0.05 --exit 0.068 --qty 128000 --leverage 30 --open "2025-01-01 10:00"

# 查看交易日志
trading journal

# 生成交易反思
trading reflect --symbol BTCUSDT
```

---

## 安装

```bash
pip install -e ".[dev]"
```

或：

```bash
uv sync
```

---

## 配置

### .env

至少需要：

```env
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
ANTHROPIC_API_KEY=...
```

### config.yaml

最重要的配置块：

- `vault`
- `risk`
- `trading`
- `logging`

现在 `risk` 已经包含不少币圈专用参数，例如：

- 高波动阈值
- funding 拥挤阈值
- alt 相对 BTC 弱势阈值
- high beta alt 风险参数
- meme 风险参数
- narrative thesis window 限制

---

## 运行模式

### `account --dry-run`

- 使用 mock 账户与 mock 持仓
- 不访问真实 Binance

### `sync --dry-run`

- 使用 mock 持仓
- 只做 Vault 内容预览
- 不访问真实 Binance
- 不写 Vault

### `trade --dry-run`

- 使用 mock 账户快照与 mock 市场快照
- 仍然会完整跑：
  - AI 研究
  - AI 执行计划
  - 风控
  - 手动下单清单

### `trade`（非 dry-run）

- 会读取真实 Binance 账户/仓位/市场快照
- 会输出真实手动下单清单
- **不会自动真实下单**

---

## 目录结构

```text
src/trading/
├── core/         # config / vault / logger / journal
├── exchange/     # Binance 账户、仓位、订单、市场快照
├── ai/           # Claude client / prompts / schemas / advisor
├── risk/         # deterministic risk gate
├── pipeline/     # end-to-end trade pipeline + persistence
├── reports/      # 预留（当前报告构建在 pipeline 内）
└── main.py       # CLI
```

---

## 测试

```bash
python -m pytest -q
```

当前测试已覆盖：

- exchange
- journal
- schemas
- pipeline
- risk gate
- market data
- CLI safe preview behavior
- 回归修复

---

## 当前边界

当前已实现：

- 币圈市场快照
- AI 研究
- AI 执行计划
- 币圈专用风控
- 手动执行清单
- Vault 报告
- SQLite 持久化
- 交易反思

当前尚未实现：

- 自动真实下单主链
- 链上数据接入
- 解锁/上币下币/治理/监管等事件源
- 实时 WebSocket 风控守护
- 组合层统一构建与动态调仓

所以当前最准确的定位是：

> **面向币圈投资的 AI 研究 + 风控 + 人工执行操作系统**
