<!-- Qoder Rule Type: Model Decision -->
<!-- 场景描述: 需要理解项目结构、数据流或模块关系时 -->

# 项目架构

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.11+ | 使用新式类型语法 |
| CLI 框架 | Typer + Rich | 命令行界面 + 终端渲染 |
| Binance API | binance-connector 3.x | 现货 Spot + 合约 UMFutures |
| AI 分析 | Anthropic Claude API | Phase 2，claude-sonnet-4-6 |
| 配置 | PyYAML + python-dotenv | config.yaml + .env 分离 |
| 数据处理 | pandas + numpy | 行情分析（Phase 2） |
| 日志 | Python logging + SQLite | Rich 控制台 + data/trades.db |
| 测试 | pytest + pytest-asyncio + pytest-mock | dry_run 模式替代 mock |
| 代码质量 | Black + Ruff + mypy strict | CI 必须通过 |

## 分层架构

```
CLI (typer)
    ↓
core/          ← 基础设施层（配置、日志、Vault）
exchange/      ← 交易所适配层（Binance 现货 + 合约）
ai/            ← AI 分析层（Claude，Phase 2）
risk/          ← 风控层（熔断、止损，Phase 3）
reports/       ← 报告生成层（Phase 2）
```

## 数据流

```
CLI (main.py)
 ├── Binance API → BinanceClient
 │    ├── AccountManager   （余额、P&L）
 │    ├── PositionManager  （持仓快照）
 │    └── OrderManager     （下单，dry_run 保护）
 │
 └── Obsidian Vault → VaultReader
      ├── 读：交易体系文档 / 调研报告 / 持仓决策
      └── 写：仓位追踪文件 / AI 分析报告

          ↓ Phase 2
     Claude AI（合并 Binance 数据 + Vault 知识做分析）
          ↓
     VaultReader.write_report()（写回 Vault）

TradingLogger（所有操作双写 Rich 控制台 + SQLite）
```

## 模块清单

| 模块 | 文件 | 责任 | Phase |
|------|------|------|-------|
| `core.config` | `src/trading/core/config.py` | 配置加载与合并（yaml + env + CLI） | 1 ✅ |
| `core.vault` | `src/trading/core/vault.py` | Obsidian Vault 读写 | 1 ✅ |
| `core.logger` | `src/trading/core/logger.py` | Rich 控制台 + SQLite 双输出日志 | 1 ✅ |
| `exchange.client` | `src/trading/exchange/client.py` | Binance Spot + UMFutures 统一封装 | 1 ✅ |
| `exchange.um_futures` | `src/trading/exchange/um_futures.py` | UMFutures 兼容层（binance-connector 3.x 不含此模块，本地实现） | 1 ✅ |
| `exchange.account` | `src/trading/exchange/account.py` | 余额查询、P&L 计算 | 1 ✅ |
| `exchange.positions` | `src/trading/exchange/positions.py` | 合约持仓 + 现货持仓查询 | 1 ✅ |
| `exchange.orders` | `src/trading/exchange/orders.py` | 市价单、限价单、止损止盈、撤单 | 1 ✅ |
| `ai.client` | `src/trading/ai/` | Claude API 客户端 + Prompt Caching | 2 🔄 |
| `ai.prompts` | `src/trading/ai/prompts/` | 持仓分析、调研报告、决策建议 Prompt | 2 🔄 |
| `risk.monitor` | `src/trading/risk/` | L1/L2/L3 熔断、BTC 黑天鹅检测 | 3 📋 |
| `reports` | `src/trading/reports/` | AI 报告生成与格式化 | 2 🔄 |
| `main` | `src/trading/main.py` | CLI 入口（account/sync/status） | 1 ✅ |

## CLI 命令

| 命令 | 功能 | 默认模式 |
|------|------|---------|
| `trading account` | 查看账户余额 + 合约持仓 + P&L | 读数据（安全） |
| `trading sync` | 同步持仓到 Vault | dry_run=True（默认预览） |
| `trading status` | 检查系统组件状态 | 读数据（安全） |

## 风控硬参数（来自 config.yaml + 交易体系文档）

| 规则 | 参数 | 值 | 动作 |
|------|------|-----|------|
| L1 告警 | alert_drawdown | -10% | 告警 |
| L2 自动清仓 | max_account_drawdown | -20% | 全部平仓 |
| L3 暂停 | suspend_drawdown | -30% | 禁止新单 |
| 杠杆上限 | max_leverage | 35x | 拒绝开仓 |
| BTC 暴跌减仓 | btc_crash_threshold | -10% | 减仓 30% |
| BTC 黑天鹅 | btc_black_swan | -15% | 减仓 50% |
| 利润锁定 | profit_lock_threshold | +50% | 锁定 50% |

## Vault 目录约定

```
Obsidian Vault/
└── 体系化交易/
    ├── 合约交易体系-完整指南.md     ← 核心交易规则（AI 系统 Prompt 来源）
    ├── 决策建议生成规范.md
    ├── 币种调研报告规范(v2.0-通用版).md
    ├── 【持仓管理】/
    │   └── 仓位追踪-{date}.md       ← trading sync 写入
    └── 【报告】/
        └── 【报告】{symbol}-{date}.md ← AI 分析报告写入
```

## 配置加载优先级

```
CLI flag（最高）> 环境变量（.env）> config.yaml（最低）
```

敏感字段（API key）只存 .env，不写 config.yaml。
