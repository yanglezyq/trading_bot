# Trading Bot

面向**区块链 / 币圈投资**的 AI 研究、风控与人工执行系统。

当前系统的核心定位不是“自动化下单机器人”，而是：

1. 读取 Binance 账户、仓位与公开市场数据
2. 结合 Obsidian Vault 中的个人交易体系做 AI 研究
3. 用确定性风控过滤研究结果
4. 输出**手动下单清单（manual order ticket）**
5. 把运行结果、市场快照与反思沉淀到 SQLite / Vault

---

## 当前能力

### 1. 账户与仓位

- Binance Spot + USDT-M Futures 账户读取
- 现货 / 合约余额查询
- 当前持仓与未实现 P&L 查询
- `account --dry-run` mock 预览

### 2. 币圈市场快照

每次 `trading trade` 会生成 `MarketSnapshot`，包括：

- 现货价 / 合约标记价
- 24h / 7d 涨跌
- 24h 成交额
- funding rate
- open interest
- basis
- 24h / 7d realized volatility
- EMA21 / EMA55 / EMA144
- 1h 趋势偏向
- 资产层级（`core / major_alt / liquid_alt / mid_alt / high_beta_alt`）
- 叙事标签（79+ symbol 显式映射 + `general_alt` fallback）
- 流动性状态
- 拥挤度
- BTC 市场状态机
- 相对 BTC 强弱
- execution template
- CoinGecko 补充：market_cap / rank / categories / FDV（10min 缓存，失败静默降级）

### 3. AI 研究与执行计划

默认使用合并单次调用模式（`combined_ai_calls: true`），一次 Claude 调用同时产出 Research + Execution，节省 ~30% token。

可选启用**多模型分歧投票**：

- `claude.ensemble_enabled: true`
- `claude.ensemble_models: [...]`

启用后会先对 `ResearchDecision` 做多模型独立研究，再按 stance 多数票形成共识结果，并把：

- `supporting_model_count`
- `consensus_strength`
- `disagreement_note`

写入研究结果与 CLI 展示。

Prompt 自动注入：
- Portfolio 上下文（gross/net exposure、持仓数、叙事集中度、仓位预算）
- 链上数据（DeFiLlama TVL）
- CoinGecko market_cap/rank
- 事件快照（宏观日历 + Token解锁，含高影响警告）
- 历史自适应提示（最近胜率/连亏/方向表现/高频风险模式/反思 lessons）
- Vault 交易知识（智能截断：按 symbol 相关性优先，不再粗暴 `[:8000]`）

AI 会输出：

- `ResearchDecision`
  - thesis
  - market_structure
  - evidence
  - catalysts / risks
  - invalidation
  - time_horizon
  - preferred_market

- `ExecutionPlan`
  - action
  - size_pct / leverage
  - entry_style
  - entry zone
  - trigger / invalidation price
  - stop / take profit
  - thesis_window_hours

### 4. 币圈专用 RiskGate

当前风控覆盖：

- 仓位上限 / 杠杆上限
- 账户 drawdown 熔断
- 高波动压杠杆 / 压仓
- funding / basis / OI 拥挤度
- BTC risk-on / risk-off / panic flush / rebound / short squeeze 过滤
- alt 相对 BTC 强弱过滤
- `high_beta_alt` 更严格限制
- `meme` 更严格限制
- stable mode setup 质量闸门
- execution template 专属规则
- 同叙事持仓集中警告
- 事件源风控（高影响宏观事件 24h 内压仓 50% + 杠杆上限 10x）

默认启用了一个“优先做高质量机会”的 `stable_mode`：

- 只偏好 `core / major_alt / liquid_alt`
- 默认拦截 `meme`
- 要求更高的 research confidence / consensus
- 要求最低 reward/risk 比
- 同币种亏损后自动进入冷静期
- 只放过更稳定的 BTC regime
- 自动把通过的机会再压成更小仓位、更低杠杆
- 给每笔 setup 计算一个 `quality score / grade`

但它现在也支持 `conviction override`：

