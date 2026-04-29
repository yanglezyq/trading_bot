# check-risk · 风控状态检查

<!-- 描述 -->
description: 读取账户实时数据，对照 config.yaml 中的 L1/L2/L3 风控参数评估账户风险状态，给出明确结论

<!-- 触发关键词 -->
触发关键词：检查风控、风险检查、熔断检查、看一下风险、BTC 黑天鹅

## 前置条件

- Binance API key 已配置
- config.yaml 中 risk 参数已确认（alert_drawdown / max_account_drawdown / suspend_drawdown 等）

## 执行步骤

1. **拉取账户数据**
   ```python
   AccountManager.get_account_summary()
   # 获取：total_balance_usdt, unrealized_pnl, pnl_pct, drawdown_pct
   ```

2. **拉取持仓数据**
   ```python
   PositionManager.get_futures_positions()
   # 获取所有开仓持仓的杠杆、名义价值、未实现 P&L
   ```

3. **对照风控参数逐项评估**

   | 检查项 | 参数来源 | 当前值 | 结论 |
   |--------|---------|--------|------|
   | 账户回撤 | `risk.alert_drawdown`（-10%）<br>`risk.max_account_drawdown`（-20%）<br>`risk.suspend_drawdown`（-30%） | `drawdown_pct` | 正常 / L1 告警 / L2 清仓 / L3 暂停 |
   | 最高杠杆 | `risk.max_leverage`（35x） | 各仓位 leverage | 是否超限 |
   | 单仓名义值占比 | `trading.max_position_size_pct`（5%） | notional / total_balance | 是否超限 |
   | 利润锁定触发 | `risk.profit_lock_threshold`（+50%）<br>`risk.profit_lock_ratio`（50%） | 各仓位 unrealized_pnl_pct | 浮盈超 50% 时提示锁定 50% 仓位 |

4. **BTC 黑天鹅检测**（若用户提及或 BTC 剧烈波动）
   - 当前不自动拉取 BTC 24h 涨跌幅（WebSocket Phase 3 功能）
   - 提示用户手动输入 BTC 当前 24h 涨跌幅，与 `risk.btc_crash_threshold`（-10%）和 `risk.btc_black_swan`（-15%）对比

5. **输出风控结论**
   ```
   ━━━ 风控状态报告 ━━━
   账户回撤：-8.5%  → [正常] (L1 阈值 -10%)
   最高杠杆：30x    → [正常] (上限 35x)
   超仓检查：CHZUSDT 名义值占比 6.2% → [⚠️ 超限] (上限 5%)

   综合结论：[黄色告警] 建议减少 CHZUSDT 仓位
   ```

6. **若触发 L2（回撤 ≥ 20%）**
   - 明确告警：账户已触发 L2 熔断条件，建议立即全部平仓
   - 禁止自动执行平仓，输出需要执行的命令清单，等待用户手动确认后逐笔操作

## 输出物

- 终端：结构化风控评估报告，含每项指标的当前值 vs 阈值对比
- 若存在告警：明确的操作建议（不自动执行）

## 注意事项

- 此 Skill 只读账户数据，不发送任何订单
- L2 触发时必须明确告知用户，但由用户决定是否执行平仓
- 风控阈值从 `config.yaml` 读取，不得在代码或 Skill 中硬编码数字
- 交叉引用：[generate-report](../generate-report/SKILL.md)（风控告警时生成告警报告）
