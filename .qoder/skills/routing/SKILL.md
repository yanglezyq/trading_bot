# 中央路由

<!-- 描述 -->
description: 根据用户意图关键词，将任务分发到对应的交易 Skill

## 全局共享约定

所有 Skill 共享以下规则：
- **dry_run 优先**：任何涉及 Binance 写操作的 Skill，默认以 dry_run=True 预览，用户确认后再执行
- **Vault 写入需确认**：写入 Obsidian Vault 前显示预览内容，等待确认
- **风控参数只读 config**：禁止在 Skill 执行过程中硬编码风控阈值，只从 config.yaml 的 RiskConfig 读取
- **文档同步**：代码变更类 Skill 完成后同步更新 docs/ 对应文件
- **真实下单三步缺一不可**（见 agent-behavior.md P0.4/P0.5/P0.6）：
  1. 展示完整下单信息 → 等待用户明确「确认」
  2. 开仓后立即挂止损止盈单，缺单则立即告警
  3. 成交后写入交易记录，未记录前不视为完成

## 路由表

| 用户意图关键词 | 分发目标 Skill | 当前可执行 | 说明 |
|--------------|--------------|-----------|------|
| 检查风控、熔断、风险检查 | check-risk | ✅ Phase 1 | 对照 L1/L2/L3 风控参数评估账户状态 |
| 同步持仓、sync、更新 Vault | sync-vault | ✅ Phase 1 | 从 Binance 拉取持仓并同步到 Vault |
| 生成报告、日报、周报 | generate-report | ✅ Phase 1 | 生成持仓汇总与 P&L 报告 |
| 分析持仓、AI 分析、持仓建议 | analyze-positions | 🔄 Phase 2 | Claude AI 分析当前持仓，写报告到 Vault |
| 调研币种、研究 xxx、调研报告 | research-coin | 🔄 Phase 2 | 生成指定币种的调研报告 |
| 启动 Phase 2、实现 AI 分析 | start-phase2 | 🔄 Phase 2 | 实现 ai/ 层：ClaudeClient + Prompt Caching |
| 新增功能、扩展代码、给…加上 | add-feature | ✅ 开发 | 在已有模块增量添加交易功能 |
| 代码审查、review、检查代码 | code-review | ✅ 开发 | 交易安全视角的代码审查 |

## Skill 间协作

```
sync-vault       → analyze-positions（同步后可立即触发 AI 分析）
check-risk       → generate-report（风控报警时生成告警报告）
analyze-positions → generate-report（分析结果格式化为报告）
research-coin    → analyze-positions（调研结果作为分析上下文）
```

## 冲突处理

多个 Skill 同时匹配时：
1. 用户明确说"用 xxx Skill" → 直接执行
2. 同时涉及"同步"和"分析" → 先 sync-vault，再 analyze-positions
3. 同时涉及"风控"和"报告" → 先 check-risk，再 generate-report