- 如果 research confidence / consensus 足够高
- 可以放宽部分 soft gate
- 允许更有把握的高 beta / 非典型 setup 继续通过
- 但账户级硬风控、仓位上限和最终 risk gate 仍然保留

### 5. 组合层（PortfolioManager）

每次 `trading trade` 还会计算：

- 全账户 gross / net exposure
- 各叙事的仓位数量与名义价值
- 针对当前交易对的仓位预算建议（`PortfolioBudget`）：
  - 资产层级 hard cap
  - 叙事集中度惩罚
  - 全账户 exposure 超限压缩
  - BTC risk-off 时的防守规模

结果同时传给 RiskGate 做约束，也写入 Vault 报告。

### 6. 手动下单清单

当前 `trading trade` 默认**不自动开新仓**，而是输出：

- side / quantity / notional / leverage
- entry zone / trigger / invalidation
- stop / take profit
- execution template
- preferred order type
- staging plan
- max slippage
- operator steps
- confirmation checklist
- cancel conditions
- review window

也就是更像”币圈交易执行操作手册”，而不是直接代替你按下单按钮。

CLI 和报告里现在也会直接显示：

- `stable_mode` 风险档位
- `setup quality score`
- `setup quality grade`
- `opportunity score`

### 6.1 自动执行 API 链

现在已经补上了真实 API 的自动执行链，但仍然是**默认关闭**：

- 只有显式 `--auto-execute`，或配置里明确允许自动执行时，才会尝试发单
- 默认仍然优先走 manual review
- 只有通过自动执行专用安全闸门，才会真正调用 Binance API

自动执行专用闸门当前包括：

- symbol 白名单
- market 白名单
- `auto_execute_live_enabled` 真实环境显式放行
- `auto_execute_kill_switch` 全局一键停自动执行
- 最低 confidence / consensus
- 最低 setup quality score
- 最大 warnings 数
- 同币种未完成订单阻塞
- 止损 / 止盈是否齐备
- 当前价格是否仍在建议入场区附近
- 单币种冷却时间
- 最近 24h 自动执行次数上限
- 最近 24h 自动执行累计名义金额上限

如果没有通过，这次运行会落成 `auto_execute_blocked`，并自动降级回手动复核，不会误发单。

如果后续你手工平仓，`trading record` 现在支持：

- `--run-id` 显式关联某次 pipeline 记录
- 不传时默认尝试自动关联最近一次同 `symbol` 的 `trade` 结果

这样后面的风险统计和自适应 prompt 能真正基于已实现结果持续修正。

注意：

- `trading trade` 不会自动开新仓
- 但 `trading rebalance --no-dry-run` 和 `batch-trade` 在 `rebalance.auto_after_batch=true` 时，**仍可能真实执行调仓单**
- 如果你希望整个系统都保持“纯人工执行”，请将 `config.yaml` 中 `rebalance.enabled` 设为 `false`，或始终使用 `--dry-run`

### 7. 实时 WebSocket 风控守护

`trading watch` 命令启动一个持续运行的守护进程：

- 订阅 Binance UM Futures `markPriceUpdate` 流（1s 刷新）
- Rich Live 仪表盘实时显示每个持仓的价格、P&L、资金费率
- 四类告警：invalidation 突破 / 近止损（< 50% 区间）/ 近止盈（< 20% 区间）/ funding 拥挤
- 断线自动重连，Ctrl+C 优雅退出
- 持仓参数自动从 Binance REST + SQLite pipeline 记录中加载

### 8. 链上数据接入（DeFiLlama）

每次 `trading trade` 在 AI 研究前自动拉取链上数据（免费，无需 API key）：

| 币种类型 | 数据 |
|---------|------|
| DeFi/DEX/Lending 协议 | 协议 TVL + 24h/7d 变化 + 部署链 |
| L1 / L2 | 链总 TVL |
| 所有币种 | 全网稳定币总供应（宏观流动性指标） |

数据以 `## On-Chain Data` 段落注入 AI 研究 prompt。网络不通时静默降级，不影响主流程。

### 9. 事件源（宏观日历 + Token解锁）

每次 `trading trade` 自动拉取即将发生的市场事件：

