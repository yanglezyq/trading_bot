<!-- Qoder Rule Type: Always Apply -->
<!-- 适用范围: 所有代码修改 -->

# 编码规范

## 基础约定

| 维度 | 规范 |
|------|------|
| Python 版本 | 3.11+，使用 `list[str]`、`dict[str, Any]`、`X | Y` 联合类型 |
| 格式化 | Black（line-length=100） + Ruff，提交前必须通过 |
| 类型检查 | mypy strict，所有公共方法必须有完整类型注解 |
| 导入顺序 | 标准库 → 第三方（binance/anthropic/typer/rich/yaml） → 本项目（`from .xxx`） |

## 数据模型

- 用 `@dataclass` 表示数据对象（`Balance`、`FuturesPosition`、`Order` 等），不用 dict
- `@property` 用于计算字段（如 `total`、`is_long`、`notional_value`）
- config 类也是 dataclass，由 `load_config()` 统一构造，禁止在其他地方散构

## dry_run 模式规范

- 所有写操作方法（下单、写 Vault、写文件）**必须**支持 `dry_run` 参数
- dry_run=True 时：打印预览，返回 `None` 或 mock 数据，不发起真实 API 调用
- dry_run 默认值：
  - `BinanceClient` 的读操作：dry_run=False 也可执行（只读安全）
  - `BinanceClient` 的写操作：dry_run=True 是强制保护
  - CLI 命令：`account`（默认 dry_run=False 读数据），`sync`（默认 dry_run=True 保护写操作）
- dry_run 层级：CLI flag → 方法参数 → client.dry_run（方法参数优先）

## 错误处理

- 异常分类：使用 `TransientError`（可重试）和 `PermanentError`（不可重试）
- 网络/API 调用使用 `@retry(max_attempts=3, base_delay=1.0)` 装饰器
- 自动识别暂时性异常：ConnectionError, TimeoutError, HTTP 429/503
- 不吞异常（不写 `except: pass`）
- CLI 层捕获异常后用 `console.print(f"[red]Error: {e}[/red]")` 显示，并 `raise typer.Exit(1)`
- 读操作失败返回 `None`，写操作失败抛出异常
- SL/TP 下单失败：自动重试 1 次，仍失败则 CRITICAL 告警

## 命名规范

- 类：PascalCase（`AccountManager`、`BinanceClient`）
- 函数/方法/变量：snake_case（`get_futures_balance`、`dry_run`）
- 常量：UPPER_SNAKE_CASE（保留用于 config.yaml 的枚举值）
- 文件名：snake_case（`client.py`、`positions.py`）
- 模块导出：每个包必须有 `__init__.py` 并显式声明 `__all__`

## 注释规范

- 默认不写注释，只在 WHY 非显而易见时写
- 禁止写 docstring 多行描述（单行 `"""简短说明"""` 可以）
- 禁止写 "# 修复了 xxx 的问题" 类型的历史注释

## 测试规范

- 测试文件放 `tests/`，文件名 `test_{module}.py`
- 每个公共方法至少一个 dry_run 测试
- fixture 命名：`{scope}_{what}`（如 `binance_client_dry_run`、`mock_binance_config`）
- 断言必须有明确的期望值，不写 `assert True`
- 不 mock 外部 API（用 dry_run=True 代替）：避免 mock 与真实行为偏差
- 新增模块时同步创建 `tests/test_{模块名}.py`

## 缓存规范

- 市场数据使用 `TTLCache` 分层缓存，不同数据类型不同 TTL
- exchange_info 等元数据使用 1h TTL
- 缓存 key 为 `"{symbol}_{interval}"` 格式
- 内部使用 OrderedDict 实现 LRU 淘汰

## 模块导出规范

新增类/函数后，必须同步更新对应 `__init__.py` 的 `__all__`：
- `src/trading/core/__init__.py` → core 层新增导出（含 TransientError, PermanentError, retry）
- `src/trading/exchange/__init__.py` → exchange 层新增导出
- `src/trading/ai/__init__.py` → ai 层新增导出
- `src/trading/risk/__init__.py` → risk 层新增导出
