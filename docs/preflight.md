# 真实环境预演 Checklist

这份清单用于把系统从“代码可运行”推进到“真实账户前可验证”。

## 1. 配置层

- `.env` 已配置：
  - `BINANCE_API_KEY`
  - `BINANCE_API_SECRET`
  - `ANTHROPIC_API_KEY`
- `config.yaml` 已检查：
  - `vault.path`
  - `risk`
  - `trading`
  - `events`
  - `rebalance`
  - `notification`

## 2. 安全边界

- 你已明确知道：
  - `trading trade` 不自动开新仓
  - `trading rebalance --no-dry-run` 可能真实调仓
  - `batch-trade` 在 `rebalance.auto_after_batch=true` 时，可能触发真实调仓
- 如果你要纯人工执行模式：
  - 将 `rebalance.enabled: false`
  - 或所有相关命令都使用 `--dry-run`

## 3. 最小 smoke 顺序

```bash
trading status
trading account --dry-run
trading sync --dry-run
trading trade BTCUSDT --dry-run
trading batch-trade BTCUSDT ETHUSDT --dry-run
```

## 4. 人工检查点

每次 `trading trade` 至少看这几块：

- `Market Snapshot`
  - `BTC Regime`
  - `Asset Tier`
  - `Narrative`
  - `Crowding`
  - `Execution Template`
- `ResearchDecision`
  - `Thesis`
  - `Evidence`
  - `Risks`
  - `Invalidation`
- `RiskDecision`
  - `Warnings`
  - `Adjusted Size`
  - `Adjusted Leverage`
- `Manual Order Ticket`
  - `preferred_order_type`
  - `staging_plan`
  - `confirmation_checklist`
  - `cancel_if`
  - `operator_steps`
  - `review_after_hours`

## 5. 上线前建议

- 先只做 `core` 币种：
  - `BTCUSDT`
  - `ETHUSDT`
- 再做 `major_alt`
- 最后才考虑 `high_beta_alt / meme`

## 6. 什么时候不要下单

- `BTC Regime = panic_flush`
- `Crowding = crowded_long / crowded_short` 且方向一致
- `RiskDecision` 明确出现 concentration / meme / high_beta 警告
- `Manual Order Ticket.entry_validity = outside_entry_zone`

## 7. 回看与复盘

每次真实执行后，至少做两件事：

1. 用 `trading record` 录入平仓交易
2. 用 `trading reflect --symbol SYMBOL` 生成反思

这样后续 AI 才能利用历史交易和反思持续改进。*** End Patch