| 数据源 | API | 内容 |
|---------|-----|------|
| 宏观经济日历 | ForexFactory JSON | 本周 CPI/FOMC/NFP 等 High/Medium impact 事件 |
| Token 解锁 | DeFiLlama emission | 30+ 币种的归属/悬崖解锁计划（7 日前瞻） |

效果：
- AI Prompt 注入 `## Upcoming Events` 段落，含警告标记
- RiskGate 自动压仓：High impact 事件 24h 内 → 仓位 -50%、杠杆 ≤10x
- 配置驱动：`config.yaml` 的 `events:` 段控制开关和参数
- 网络失败静默降级，不影响主流程

### 10. 飞书通知推送

通过飞书群机器人 Webhook 推送关键事件到手机/桌面：

| 场景 | 触发条件 | 消息内容 |
|------|---------|----------|
| 风控告警 | RiskDaemon 检测到 invalidation/近止损/近止盈/funding拥挤 | 币种 + 告警类型 + 当前价 + 方向 |
| Pipeline 完成 | `trading trade` 执行完毕 | stance + action + 风控结论 + 关键价位 |
| SL/TP 触发 | 自动下单的止损挂单失败 | CRITICAL 告警 + 人工介入提示 |
| 批量扫描 | `trading batch-trade` 完成 | 所有有信号币种汇总 |

特性：同一告警 5 分钟内自动去重、网络故障静默降级不阻塞主流程。

### 11. 风控效果统计

`trading risk-stats` 不再只统计 warning / block 的触发频次，现在还会尽量关联：

- linked runs
- linked closed trades
- linked win rate
- linked total / avg realized P&L
- 每条 warning / blocking 规则对应的结果上下文

前提是已平仓交易通过 `trading record --run-id ...` 或自动关联成功写入 SQLite。

### 12. 历史结果回放

`trading replay-stats` 会把已关联的 `trade_runs + trade_records` 直接回放成策略样本，当前可按：

- BTC regime
- execution template
- narrative
- final action
- adaptive profile

查看：

- sample count
- win rate
- avg / total realized P&L
- avg P&L%
- losing samples 中最常见的 warnings

它不是历史 K 线回测，但已经能回答“哪些市场状态下，这套建议更靠谱”。

### 13. Edge 研究统计

`trading edge-stats` 会在已有 linked 样本上进一步计算更像交易员会看的赚钱指标：

- win rate
- avg win / avg loss
- payoff ratio
- profit factor
- expectancy

并按：

- BTC regime
- execution template
- narrative
- setup quality grade
- gating profile

切片查看哪里最有 edge。

同时，系统现在已经把这层研究轻度反哺回主链：

- 历史 edge 明显为正的 slice：允许轻度放大
- 历史 edge 明显为负的 slice：自动降级或拦截
- 样本不足：不乱动
- 统一的 `opportunity_score` 会把 confidence / consensus / quality / edge / reward-risk / warnings 压成单一分数

另外现在也支持：

- `trading tune-edge`

把这些强/弱 edge slice 生成为：

- `edge_policy_allowlist`
- `edge_policy_denylist`

并导出或应用到配置中。

如果你确认建议合理，也可以直接应用：

- `trading tune-edge --apply --yes`

### 14. 多币种批量扫描

`trading batch-trade` 一次性对多个交易对运行完整 Pipeline：

- 支持命令行指定或从 `config.yaml` 的 `monitor.scan_symbols` 读取默认列表
- 默认会突出显示 `batch_top_n_signals` 个最值得做的机会
- 当开启 `--auto-execute` 时，也只会对 Top-N 候选池再做第二段自动执行
- 单个币种失败不阻塞其他币种
- 完成后汇总所有有信号（非 neutral）结果
- 自动推送飞书汇总通知

它现在的流程是：

1. 先全量分析所有候选
2. 排出 Top-N 机会池
3. 只有这批最强候选才进入自动执行阶段

另外，自动执行还会再受一个独立名额限制：

- `monitor.batch_auto_execute_max_candidates`

也就是你可以：

- 展示 Top 5 机会
- 但只让其中前 2 个进入自动执行

### 15. 历史 K 线回放 Backtest

