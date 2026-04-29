# start-phase2 · 启动 Claude AI 分析引擎

<!-- 描述 -->
description: 实现 ai/ 层核心模块（ClaudeClient + Prompt Caching + 三类 Prompt），打通从 Binance 数据到 Claude 分析再到 Vault 报告的完整链路

<!-- 触发关键词 -->
触发关键词：启动 Phase 2、实现 AI 分析、接入 Claude、实现 ai 模块

## 前置条件

- Phase 1 所有 CLI 命令（account / sync / status）运行正常
- `.env` 中已配置 `ANTHROPIC_API_KEY`
- Vault 中存在 `合约交易体系-完整指南.md` 和 `决策建议生成规范.md`

## 执行步骤

### Step 1 · 实现 `src/trading/ai/client.py`

创建 `ClaudeClient`，封装：
- 基础 Claude API 调用（`anthropic.Anthropic().messages.create()`）
- **Prompt Caching**：将长文档（交易体系、调研规范）设置为 `cache_control: {"type": "ephemeral"}`，5 分钟内重复调用命中缓存
- 模型从 `ClaudeConfig.model`（`claude-sonnet-4-6`）读取，不硬编码
- `max_tokens` 从 `ClaudeConfig.max_tokens` 读取

```python
# 示例接口
class ClaudeClient:
    def __init__(self, config: ClaudeConfig): ...
    def analyze(self, system_prompt: str, user_message: str) -> str:
        """调用 Claude，system_prompt 启用 Prompt Caching。"""
```

### Step 2 · 实现 `src/trading/ai/prompts/` 三类 Prompt 函数

| 文件 | 函数 | 用途 |
|------|------|------|
| `position_analysis.py` | `build_position_analysis_prompt(positions, vault_context)` | 持仓分析 |
| `coin_research.py` | `build_coin_research_prompt(symbol, spec, history)` | 币种调研 |
| `decision.py` | `build_decision_prompt(positions, analysis, spec)` | 决策建议 |

每个函数返回 `(system_prompt: str, user_message: str)`，system_prompt 包含需要缓存的长文档。

### Step 3 · 更新 `src/trading/ai/__init__.py`

```python
from .client import ClaudeClient
from .prompts.position_analysis import build_position_analysis_prompt
from .prompts.coin_research import build_coin_research_prompt
from .prompts.decision import build_decision_prompt

__all__ = ["ClaudeClient", "build_position_analysis_prompt", "build_coin_research_prompt", "build_decision_prompt"]
```

### Step 4 · 为 `ClaudeClient` 添加测试

在 `tests/test_ai.py` 中：
- 验证 `ClaudeClient` 初始化（需 `ANTHROPIC_API_KEY`，可用 pytest skip 跳过无 key 环境）
- 验证 Prompt 构建函数的输出结构（不调用真实 API）

### Step 5 · 在 `main.py` 新增 `analyze` 和 `research` 命令

```bash
trading analyze          # 分析当前持仓
trading research CHZ     # 调研 CHZ
```

命令参数风格与现有 `account`/`sync` 保持一致（`--config`、`--dry-run`）。

### Step 6 · 同步文档

- `docs/modules-catalog.md`：ai/ 层更新为「✅ Phase 2 已实现」
- `docs/setup.md`：新增 `trading analyze` 和 `trading research` 命令说明
- `.qoder/rules/project-architecture.md`：Phase 2 标记从「🔄」改为「✅」
- `routing/SKILL.md`：analyze-positions 和 research-coin 的「当前可执行」改为「✅」

## 输出物

- `src/trading/ai/client.py`（ClaudeClient）
- `src/trading/ai/prompts/position_analysis.py`
- `src/trading/ai/prompts/coin_research.py`
- `src/trading/ai/prompts/decision.py`
- `src/trading/ai/__init__.py`（更新导出）
- `tests/test_ai.py`
- 更新的 `docs/` 和 `.qoder/rules/` 文件

## 注意事项

- Prompt Caching 是核心：每次分析要先传入交易体系文档作为 cached system prompt，再传入实时数据作为 user message
- `ClaudeConfig.cache_ttl_seconds = 300`（5 分钟），频繁调用时充分利用缓存节省 token
- Claude 输出只写报告，不生成下单指令
- 交叉引用：[analyze-positions](../analyze-positions/SKILL.md)（Phase 2 完成后完整执行）
- 交叉引用：[research-coin](../research-coin/SKILL.md)（Phase 2 完成后完整执行）
