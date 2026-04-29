# 🤖 Trading Bot - AI 交易助手

基于 Obsidian Vault 知识体系的智能加密货币交易助手。

## 📋 核心特性

### ✅ Phase 1：基础框架 + 币安连接
- ✓ Binance API 集成（现货 + 合约）
- ✓ 账户余额与 P&L 查询
- ✓ 持仓管理和同步
- ✓ 结构化日志系统
- ✓ Obsidian Vault 知识库读写

### 🔄 Phase 2：Claude AI 分析引擎（规划中）
- Claude API 集成 + Prompt Caching
- 持仓智能分析与建议
- 币种调研报告生成
- 决策建议自动化

### ⚙️ Phase 3：风控自动化（规划中）
- 账户级熔断机制（L1/L2/L3）
- 止损/止盈管理
- BTC 黑天鹅检测
- 实时 WebSocket 监控

### 🎯 Phase 4：完整工作流（规划中）
- 交互式确认界面
- 自动报告生成
- 完整测试覆盖

---

## 🚀 快速开始

### 前置要求
- Python 3.11+
- Binance API 密钥
- Anthropic Claude API 密钥
- Obsidian Vault 已配置

### 安装

```bash
# 1. 复制 .env.example 为 .env
cp .env.example .env

# 2. 编辑 .env，填入 API 密钥
vi .env

# 3. 安装依赖（使用 pip）
pip install -e .

# 或使用 uv（推荐）
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### 基本命令

```bash
# 查看账户余额和持仓
trading account

# 同步持仓到 Vault（预览模式）
trading sync

# 实际写入 Vault（谨慎！）
trading sync --no-dry-run

# 查看系统状态
trading status

# 帮助
trading --help
```

---

## 📂 项目结构

```
trading-bot/
├── src/trading/
│   ├── core/              # 核心模块
│   │   ├── config.py      # 配置管理
│   │   ├── vault.py       # Vault 读写
│   │   └── logger.py      # 日志系统
│   ├── exchange/          # 币安交互
│   │   ├── client.py      # 基础客户端
│   │   ├── account.py     # 账户管理
│   │   ├── positions.py   # 持仓查询
│   │   ├── orders.py      # 订单管理
│   │   └── stream.py      # WebSocket 流
│   ├── ai/                # AI 分析（Phase 2）
│   ├── risk/              # 风控模块（Phase 3）
│   ├── reports/           # 报告生成（Phase 2）
│   └── main.py            # CLI 入口
├── tests/                 # 单元测试
├── data/                  # 本地数据
│   └── trades.db          # SQLite 日志
├── config.yaml            # 风控参数
├── .env.example           # 密钥模板
├── .env                   # 实际密钥（gitignore）
├── pyproject.toml         # 依赖定义
└── README.md              # 本文件
```

---

## ⚙️ 配置说明

### config.yaml 关键参数

```yaml
# 风控参数（从交易体系提取）
risk:
  max_account_drawdown: -0.20    # L2：全部平仓
  alert_drawdown: -0.10          # L1：告警
  suspend_drawdown: -0.30        # L3：暂停交易
  max_leverage: 35               # 杠杆上限
  btc_crash_threshold: -0.10     # BTC -10% 触发减仓
  btc_black_swan: -0.15          # BTC -15% 触发
```

### .env 必填项

```
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
ANTHROPIC_API_KEY=sk-ant-...
```

---

## 📊 Phase 1 命令详解

### `trading account`
显示账户概览：
- 余额（现货 + 合约）
- P&L 百分比
- 所有开仓持仓
- 杠杆信息

```bash
trading account
trading account --dry-run
```

### `trading sync`
同步持仓到 Vault：
- 读取 Binance 实时持仓
- 格式化为 Markdown 表格
- 预览后写入 Vault `【持仓管理】/` 目录

```bash
trading sync              # 预览模式（默认安全）
trading sync --no-dry-run # 真实写入
```

### `trading status`
检查系统状态：
- Vault 文件是否存在
- Binance API 连接
- Claude API 是否配置

---

## 🛡️ 风控规则（硬编码）

所有风控参数来自 Vault `合约交易体系-完整指南.md`：

| 规则 | 触发条件 | 动作 |
|------|---------|------|
| **L1 告警** | 浮亏 -10% | 发出告警，建议减仓 |
| **L2 清仓** | 浮亏 -20% | **自动全部平仓**（无需确认） |
| **L3 暂停** | 浮亏 -30% | 禁止新交易 |
| **BTC 暴跌** | 单日 -10% | 主动减仓 30% |
| **黑天鹅** | 单日 -15% | 主动减仓 50% |
| **利润锁定** | 浮盈 >50% | 锁定 50% 头寸 |
| **止损** | 开仓即挂 | 3-5%，不可修改 |

---

## 🔐 安全提示

⚠️ **重要：**
- `.env` 文件包含敏感信息，**绝不提交到 Git**
- `--dry-run` 是默认模式，所有写操作必须显式确认
- 在生产环境前充分测试所有命令
- 定期检查日志 `data/trades.db`

---

## 📈 开发进度

### Phase 1 ✅
- [x] 项目初始化
- [x] 配置系统
- [x] Vault 集成
- [x] Binance API 连接
- [x] CLI 框架（account, sync, status）
- [x] 日志系统
- [ ] 完整测试

### Phase 2 🔄
- [ ] Claude AI 客户端
- [ ] Prompt Caching
- [ ] 持仓分析 Prompt
- [ ] 币种调研 Prompt
- [ ] 决策建议 Prompt
- [ ] CLI 命令：analyze, research, decision

### Phase 3 📋
- [ ] 风控守护进程
- [ ] 熔断机制实现
- [ ] 止损/止盈管理
- [ ] 黑天鹅检测
- [ ] WebSocket 流

### Phase 4 📅
- [ ] 交互式确认界面
- [ ] 完整工作流集成
- [ ] 性能优化
- [ ] 生产就绪

---

## 🧪 测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行特定测试
python -m pytest tests/test_exchange.py -v

# 生成覆盖率报告
python -m pytest --cov=trading tests/
```

---

## 📞 日志和调试

查看最近的日志：

```bash
python -c "
from trading.core import TradingLogger, LoggingConfig
config = LoggingConfig(sqlite_db='data/trades.db')
table = TradingLogger.get_logs_table('data/trades.db', limit=20)
from rich.console import Console
Console().print(table)
"
```

---

## 🤝 贡献

目前为个人项目。欢迎反馈和改进建议！

---

## 📄 许可

私人项目。

---

## 🎯 下一步

1. **验证 Phase 1**：运行 `trading account` 确认连接正常
2. **配置风控**：检查 `config.yaml` 中的风控参数
3. **测试同步**：运行 `trading sync` 预览持仓同步
4. **启动 Phase 2**：实现 Claude AI 分析引擎

---

**最后更新**: 2024-12-15  
**当前版本**: 0.1.0 (Phase 1 - Beta)
