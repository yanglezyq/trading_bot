# 环境搭建与使用指南

## 前置要求

- Python 3.11+
- Binance API Key（读取真实账户/真实市场快照时需要）
- Anthropic Claude API Key（运行 `trading trade` / `trading reflect` 时需要）
- Obsidian Vault（用于交易体系、持仓追踪、执行报告）

## 安装

```bash
cd trading-bot
pip install -e ".[dev]"
```

或：

```bash
uv sync
```

## 配置

### 1. 环境变量

```bash
cp .env.example .env
```

填写：

```env
BINANCE_API_KEY=你的币安 API Key
BINANCE_API_SECRET=你的币安 API Secret
ANTHROPIC_API_KEY=你的 Claude API Key
```

### 2. Vault 路径

在 `config.yaml` 中配置：

```yaml
vault:
  path: "/Users/你的用户名/Documents/Obsidian Vault"
```

也可以用：

```env
OBSIDIAN_VAULT_PATH=/path/to/your/vault
```

### 3. Vault 最小目录结构

```text
Obsidian Vault/
└── 体系化交易/
    ├── 合约交易体系-完整指南.md
    ├── 【持仓管理】/
    └── 【报告】/
```

## 命令说明

### `trading status`

检查：

- Vault
- Binance API
- Claude API

```bash
trading status
```

### `trading account`

```bash
trading account
trading account --dry-run
```

- `--dry-run`：mock 账户和 mock 持仓，不访问真实 Binance。

### `trading sync`

```bash
trading sync --dry-run
trading sync --no-dry-run
```

当前行为：

- `--dry-run`
  - 使用 mock 持仓
  - 只预览
  - 不访问真实 Binance
  - 不写 Vault
- `--no-dry-run`
  - 读取真实 Binance 仓位
  - 写入 Vault

### `trading trade`

```bash
trading trade BTCUSDT
trading trade ETHUSDT --dry-run
```

当前行为：

- 不自动下单
- 输出：
  - `Market Snapshot`
  - `ResearchDecision`
  - `ExecutionPlan`
  - `RiskDecision`
  - `Manual Order Ticket`
- 会写：
  - SQLite `trade_runs`
  - Vault 执行报告（默认开启）

### `trading record`

手工录入已平仓交易：

```bash
trading record BTCUSDT --side long --entry 50000 --exit 54000 --qty 0.2 --leverage 5 --open "2025-01-01 10:00"
trading record BTCUSDT --side long --entry 50000 --exit 54000 --qty 0.2 --leverage 5 --open "2025-01-01 10:00" --run-id 42
```

- 支持 `--run-id` 显式关联某次 pipeline run
- 不传时默认尝试自动关联最近一次同 `symbol` 的 `trade` 结果

### `trading journal`

查看历史交易与统计：

```bash
trading journal
trading journal --symbol BTCUSDT
```

### `trading reflect`

对历史交易生成 AI 反思：

```bash
trading reflect --symbol BTCUSDT
```

### `trading risk-stats`

统计最近 pipeline 记录里最常出现的 warning / blocking 规则，并尽量关联已平仓结果：

```bash
trading risk-stats
trading risk-stats --symbol BTCUSDT
```

输出会包含：

- 规则触发频次
- linked runs / linked trades
- linked win rate
- avg realized P&L

### `trading replay-stats`

回放已关联的真实结果样本：

```bash
trading replay-stats
trading replay-stats --symbol BTCUSDT
```

会按：

- BTC regime
- execution template
- narrative
- final action
- adaptive profile

输出 win rate 与 avg P&L 聚合。

### `trading edge-stats`

从 linked 样本计算更贴近赚钱能力的统计：

```bash
trading edge-stats
trading edge-stats --symbol BTCUSDT
```

输出会包含：

- expectancy
- avg win / avg loss
- payoff ratio
- profit factor

并按 regime / template / narrative / quality / gating profile 切片。

这层统计现在也会反哺回主链：

- 强 edge slice：轻度放大
- 弱 edge slice：降级或拦截
- 样本不足：保持中性

### `trading tune-edge`

把 edge 研究结果转成 allowlist / denylist 建议：

```bash
trading tune-edge
trading tune-edge --symbol BTCUSDT
trading tune-edge --output edge-policy.yaml
```

输出的是：

- `trading.edge_policy_allowlist`
- `trading.edge_policy_denylist`

可以用来把最赚钱或最差的 slice 固化进配置。

如果你确认建议合理，也可以直接应用：

```bash
trading tune-edge --apply --yes
```

### `trading backtest`

对已保存的 run 信号做历史 K 线回放：

```bash
trading backtest
trading backtest --symbol BTCUSDT --limit 20 --hours 48
```

当前回放会模拟：

- stop loss
- take profit
- time exit

并额外输出 rolling windows 视角，方便看收益是否稳定。

现在也会额外输出 walk-forward windows，方便比较训练段和测试段表现是否一致。

适合先验证现有 plan 在历史价格路径里是更容易 hit TP 还是 hit SL。

### `trading batch-trade --auto-execute`

现在会分两段执行：

1. 先全量分析所有币种
2. 再只对 Top-N 候选池进入自动执行

这样不会因为扫描顺序而把资金分散到较弱机会。

另外还可以单独限制真正进入自动执行的名额：

- `monitor.batch_auto_execute_max_candidates`

### `trading tune-risk`

基于 linked 样本生成保守型风控参数调优建议：

