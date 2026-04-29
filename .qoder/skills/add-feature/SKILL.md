# add-feature · 新增交易功能

<!-- 描述 -->
description: 在已有交易模块上增量添加功能，确保 dry_run 安全保护和风控参数正确引用

<!-- 触发关键词 -->
触发关键词：新增功能、给…加上、扩展、增加方法、在…里加

## 前置条件

- 用户说明：目标模块 + 新功能描述
- 已阅读目标模块源码，理解现有 dry_run 模式和接口风格

## 执行步骤

1. **阅读目标模块**
   确认：
   - 现有方法的 dry_run 处理方式（`if self.client.dry_run: return mock_data`）
   - 返回类型约定（dataclass / dict / None）
   - 错误处理风格（`raise RuntimeError(f"描述: {e}")`）

2. **检查文档一致性（P0.1）**
   对比 `docs/modules-catalog.md` 中该模块的描述与当前代码，发现不一致时暂停并告知用户。

3. **实现新功能**
   - 有写操作（下单/Vault写入）→ 必须支持 `dry_run: Optional[bool] = None`
   - 风控参数（阈值、比例）→ 从 `RiskConfig` 读取，不硬编码数字
   - 新数据类型 → 用 `@dataclass`，加 `@property` 计算字段

4. **更新 `__init__.py`**（若有新导出的类或函数）

5. **为新功能添加 dry_run 测试**
   在已有 `tests/test_{模块名}.py` 中追加：
   - dry_run=True 路径：验证返回 mock 数据，且不调用真实 API
   - 注意：不使用 `pytest-mock` 模拟 Binance 响应——用 dry_run=True 代替 mock，避免 mock 与真实 API 行为偏差（见 coding-conventions.md）

6. **同步更新文档**
   - `docs/modules-catalog.md`：更新对应模块的主要导出和责任描述
   - 若影响 CLI 命令：更新 `docs/setup.md`

7. **运行测试验证**
   ```bash
   python -m pytest tests/test_{模块名}.py -v
   ```

8. **输出 commit message 建议**（不自动执行 git commit）

## 新功能分类参考

| 新功能类型 | 典型所属模块 | 关键注意点 |
|-----------|------------|-----------|
| 新订单类型（如追踪止损） | exchange.orders | dry_run 保护必须完整 |
| 新账户查询（如历史成交） | exchange.account | 返回空列表或 None 表示无数据 |
| 新 Vault 读取规则 | core.vault | 文件不存在时返回 None，不抛异常 |
| 新风控指标 | risk.monitor（Phase 3） | 阈值必须来自 RiskConfig |
| 新 CLI 命令 | main.py | 保持 --dry-run / --config 参数风格一致 |
| 新 AI Prompt | ai.prompts（Phase 2） | 启用 Prompt Caching |

## 注意事项

- 不改已有方法签名（破坏兼容性）
- 不引入新第三方依赖（先在 pyproject.toml 确认）
- 交叉引用：[code-review](../code-review/SKILL.md)（完成后做交易安全视角审查）
