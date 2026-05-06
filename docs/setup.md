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
```

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

## 当前系统边界

当前已实现：

- 币圈市场快照
- AI 研究与执行计划
- 确定性 RiskGate
- 手动下单清单
- 交易反思
- SQLite / Vault 持久化

当前未实现：

- 自动真实下单主链
- 链上数据接入
- 上下币 / 解锁 / 治理 / 宏观事件层
- 组合优化层

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