```bash
trading tune-risk
trading tune-risk --symbol DOGEUSDT
trading tune-risk --output tuned-risk.yaml
```

默认只展示建议；`--output` 会导出一个独立 YAML patch，不会覆盖原始 `config.yaml`。

如果你确认建议合理，也可以直接应用：

```bash
trading tune-risk --apply --yes
```

系统会先自动备份当前 `config.yaml`。

### `trading rollback-config`

查看 / 回滚配置备份：

```bash
trading rollback-config --list
trading rollback-config --yes
```

如需恢复指定备份：

```bash
trading rollback-config --backup .trading-config-backups/config-tune-risk-20260507-120000.yaml --yes
```

## 推荐启动顺序

```bash
trading status
trading account --dry-run
trading sync --dry-run
trading trade BTCUSDT --dry-run
```

确认 Market Snapshot、RiskDecision、Manual Order Ticket 都符合预期后，再切换到真实账户读取模式。

## Dry-run 的边界

### `account --dry-run`

- 使用 mock 账户
- 使用 mock 持仓

### `sync --dry-run`

- 使用 mock 持仓
- 只预览 Vault 内容

### `trade --dry-run`

- 使用 mock 账户快照
- 使用 mock 市场快照
- 仍会运行完整 AI 研究 / 计划 / 风控 / 手动下单清单逻辑

### `trade --auto-execute`

- 显式请求真实 API 自动执行
- 只有通过自动执行专用安全闸门，才会真正发单
- 否则会降级成 `auto_execute_blocked` / 手动复核

自动执行专用安全闸门包括：

- symbol / market 白名单
- `auto_execute_live_enabled` 真实环境显式放行
- `auto_execute_kill_switch` 全局一键停自动执行
- 最低 confidence / consensus / setup quality
- warnings 数上限
- 同币种未完成订单阻塞
- 止损止盈是否齐备
- entry zone 是否仍有效
- 单币种冷却
- 24h 自动执行次数上限
- 24h 自动执行累计名义金额上限

## 当前系统边界

当前已实现：

- 币圈市场快照（TTL 分层缓存）
- AI 研究与执行计划
- 确定性 RiskGate（规则引擎，12+规则文件）
- 组合快照与仓位预算
- 资金曲线 EMA 保护
- 动态杠杆调整
- 手动下单清单
- 多模型分歧投票（ResearchDecision 共识层，可选）
- 交易反思
- 实时 WebSocket 风控守护
- 链上数据接入（DeFiLlama）
- 基础设施（连接复用 + 重试 + 缓存 + 配置验证 + 超时）
- SQLite / Vault 持久化
- Prompt 自适应（基于历史胜率/连亏/反思/risk-stats）
- 风控效果统计（频次 + linked outcome 上下文）
- 历史结果回放（按 regime/template/narrative/action/profile 聚合）
- Edge 研究统计（按 expectancy / PF / payoff ratio 找赚钱 slice）
- 收益导向 edge policy overlay（把历史 edge 反哺回真实下单资格与仓位）
- Alpha allowlist / denylist 建议（把赚钱 slice 固化进配置）
- Batch Top-N 机会池（按 edge / quality / confidence 排序）
- 历史 K 线回放 Backtest（按已保存 run 信号回放价格路径）
- 风控参数调优建议（只读建议 + YAML patch 导出）
- 风控参数安全应用 / 回滚
- stable mode 高质量 setup 闸门（含 reward/risk 下限与亏损后冷静期）
- conviction override（高信心时可放宽部分 soft gate）
- 真实 API 自动执行链（默认关闭，需 `--auto-execute` 或显式配置开启）
- 统一机会分数 Opportunity Score（排序与自动执行门槛）
- 301+ 自动化测试用例

当前未实现：

- 自动真实开仓主链

## 真实环境预演 Checklist

建议在真实账户前按顺序执行：

1. `trading status`
2. `trading account --dry-run`
3. `trading sync --dry-run`
4. `trading trade BTCUSDT --dry-run`
5. `trading batch-trade BTCUSDT ETHUSDT --dry-run`

确认以下几点：

- Vault 路径正确且系统指南文件存在
- `Market Snapshot` 中的 `BTC Regime / Narrative / Execution Template` 合理
- `RiskDecision` 没有明显错误或过度放宽
- `Manual Order Ticket` 中的 `preferred_order_type / staging_plan / cancel_if` 可执行
- 如果你不希望系统出现任何真实调仓单，请确认：
  - `rebalance.enabled = false`
  - 或仅使用 `--dry-run`

真实环境注意：

- `trading trade` 当前不会自动开新仓
- `trading rebalance --no-dry-run` 和 `batch-trade` 在 `rebalance.auto_after_batch=true` 下，仍可能真实执行调仓

## 长期增强方向

- 上下币 / 解锁 / 治理 / 宏观事件层
- 组合动态调仓执行引擎
- 异步化改造
- 历史回测 / 结果回放
- 风控参数自动审批 / 分级应用
- 外部监控告警集成

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `Anthropic API key not configured` | 缺少 Claude key | 设置 `ANTHROPIC_API_KEY` |
| `Trading directory not found` | Vault 路径错误 | 检查 `config.yaml` 的 `vault.path` |
| `Binance API credentials must be configured` | 在非 dry-run 下读取真实 Binance | 配置 `.env` 或改用 `--dry-run` |
| `trading: command not found` | 包未安装 | 运行 `pip install -e .` |

## 开发命令

```bash
python -m pytest -q
black src/ tests/
ruff check src/ tests/
mypy src/
```
