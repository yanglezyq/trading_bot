# 🚀 快速开始指南

## 5 分钟内启动 Trading Bot

### Step 1: 环境配置（1 分钟）

```bash
cd 体系化交易/_projects/trading-bot

# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入你的 API 密钥
# BINANCE_API_KEY=你的币安 API Key
# BINANCE_API_SECRET=你的币安 API Secret
# ANTHROPIC_API_KEY=你的 Claude API Key
```

**获取 API 密钥：**
- **Binance**: https://www.binance.com/en/account/api-management
- **Claude**: https://console.anthropic.com

### Step 2: 安装依赖（2 分钟）

**使用 uv（推荐，最快）：**
```bash
# 安装 uv（如果还没装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装项目依赖
uv sync
```

**或使用 pip：**
```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -e .
```

### Step 3: 验证安装（1 分钟）

```bash
# 查看帮助
trading --help

# 检查系统状态
trading status
```

**预期输出：**
```
🔍 System Status
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ Component         ┃ Status         ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ Vault Directory   │ ✓ OK           │
│ System Guide      │ ✓ OK           │
│ Reports           │ 12 files       │
│ Binance API       │ ✓ OK           │
│ Claude API        │ ✓ Configured   │
└───────────────────┴────────────────┘
```

### Step 4: 运行第一个命令（1 分钟）

```bash
# 查看账户余额和持仓
trading account

# 预览持仓同步（不会真的写入 Vault）
trading sync

# 同步持仓到 Vault（实际写入）
trading sync --no-dry-run
```

---

## 🧪 验证检查清单

- [ ] `.env` 已填入三个 API 密钥
- [ ] `trading --help` 显示命令列表
- [ ] `trading status` 所有组件都是 ✓ OK
- [ ] `trading account` 显示你的余额和持仓
- [ ] `trading sync` 可以预览持仓数据

---

## 📝 常见问题

### ❌ "ModuleNotFoundError: No module named 'trading'"

**解决方案：**
```bash
# 确保在项目目录内
cd 体系化交易/_projects/trading-bot

# 重新安装
pip install -e .
```

### ❌ "Binance API 连接失败"

**检查清单：**
1. `.env` 中的 API 密钥是否正确
2. Binance 账户是否启用了 API
3. API 密钥是否有 "Trading" 权限
4. 网络连接是否正常

### ❌ "Vault path does not exist"

**解决方案：**
在 `config.yaml` 中检查 Vault 路径是否正确：
```yaml
vault:
  path: "/Users/shuchang/Documents/Obsidian Vault"  # 修改为你的实际路径
```

### ❌ "Anthropic API key not set"

在 `.env` 中添加：
```
ANTHROPIC_API_KEY=sk-ant-...
```

---

## 💡 下一步

1. **了解命令**：阅读 [README.md](README.md) 的"Phase 1 命令详解"部分
2. **配置风控**：编辑 `config.yaml` 中的风控参数
3. **设置 Vault**：确保 Vault 中有必要的文件：
   - `合约交易体系-完整指南.md`
   - `【持仓管理】/` 目录
   - `【报告】/` 目录
4. **运行测试**：
   ```bash
   python -m pytest tests/ -v
   ```

---

## 🎯 Phase 1 三大命令

### 1️⃣ `trading account` — 账户总览
```bash
trading account
```
显示：
- 现货余额
- 合约余额
- 总浮亏浮盈百分比
- 所有开仓持仓（含杠杆、P&L）

### 2️⃣ `trading sync` — 同步持仓
```bash
trading sync              # 预览（安全）
trading sync --no-dry-run # 真实写入（确认后）
```
功能：
- 从 Binance 拉取最新持仓
- 格式化为 Markdown 表格
- 写入 Vault `【持仓管理】/仓位追踪-*.md`

### 3️⃣ `trading status` — 系统检查
```bash
trading status
```
检查：
- Vault 是否就绪
- Binance API 是否连接
- Claude API 是否配置

---

## 🔒 安全提示

⚠️ **重要事项：**
1. **不要提交 `.env`** — 使用 `.gitignore` 保护敏感信息
2. **默认 dry_run 模式** — 所有命令默认安全，不会实际交易
3. **验证每次操作** — `trading sync` 会显示预览，请仔细检查
4. **定期备份** — Vault 包含重要的持仓数据

---

## 📊 项目状态

| 阶段 | 状态 | 进度 |
|------|------|------|
| Phase 1 | ✅ 完成 | 100% |
| Phase 2 | 🔄 规划中 | 0% |
| Phase 3 | 📋 待开始 | 0% |
| Phase 4 | 📋 待开始 | 0% |

---

**需要帮助？** 查看 README.md 或运行 `trading --help`

**最后更新**: 2024-12-15