`trading backtest` 会对已保存的 run 信号回放历史 Binance K 线，模拟：

- 是否先 hit SL
- 是否先 hit TP
- 或最后走 time exit

这是第一版 run-signal 驱动回放，不是完整 point-in-time 多轮滚动回测，但已经能验证：

- 当时的计划如果真的拿着，会发生什么
- 不同 regime/template 下的计划，在价格路径里是否更容易 hit TP 或 SL
- rolling windows 下的收益是否稳定
- walk-forward 窗口里训练段和测试段是否同样成立

### 16. 风控参数调优建议

`trading tune-risk` 会基于：

- linked closed trades
- `risk-stats`
- `replay-stats`

生成一组**保守型调优建议**，当前会覆盖：

- `meme` 风险参数
- `high_beta_alt` 风险参数
- BTC 弱势时 alt 风险参数
- funding 过热阈值
- 事件压仓参数
- adaptive prompt loss-streak 阈值

默认只展示建议；如有需要可导出为独立 YAML patch，不会直接覆盖主配置。

如果你确认要应用建议，现在也支持：

- `trading tune-risk --apply --yes`
- 自动先备份 `config.yaml`
- 再把建议 merge 进现有配置

回滚可用：

- `trading rollback-config --list`
- `trading rollback-config --yes`

---

## 主要命令

```bash
# 系统状态
trading status

# 账户与持仓
trading account
trading account --dry-run

# 同步持仓到 Vault
trading sync --dry-run
trading sync --no-dry-run

# 运行完整币圈 AI 交易流程（输出手动下单清单）
trading trade BTCUSDT
trading trade ETHUSDT --dry-run
trading trade BTCUSDT --auto-execute

# 实时 WebSocket 风控守护（监控持仓价格/资金费率/失效价突破）
trading watch                                          # 自动从 Binance 读取持仓
trading watch BTCUSDT ETHUSDT                          # 指定币种（失效价从 SQLite 加载）
trading watch BTCUSDT --direction long --entry 95000 --invalidation 93000 --stop 92000

# 多币种批量扫描（一次分析多个交易对）
trading batch-trade                                    # 使用 config.yaml 中 scan_symbols
trading batch-trade BTCUSDT ETHUSDT SOLUSDT --dry-run  # 指定币种
trading batch-trade BTCUSDT ETHUSDT --auto-execute

# 录入已平仓交易
trading record CHZUSDT --side long --entry 0.05 --exit 0.068 --qty 128000 --leverage 30 --open "2025-01-01 10:00"
trading record BTCUSDT --side long --entry 50000 --exit 52000 --qty 0.2 --leverage 5 --open "2025-01-01 10:00" --run-id 42

# 查看交易日志
trading journal

# 生成交易反思
trading reflect --symbol BTCUSDT

# 查看风控规则统计
trading risk-stats
trading risk-stats --symbol BTCUSDT

# 回放已关联结果样本
trading replay-stats
trading replay-stats --symbol BTCUSDT

# 查看 edge 统计
trading edge-stats
trading edge-stats --symbol BTCUSDT

# 生成 alpha allow/deny 建议
trading tune-edge
trading tune-edge --symbol BTCUSDT --output edge-policy.yaml
trading tune-edge --symbol BTCUSDT --apply --yes

# 历史 K 线回放 backtest
trading backtest
trading backtest --symbol BTCUSDT --limit 20 --hours 48

# 生成风控参数调优建议
trading tune-risk
trading tune-risk --symbol DOGEUSDT --output tuned-risk.yaml
trading tune-risk --symbol DOGEUSDT --apply --yes

# 查看 / 回滚配置备份
trading rollback-config --list
trading rollback-config --yes
```

---

## 安装

```bash
pip install -e ".[dev]"
```

或：

```bash
uv sync
```

---

## 配置

### .env

至少需要：

```env
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
ANTHROPIC_API_KEY=...
```

### config.yaml

最重要的配置块：

