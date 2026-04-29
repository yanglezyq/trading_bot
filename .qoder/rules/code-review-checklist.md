<!-- Qoder Rule Type: Apply Manually -->
<!-- 触发方式: 用户请求代码审查时 -->

# 代码审查清单（交易系统视角）

> 核心优先级：资金安全 > 功能正确 > 代码质量

## P0 · 必改（影响资金安全或导致静默失败）

| 检查项 | 说明 |
|--------|------|
| 写单操作无 dry_run 保护 | `new_order`、`cancel_order` 等未被 `if dry_run: return None` 保护 |
| dry_run 覆盖不完整 | dry_run=True 路径仍发出真实 HTTP 请求 |
| 风控阈值硬编码 | 回撤比例、杠杆上限等数字硬写代码，未从 `RiskConfig` 读取 |
| Vault 路径硬编码 | 绝对路径字符串硬写代码，未从 `VaultConfig` 读取 |
| API 异常被吞没 | `except Exception: pass` 导致 Binance/Claude API 失败无声消失 |
| None 链式调用 | `get_futures_balance()` 等返回 `Optional[X]` 的结果未判空直接使用 |
| 测试断言无效 | `assert True` 或永为真的断言，dry_run 路径未被测试覆盖 |
| 新导出未更新 `__init__.py` | 新类/函数外部无法导入 |

## P1 · 建议修改（影响可维护性和可观测性）

| 检查项 | 说明 |
|--------|------|
| 关键操作无日志 | 下单、Vault 写入、风控触发等未调用 `TradingLogger` |
| dry_run 优先级不明确 | 方法内同时存在 `client.dry_run` 和参数 `dry_run`，未说明哪个优先 |
| 返回类型不一致 | 正常路径返回 dataclass，错误路径返回 `{"error": ...}` dict，应统一 |
| 缺少类型注解 | 公共方法参数或返回值无类型（mypy strict 会报错） |
| 导入顺序混乱 | 标准库 → 第三方（binance/anthropic）→ 本项目 顺序未遵守 |

## P2 · 可选优化

| 检查项 | 说明 |
|--------|------|
| 注释只说 WHAT | 复述代码功能的注释可删掉，只保留说明 WHY 的注释 |
| 过长方法 | 超过 60 行的方法考虑拆分（不含 dry_run 分支重复结构） |

## 审查工作流

```
1. 确认范围  →  git diff 或用户指定文件
2. P0 逐项扫描  →  文件:行号 + 问题 + 风险 + 修复建议
3. P1/P2 汇总输出
4. 询问是否修复 P0（默认执行）、P1（询问确认）
5. 修复后运行 pytest 验证
6. 输出最终结论
```
