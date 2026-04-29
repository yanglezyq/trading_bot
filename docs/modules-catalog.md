# 模块清单

> 变更此文件时，同步更新 `.qoder/rules/project-architecture.md` 的模块清单表。

## core/ · 基础设施层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| config | `src/trading/core/config.py` | `AppConfig`, `load_config`, `VaultConfig`, `BinanceConfig`, `ClaudeConfig`, `RiskConfig`, `TradingConfig`, `MonitorConfig`, `LoggingConfig` | 从 config.yaml + .env + CLI 参数合并配置，提供强类型 dataclass |
| vault | `src/trading/core/vault.py` | `VaultReader` | Obsidian Vault 读写：交易体系文档、持仓追踪、研究报告 |
| logger | `src/trading/core/logger.py` | `TradingLogger`, `SQLiteHandler` | Rich 控制台 + SQLite 双输出结构化日志 |

**导出入口**：`src/trading/core/__init__.py`
```python
from .config import AppConfig, BinanceConfig, load_config
from .logger import TradingLogger
from .vault import VaultReader
```

---

## exchange/ · 交易所适配层

| 模块 | 文件 | 主要导出 | 责任 |
|------|------|---------|------|
| client | `src/trading/exchange/client.py` | `BinanceClient` | Binance Spot + UMFutures 统一封装，支持 testnet 和 dry_run |
| um_futures | `src/trading/exchange/um_futures.py` | （内部） | UMFutures 兼容层；binance-connector 3.x 不含 `um_futures` 子模块，本地封装 `/fapi/` 端点 |
| account | `src/trading/exchange/account.py` | `AccountManager`, `Balance`, `FuturesBalance`, `AccountPnL` | 余额查询（现货+合约）、P&L 计算 |
| positions | `src/trading/exchange/positions.py` | `PositionManager`, `FuturesPosition`, `SpotPosition` | 合约持仓 + 现货持仓查询，计算名义价值 |
| orders | `src/trading/exchange/orders.py` | `OrderManager`, `Order` | 市价单、限价单、止损单、止盈单下单与撤单 |

**导出入口**：`src/trading/exchange/__init__.py`
```python
from .client import BinanceClient
from .account import AccountManager
from .positions import PositionManager
from .orders import OrderManager
```

---

## ai/ · AI 分析层（Phase 2，待实现）

| 模块 | 文件 | 计划导出 | 责任 |
|------|------|---------|------|
| client | `src/trading/ai/client.py` | `ClaudeClient` | Claude API 封装，支持 Prompt Caching |
| prompts | `src/trading/ai/prompts/` | Prompt 模板函数 | 持仓分析、币种调研、决策建议三类 Prompt |

---

## risk/ · 风控层（Phase 3，待实现）

| 模块 | 文件 | 计划导出 | 责任 |
|------|------|---------|------|
| monitor | `src/trading/risk/monitor.py` | `RiskMonitor` | L1/L2/L3 熔断检测，BTC 黑天鹅检测 |
| circuit_breaker | `src/trading/risk/circuit_breaker.py` | `CircuitBreaker` | 自动平仓、暂停交易执行 |

---

## reports/ · 报告层（Phase 2，待实现）

| 模块 | 文件 | 计划导出 | 责任 |
|------|------|---------|------|
| generator | `src/trading/reports/generator.py` | `ReportGenerator` | AI 分析结果格式化为 Vault Markdown |

---

## main.py · CLI 入口

| 命令 | 函数 | 描述 |
|------|------|------|
| `trading account` | `account()` | 显示账户余额 + 合约持仓 + P&L |
| `trading sync` | `sync()` | 同步持仓到 Obsidian Vault（默认 dry_run） |
| `trading status` | `status()` | 检查系统组件状态 |

---

## 数据类速查

| 类名 | 所在模块 | 用途 |
|------|---------|------|
| `AppConfig` | core.config | 顶层配置聚合对象 |
| `VaultConfig` | core.config | Vault 路径配置 |
| `BinanceConfig` | core.config | Binance API 配置 |
| `ClaudeConfig` | core.config | Claude AI 配置 |
| `RiskConfig` | core.config | 风控参数 |
| `Balance` | exchange.account | 现货资产余额 |
| `FuturesBalance` | exchange.account | 合约账户余额 |
| `AccountPnL` | exchange.account | 账户级 P&L |
| `FuturesPosition` | exchange.positions | 合约持仓 |
| `SpotPosition` | exchange.positions | 现货持仓 |
| `Order` | exchange.orders | 订单信息 |
