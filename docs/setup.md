# 环境搭建与使用指南

## 前置要求

- Python 3.11+
- Binance 账户 + API Key（需开启合约权限）
- Anthropic Claude API Key（Phase 2 起需要）
- Obsidian Vault 已配置（路径见 config.yaml）

## 安装

```bash
# 克隆项目后进入目录
cd trading-bot

# 安装（含开发依赖）
pip install -e ".[dev]"
# 或使用 uv（更快）
uv sync
```

## 配置

### 第一步：密钥配置

```bash
cp .env.example .env
```

编辑 `.env`，填入：

```
BINANCE_API_KEY=你的币安 API Key
BINANCE_API_SECRET=你的币安 API Secret
ANTHROPIC_API_KEY=sk-ant-...
```

**注意**：`.env` 已在 `.gitignore`，绝不提交。

### 第二步：Vault 路径

编辑 `config.yaml`，确认：

```yaml
vault:
  path: "/Users/你的用户名/Documents/Obsidian Vault"
```

或在 `.env` 中覆盖：

```
OBSIDIAN_VAULT_PATH=/path/to/your/vault
```

### 第三步：Vault 目录结构

确保 Vault 内存在以下结构（`trading sync` 写入前会自动创建子目录）：

```
Obsidian Vault/
└── 体系化交易/
    ├── 合约交易体系-完整指南.md    ← 必须存在
    ├── 【持仓管理】/               ← 自动创建
    └── 【报告】/                   ← 自动创建
```

## CLI 命令

### `trading status` · 系统状态检查

```bash
trading status
```

输出：Vault 路径、Binance API 连通性、Claude API 配置状态。**建议安装后第一个运行的命令。**

### `trading account` · 账户概览

```bash
trading account              # 查看真实账户（需 API key）
trading account --dry-run    # 使用模拟数据预览
```

输出：
- 合约账户余额 (USDT)
- 现货账户余额 (USDT)
- 总余额、未实现 P&L、回撤 %
- 所有开仓合约持仓（交易对、数量、开仓价、杠杆、P&L）

### `trading sync` · 同步持仓到 Vault

```bash
trading sync              # 预览模式（默认，安全）
trading sync --no-dry-run # 真实写入 Vault（谨慎）
```

写入路径：`Obsidian Vault/体系化交易/【持仓管理】/仓位追踪-{datetime}.md`

## 开发命令

```bash
# 运行全部测试（dry_run 模式，无需 API key）
python -m pytest tests/ -v

# 运行特定模块测试
python -m pytest tests/test_exchange.py -v

# 测试覆盖率
python -m pytest tests/ --cov=trading --cov-report=term-missing

# 代码格式化
black src/ tests/
ruff check src/ tests/

# 类型检查
mypy src/
```

## 查看日志

```bash
python -c "
from trading.core import TradingLogger
table = TradingLogger.get_logs_table('data/trades.db', limit=20)
from rich.console import Console
Console().print(table)
"
```

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `ValueError: Binance API credentials must be configured` | .env 未配置 | 复制 .env.example 并填入 key |
| `ValueError: Trading directory not found` | Vault 路径错误 | 检查 config.yaml 的 vault.path |
| `trading: command not found` | 包未安装 | 运行 `pip install -e .` |
| 测试全部失败 | 依赖未安装 | 运行 `pip install -e ".[dev]"` |

## 安全提示

- 使用 `--dry-run` 作为默认工作模式，验证无误后再去掉
- `trading sync --no-dry-run` 会写入 Vault，确认路径正确再执行
- 生产账户操作前，先用 Binance testnet 验证（config.yaml 中设置 `futures_testnet: true`）
