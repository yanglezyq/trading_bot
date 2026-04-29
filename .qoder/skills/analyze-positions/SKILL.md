# analyze-positions · 持仓 AI 分析

<!-- 描述 -->
description: 读取 Binance 当前持仓 + Vault 交易体系知识，调用 Claude 生成持仓分析建议，写回 Vault【报告】

<!-- 触发关键词 -->
触发关键词：分析持仓、AI 分析、持仓建议、分析一下

> **当前阶段：Phase 2（未实现）**
> 步骤 3（调用 Claude）依赖 `ai/client.py`，目前该模块为空。
> 当前可手动执行步骤 1-2 收集数据，再将数据粘贴给 Claude 完成分析。
> 完整自动化需先完成 [start-phase2](../start-phase2/SKILL.md)。

## 前置条件

- Binance API key 已在 .env 配置（读取持仓用）
- Anthropic API key 已在 .env 配置（Phase 2 需要）
- Vault 中存在 `合约交易体系-完整指南.md`（作为 AI 系统提示词来源）
- 当前有开仓的合约持仓

## 执行步骤

1. **拉取实时持仓**
   ```bash
   trading account   # 不加 --dry-run，否则只返回 mock 数据而非真实持仓
   ```
   内部调用 `PositionManager.get_futures_positions()`（dry_run=False）获取所有开仓持仓。

2. **构建知识上下文**
   调用 `VaultReader.build_knowledge_context(symbols)` 读取：
   - `合约交易体系-完整指南.md`（核心规则）
   - `决策建议生成规范.md`（输出格式规范）
   - 各持仓币种的历史调研报告（若存在）
   - 最近的仓位追踪文件

3. **调用 Claude 分析**（Phase 2）
   - 将交易体系 + 实时持仓组装为 Prompt
   - 启用 Prompt Caching（交易体系文档作为缓存层）
   - 要求 Claude 针对每个持仓给出：当前状态判断 / 建议操作 / 风险提示

4. **显示分析结果预览**
   - 在终端输出 Claude 的分析内容
   - 询问用户：是否写入 Vault？

5. **写入 Vault**（用户确认后）
   ```python
   VaultReader.write_report(
       filename=f"【报告】持仓分析-{date}.md",
       content=analysis_content
   )
   ```

6. **记录日志**
   ```python
   TradingLogger.info("持仓分析完成", extra={"symbols": [...], "report_path": ...})
   ```

## 输出物

- 终端：Claude 分析摘要（含每个持仓的建议）
- Vault：`体系化交易/【报告】/【报告】持仓分析-{datetime}.md`

## 注意事项

- Phase 2 实现前，可手动读取 Vault 内容后复制给 Claude 完成人工分析
- Claude 的建议仅供参考，实际操作必须经用户确认
- 禁止在此 Skill 中自动下单，分析结论只写报告，不触发 OrderManager
- 交叉引用：[research-coin](../research-coin/SKILL.md)（调研报告可丰富分析上下文）
- 交叉引用：[generate-report](../generate-report/SKILL.md)（分析结果可格式化为完整报告）
