# research-coin · 币种调研报告

<!-- 描述 -->
description: 调用 Claude AI，按照 Vault 中的调研报告规范，为指定币种生成结构化调研报告并写入 Vault【报告】

<!-- 触发关键词 -->
触发关键词：调研币种、调研 xxx、研究 xxx、生成调研报告、调研报告

> **当前阶段：Phase 2（未实现）**
> 步骤 5（调用 Claude 生成报告）依赖 `ai/client.py`，目前该模块为空。
> 当前可手动执行步骤 1-4 收集上下文，再将内容交给 Claude 手动生成报告。
> 完整自动化需先完成 [start-phase2](../start-phase2/SKILL.md)。

## 前置条件

- 用户指定目标币种（如 BTC、CHZ、ASTER）
- Anthropic API key 已配置（Phase 2）
- Vault 中存在 `币种调研报告规范(v2.0-通用版).md`（作为输出格式模板）

## 执行步骤

1. **确认目标币种**
   - 确认交易对格式（如用户说"CHZ"，对应 Binance 的 `CHZUSDT`）
   - 询问：是针对当前持仓做调研，还是研究未持仓的新标的？

2. **读取调研规范**
   调用 `VaultReader` 读取 `币种调研报告规范(v2.0-通用版).md`，作为报告结构模板。

3. **读取已有信息**（若存在）
   - 调用 `VaultReader.read_coin_report(symbol)` 读取历史调研报告
   - 调用 `VaultReader.read_position_decision(symbol)` 读取历史持仓决策
   - 作为 Claude 的参考背景，避免重复分析

4. **读取当前持仓状态**（若该币种有仓位）
   - 调用 `PositionManager.get_position(symbol)` 获取实时持仓数据
   - 将持仓信息（开仓价、杠杆、未实现 P&L）加入分析上下文

5. **调用 Claude 生成报告**（Phase 2）
   - 按照调研规范结构生成报告
   - 启用 Prompt Caching（调研规范文档作为缓存层）
   - 报告须覆盖：项目基本面 / 技术面分析 / 风险提示 / 操作建议

6. **显示报告预览**
   - 终端输出完整报告内容
   - 询问用户：是否写入 Vault？

7. **写入 Vault**（用户确认后）
   ```python
   VaultReader.write_report(
       filename=f"【报告】{symbol}调研-{date}.md",
       content=report_content
   )
   ```

## 输出物

- 终端：报告预览
- Vault：`体系化交易/【报告】/【报告】{symbol}调研-{datetime}.md`

## 注意事项

- 报告格式必须遵循 `币种调研报告规范(v2.0-通用版).md`，不得自行发明结构
- Phase 2 实现前，可手动将调研规范 + 问题交给 Claude 完成人工调研
- 报告是信息整理，不包含下单指令
- 交叉引用：[analyze-positions](../analyze-positions/SKILL.md)（调研报告写入后可触发持仓分析）
