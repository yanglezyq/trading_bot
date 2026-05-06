<!-- Qoder Rule Type: Always Apply -->
<!-- 适用范围: 所有请求 -->

# Agent 行为规则

## 行为边界速查表

| 类别 | 可自由执行 | 必须询问用户 | 禁止 |
|------|-----------|------------|------|
| 代码修改 | 编辑 src/、tests/、config/、docs/ | — | — |
| 测试运行 | pytest tests/ -v（dry_run 模式） | 指定真实 Binance 环境 | 对真实账户执行写操作 |
| 文档代码冲突 | — | 告知冲突点并等待裁决 | 自行选边修改 |
| Git 操作 | status/diff/log/show | commit 需确认 message | 自动 commit/push/--force |
| 批量操作 | — | 告知影响范围后确认 | 用全局命令替代精准操作 |
| .env 文件 | 只读（提示配置内容） | — | 写入或覆盖 .env |
| 真实下单 | — | — | 永远不自动 place_market_order/place_limit_order 等写单操作 |

## P0 硬性规则

### P0.1 · 文档与代码不一致时必须询问
发现 rules/ 或 docs/ 的约定与实际代码冲突时：
1. 禁止自行选边修改，禁止无声纠偏
2. 向用户说明：冲突点 / 文档侧说法 / 代码侧实际 / 两个修复方案
3. 由用户决定以哪一方为准，然后同步更新另一方

### P0.2 · 运行前必须确认环境
用户请求运行测试或命令但未明确指定环境时，必须先确认：
- 是使用 dry_run 模式（默认安全）还是真实 Binance API？
- 如涉及 Vault 写操作：是预览还是真实写入？

### P0.3 · Git 提交由用户执行
代码修改完成后，输出 commit message 建议，但不自动执行 git commit / git push。

### P0.4 · 真实下单前必须展示完整信息并等待用户明确确认

任何涉及 `place_market_order`、`place_limit_order`、`place_stop_market_order`、
`place_take_profit_market_order`、`cancel_order` 的真实调用（非 dry_run），
执行前必须向用户展示以下信息，等待明确的「确认执行」指令，不能批量自动执行：

```
━━━ 下单确认 ━━━
交易对:    XXXUSDT
方向:      多/空
数量:      X
价格:      $X（市价 / 限价 $X）
杠杆:      Xx
预估保证金: $X
止损价:    $X（-X%）
止盈价:    $X（+X%）

输入「确认」执行 | 输入其他取消
```

**禁止**在未收到用户明确「确认」文字前调用任何真实下单 API。

---

### P0.5 · 每笔真实下单必须同时附带止损止盈单

开仓订单（`place_market_order` / `place_limit_order`）成交后，必须立即：
1. 调用 `place_stop_market_order()` 挂止损单
2. 调用 `place_take_profit_market_order()` 挂止盈单
3. 向用户确认三笔订单均已提交，并显示订单 ID

**禁止**只开仓不挂止损止盈单。若止损/止盈单提交失败，必须立即告警并询问用户是否手动平仓。

止损止盈参数来源（优先级从高到低）：
- 用户在本次对话中明确指定的价位
- `config.yaml` 中 `trading.default_stop_loss_pct` / `trading.default_take_profit_pct`
- 禁止自行估算或省略

---

### P0.6 · 每笔实际成交的交易必须写入交易日志

每次真实下单成交后，必须将以下字段写入统一交易记录（实现完成前以 Vault Markdown 代替）：

| 字段 | 说明 |
|-----|------|
| 交易对 / 方向 | symbol, LONG/SHORT |
| 开仓时间 / 价格 / 数量 / 杠杆 | 实际成交数据 |
| 止损价 / 止盈价 | 挂单价位 |
| 平仓时间 / 价格 / 已实现 P&L | 平仓后补填 |
| 经验备注 | 用户填写，必须字段 |

**禁止**在未写入交易记录的情况下认为"一笔交易已完成"。

> 当前状态：TradeJournal 模块待实现，临时写入  
> `Vault/体系化交易/【交易记录】/交易日志-{year}.md`

---

## 禁止事项清单

- 禁止自动提交 Git
- 禁止向 Binance 真实账户发单（dry_run=False 的写操作）
- 禁止修改 .env 文件内容
- 禁止扩大操作范围（用户说"修改 A"，不顺手改 B）
- 禁止在未告知风险的情况下执行 --no-dry-run 的 trading sync
- 禁止跳过 dry_run 前置确认直接进入真实交易流程
- 禁止只开仓不挂止损止盈（P0.5）
- 禁止成交后不写交易记录（P0.6）
