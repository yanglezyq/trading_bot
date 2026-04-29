# sync-vault · 持仓同步到 Vault

<!-- 描述 -->
description: 从 Binance 拉取最新持仓，生成 Markdown 格式预览，用户确认后写入 Obsidian Vault【持仓管理】

<!-- 触发关键词 -->
触发关键词：同步持仓、sync、更新 Vault、写入持仓、同步到 Vault

## 前置条件

- Binance API key 已配置
- Vault 路径在 config.yaml 中正确配置，且 `体系化交易/` 目录存在

## 执行步骤

1. **预览模式拉取持仓**
   ```bash
   trading sync   # 默认 dry_run=True，只预览不写入
   ```
   输出内容包含：
   - 合约持仓表格（交易对、数量、开仓价、杠杆、未实现 P&L、P&L%）
   - 现货持仓表格（资产、数量）
   - 同步时间戳

2. **确认写入路径**
   告知用户：将写入 `{vault_path}/体系化交易/【持仓管理】/仓位追踪-{datetime}.md`

3. **询问是否写入**
   - 若用户确认 → 执行 `trading sync --no-dry-run`
   - 若用户拒绝 → 仅保留预览，不写入

4. **写入成功后确认**
   - 输出写入的文件完整路径
   - 记录日志：`TradingLogger.info("持仓同步完成", extra={...})`

5. **可选：触发持仓分析**
   询问用户：持仓已同步，是否立即进行 AI 分析？
   → 若是，触发 [analyze-positions](../analyze-positions/SKILL.md)

## 输出物

- 终端：持仓预览表格
- Vault（确认后）：`体系化交易/【持仓管理】/仓位追踪-{datetime}.md`

## 注意事项

- 默认 dry_run=True，禁止在未得到用户确认的情况下直接执行 `--no-dry-run`
- 每次同步生成新文件（带时间戳），不覆盖历史记录
- 若 Vault 路径不存在，先报错提示配置问题，不自动创建 Vault 根目录
- 交叉引用：[analyze-positions](../analyze-positions/SKILL.md)（同步后可触发 AI 分析）