- `vault`
- `risk`
- `trading`（含 `combined_ai_calls` AI合并调用开关）
- `trading`（含 `stable_mode_*` 与 `conviction_override_*` 高质量 setup / 高信心放行参数）
- `trading`（含 `auto_execute_*` 自动执行白名单 / 频率 / 信号门槛）
- `trading`（含 `edge_policy_*` 基于历史赚钱能力的 allowlist / denylist / 放大参数）
- `trading`（含 `opportunity_score_*` 自动执行统一分数门槛）
- `claude`（含 `ensemble_enabled / ensemble_models / ensemble_min_agreement / adaptive_prompt_*`）
- `events`（事件源开关 + 压仓参数）
- `rebalance`（组合调仓开关 + 偏差阈值 + 安全上限）
- `monitor`（含 `scan_symbols` 批量扫描列表）
- `notification`（飞书 Webhook URL + 各场景开关）
- `logging`

现在 `risk` 已经包含不少币圈专用参数，例如：

- 高波动阈值
- funding 拥挤阈值
- alt 相对 BTC 弱势阈值
- high beta alt 风险参数
- meme 风险参数
- narrative thesis window 限制

---

## 运行模式

### `account --dry-run`

- 使用 mock 账户与 mock 持仓
- 不访问真实 Binance

### `sync --dry-run`

- 使用 mock 持仓
- 只做 Vault 内容预览
- 不访问真实 Binance
- 不写 Vault

### `trade --dry-run`

- 使用 mock 账户快照与 mock 市场快照
- 仍然会完整跑：
  - AI 研究
  - AI 执行计划
  - 风控
  - 手动下单清单

### `trade`（非 dry-run）

- 会读取真实 Binance 账户/仓位/市场快照
- 会输出真实手动下单清单
- **不会自动真实下单**

---

## 目录结构

```text
src/trading/
├── core/           # config / vault / logger / journal / retry
├── exchange/       # Binance 账户、仓位、订单、市场快照(TTLCache) + CoinGecko
├── onchain/        # 链上数据（DeFiLlama 协议TVL / 链TVL / 稳定币）
├── events/         # 事件源（宏观日历 + Token解锁）
├── ai/             # Claude client / prompts(含Portfolio注入+事件注入+智能截断) / schemas / advisor
├── risk/           # RiskGate(rules/) / PortfolioManager / EquityCurve / RiskDaemon
├── notification/   # 飞书 Webhook 通知推送
├── pipeline/       # end-to-end trade pipeline + persistence + rebalancer
├── reports/        # 预留（当前报告构建在 pipeline 内）
└── main.py         # CLI
```

---

## 测试

```bash
python -m pytest -q
```

当前测试已覆盖（301+ 自动化测试）：

- exchange / um_futures
- journal
- schemas
- pipeline
- risk gate + 规则引擎
- ensemble 投票与 risk-stats 聚合
- adaptive prompt profile
- trade run ↔ closed trade linking
- replay stats / linked outcome replay
- edge stats / expectancy research
- kline replay backtest
- tune-edge policy suggestions
- risk tuning suggestions
- market data + TTLCache
- portfolio manager
- notification（飞书 Webhook）
- events（宏观日历 + Token解锁 + EventRiskRule）
- CLI safe preview behavior
- 基础设施（retry / config validation / 连接复用）
- 回归修复

---

## 当前边界

当前已实现：

