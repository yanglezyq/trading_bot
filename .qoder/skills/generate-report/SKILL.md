# generate-report · 生成交易报告

<!-- 描述 -->
description: 汇总账户余额、持仓状态、P&L、风控指标，生成结构化交易报告并写入 Vault【报告】

<!-- 触发关键词 -->
触发关键词：生成报告、日报、周报、交易总结、生成持仓报告

## 前置条件

- Binance API key 已配置
- 确认报告类型：日报（当日快照）/ 周报（近 7 日汇总）/ 临时报告（当前状态）

## 执行步骤

1. **确认报告类型和时间范围**
   - 日报：当前账户快照 + 当日已实现 P&L
   - 周报：读取 `data/trades.db` 中近 7 日日志汇总
   - 临时报告：当前持仓快照

2. **采集数据**
   ```python
   # 账户数据
   AccountManager.get_account_summary()
   # → futures_balance, spot_balance, total_balance, unrealized_pnl, pnl_pct, drawdown_pct

   # 持仓数据
   PositionManager.get_positions_summary()
   PositionManager.get_futures_positions()
   # → futures_count, long_count, short_count, total_notional, 各仓位详情

   # 风控状态（复用 check-risk 逻辑）
   # → L1/L2/L3 当前级别
   ```

3. **生成报告 Markdown**
   报告结构：
   ```markdown
   # 交易报告 - {日期}

   ## 账户概览
   | 指标 | 数值 |
   | 合约余额 | $X |
   | 现货余额 | $X |
   | 总余额 | $X |
   | 未实现 P&L | $X (X%) |
   | 当前回撤 | X% |
   | 风控等级 | 正常 / L1 / L2 / L3 |

   ## 持仓详情
   | 交易对 | 方向 | 数量 | 开仓价 | 杠杆 | 未实现 P&L | P&L% |

   ## 风控状态
   各项风控指标 vs 阈值对比

   ## 备注
   （用户手动补充）
   ```

4. **显示报告预览**

5. **询问是否写入 Vault**
   写入路径：`体系化交易/【报告】/【报告】交易日报-{date}.md`

6. **记录日志**

## 输出物

- 终端：报告预览
- Vault（确认后）：`体系化交易/【报告】/【报告】{报告类型}-{datetime}.md`

## 注意事项

- 报告只做数据汇总，不生成操作指令
- 周报依赖 `data/trades.db` 日志，若 SQLite 无数据则降级为当前快照报告
- 交叉引用：[check-risk](../check-risk/SKILL.md)（风控评估结果嵌入报告）
- 交叉引用：[sync-vault](../sync-vault/SKILL.md)（建议报告前先同步最新持仓）
