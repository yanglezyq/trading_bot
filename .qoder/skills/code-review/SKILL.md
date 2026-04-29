# code-review · 交易安全审查

<!-- 描述 -->
description: 从交易安全视角审查代码变更，重点检查 dry_run 保护、风控参数引用、API 错误处理

<!-- 触发关键词 -->
触发关键词：代码审查、review、检查代码、安全检查

## 前置条件

- 用户指定审查范围（文件列表 / 模块名 / git diff）
- 已读 `.qoder/rules/code-review-checklist.md`

## 执行步骤

1. **确认审查范围**
   - 未指定时，默认审查当前未提交的变更：`git diff --name-only HEAD`

2. **逐文件扫描，按 P0/P1/P2 分级输出**
   每条格式：`文件:行号 | 问题 | 风险 | 修复建议`

3. **询问是否修复 P0**（默认执行）

4. **修复后运行测试验证**
   ```bash
   python -m pytest tests/ -v
   ```

5. **输出审查结论**
   ```
   P0（交易安全必改）：X 项，已修复 Y 项
   P1（建议修改）：Z 项
   测试：通过 / 失败
   ```

## 交易系统专项检查点

除通用代码规范外，重点检查以下交易特有风险：

### 必须修复（P0）

| 检查项 | 示例问题 |
|--------|---------|
| 写单操作无 dry_run 保护 | `futures_client.new_order()` 未被 `if dry_run: return` 保护 |
| 风控阈值硬编码 | 代码中出现 `-0.20`、`35` 等应从 RiskConfig 读取的数字 |
| Vault 路径硬编码 | 代码中出现 `/Users/xxx/Obsidian Vault/` 等绝对路径，应从 VaultConfig 读取 |
| API 异常未传播 | `except Exception: pass` 导致 Binance API 失败无声消失 |
| dry_run 层级混乱 | 方法内既用 `client.dry_run` 又用参数 `dry_run`，优先级未明确 |
| 真实下单前未 dry_run 预览 | CLI 命令未经 dry_run 确认直接调用写单接口 |

### 建议修改（P1）

| 检查项 | 示例问题 |
|--------|---------|
| 日志缺失 | 下单、Vault 写入等关键操作未调用 TradingLogger |
| None 未判断 | `get_futures_balance()` 返回 `Optional[float]` 但直接用于计算 |
| 返回类型不一致 | 部分路径返回 dict，部分路径返回 None，应统一 |

## 注意事项

- 交易系统代码审查的核心优先级：资金安全 > 功能正确 > 代码质量
- 交叉引用：`.qoder/rules/code-review-checklist.md`（完整审查条目）
- 交叉引用：[add-feature](../add-feature/SKILL.md)（开发完成后触发审查）
