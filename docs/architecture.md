# 系统架构

## 设计原则

1. **安全优先**：所有写操作默认 dry_run，真实操作需显式开启
2. **知识驱动**：AI 分析基于 Obsidian Vault 中的个人交易体系，而非通用建议
3. **分层隔离**：core（基础设施）→ exchange（交易所适配）→ ai/risk/reports（业务逻辑）
4. **可观测性**：每次操作双写 Rich 控制台 + SQLite，支持事后审计

## 分层架构图

```
┌─────────────────────────────────────────────────────┐
│                  CLI (Typer)                         │
│   trading account | trading sync | trading status    │
└─────────────────┬───────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────┐
│                 core/                                │
│  AppConfig ← config.yaml + .env + CLI flag          │
│  VaultReader ← Obsidian Vault 读写                   │
│  TradingLogger ← Rich 控制台 + SQLite                │
└──────┬──────────────────────────┬───────────────────┘
       │                          │
┌──────▼──────┐           ┌──────▼──────────────────┐
│  exchange/  │           │  ai/  (Phase 2)          │
│  BinanceClient          │  Claude API + Caching    │
│  AccountManager         │  Prompt 模板              │
│  PositionManager        └──────────────────────────┘
│  OrderManager                   │
└──────┬──────┘           ┌──────▼──────────────────┐
       │                  │  reports/  (Phase 2)     │
       │                  │  报告生成与格式化          │
       │                  └──────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────┐
│  risk/  (Phase 3)                                    │
│  L1/L2/L3 熔断 | BTC 黑天鹅 | 止损止盈守护进程        │
└─────────────────────────────────────────────────────┘
```

## 配置加载流程

```
config.yaml（基础参数）
    +
.env（敏感凭证：API key）
    +
CLI flag（运行时覆盖：--dry-run）
    ↓
load_config() → AppConfig（统一配置对象）
    ↓
各模块接收 XxxConfig 子对象
```

## dry_run 传递链路

```
CLI flag --dry-run
    ↓
AppConfig.trading.dry_run
    ↓
BinanceClient(dry_run=dry_run)
    ↓
OrderManager.place_xxx(dry_run=client.dry_run)  ← 方法参数可覆盖
```

注意：读操作（account/positions）即使 dry_run=False 也安全，dry_run 主要保护写操作。

## 外部依赖

| 依赖 | 用途 | 连通性要求 |
|------|------|-----------|
| Binance Futures API | 合约账户、持仓、下单 | 需网络 + API key |
| Binance Spot API | 现货账户余额 | 需网络 + API key |
| Anthropic Claude API | AI 分析（Phase 2） | 需网络 + API key |
| Obsidian Vault | 交易知识库读写 | 本地文件系统 |
| SQLite | 日志持久化 | 本地文件系统 |

## Phase 开发状态

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 基础框架 + Binance 连接 + CLI | ✅ 完成 |
| Phase 2 | Claude AI 分析引擎 + 报告生成 | 🔄 规划中 |
| Phase 3 | 风控守护进程 + WebSocket 监控 | 📋 待启动 |
| Phase 4 | 完整工作流 + 交互式确认 | 📋 待启动 |