- 币圈市场快照（TTL 分层缓存：K线5min / Ticker30s / Funding1min / Depth10s）
- CoinGecko 数据扩展（market_cap / rank / categories / FDV，10min 缓存）
- 事件源接入（宏观经济日历 + Token解锁，高影响事件自动压仓 50% + 杠杆≤ 10x）
- 组合动态调仓（Rebalancer: 基于 PortfolioBudget 偏差检测 + 自动执行减仓/加仓 + batch-trade 末尾自动触发）
- AI 研究 + 执行计划（Claude，支持合并单调用节省 ~30% token）
- AI Prompt 注入 Portfolio 上下文（gross/net exposure、叙事集中度、仓位预算）
- Vault 上下文智能截断（按 symbol 相关性优先 + token 预算）
- 叙事标签映射（79+ 显式映射 + `general_alt` fallback）
- 确定性风控规则引擎（13+规则文件，BaseRule 架构，含 EventRiskRule）
- 组合层快照与仓位预算（PortfolioManager）
- 资金曲线 EMA 保护（快/慢 EMA 交叉触发仓位压缩）
- 动态杠杆调整（波动率 + 胜率加权）
- 手动执行清单
- Vault 报告 + SQLite 持久化
- 交易反思
- 实时 WebSocket 风控守护（`trading watch`）
- 链上数据接入（DeFiLlama）
- 飞书通知推送（风控告警 / Pipeline完成 / SL触发 / 批量扫描）
- 多币种批量扫描（`trading batch-trade`）
- Prompt 自适应（基于历史胜率/连亏/反思/risk-stats）
- 风控规则效果统计（触发频次 + 关联已平仓结果）
- 历史结果回放（`replay-stats`，按 regime/template/narrative 回看真实样本）
- Edge 研究统计（`edge-stats`，按 expectancy / PF / payoff ratio 找赚钱 slice）
- 收益导向 edge policy overlay（历史强 edge slice 轻度放大，弱 edge slice 降级）
- Alpha allowlist / denylist 建议（`tune-edge`，可把赚钱 slice 固化进配置）
- Batch Top-N 机会池（按 edge / quality / confidence 排序）
- 统一机会分数 Opportunity Score（把 edge / quality / confidence 压成一个排序与执行分数）
- 历史 K 线回放 Backtest（`backtest`，按已保存 run 信号回放价格路径）
- 风控参数调优建议（`tune-risk`，默认只读分析，可导出 YAML patch）
- 风控参数安全应用 / 回滚（`tune-risk --apply --yes` + `rollback-config`）
- 真实 API 自动执行链（默认关闭，需 `--auto-execute` 或显式配置开启）
- 基础设施：连接复用 / 指数退避重试 / 配置验证 / 请求超时 / SL失败补偿
- 301+ 自动化测试全通过

当前尚未实现 / 仍建议继续增强：

| 类别 | 项目 | 说明 |
|------|------|------|
| 执行引擎 | 自动真实开仓主链 | 当前 `trade` 路径只输出人工下单清单，不直接开新仓 |
| 调度 | 定时批量扫描 | APScheduler/cron 每 4h 自动运行 batch-trade + 飞书推送，无人值守 |
| 监控告警 | Grafana / Prometheus 指标 | 账户权益、持仓曝险、风控触发次数等时序指标可视化 |
| 性能 | 异步化改造 | asyncio + aiohttp 替换同步 REST，并发获取多币种数据 |
| 性能 | 多币种并行 Pipeline | 同时运行多个交易对的研究+风控流程（当前 batch-trade 为串行） |

---

## 真实环境预演

在连接真实账户前，建议按这条顺序检查：

1. `trading status`
2. `trading account --dry-run`
3. `trading sync --dry-run`
4. `trading trade BTCUSDT --dry-run`
5. `trading batch-trade BTCUSDT ETHUSDT --dry-run`
6. 如需监控，再运行 `trading watch BTCUSDT --direction long --entry ... --invalidation ...`

更完整的上线前检查清单见：

- [docs/preflight.md](docs/preflight.md)

## 长期增强方向

| 类别 | 项目 | 说明 |
|------|------|------|
| AI 增强 | 多模型仲裁升级 | 在已有 ensemble 基础上，引入更细的 disagreement routing / veto 机制 |
| 回测 | 历史回测引擎 | 用历史 K 线模拟 Pipeline 决策，统计胜率/最大回撤 |
| 规则学习 | 风控参数自动审批策略 | 在已有人工 apply/rollback 基础上，引入更严格的审批条件、分级应用与效果追踪 |
| 知识库 | 自动抽取交易经验 | 从历史反思中自动提炼规则，并反馈到风控参数 |
| 知识库 | 币种研究库索引 | 自动索引历史调研报告，下次研究时自动参考 |
| 报告 | 独立 Reports 模块 | 结构化报告生成（PDF/HTML），周报/月报自动汇总 |
| 运维 | Docker 化部署 | docker-compose 一键部署，含 cron 定时任务 |

所以当前最准确的定位是：

> **面向币圈投资的 AI 研究 + 风控 + 人工执行操作系统**
