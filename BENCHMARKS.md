# Python 客户端性能与资源基准

本工具检查 Fastls 的协议、请求行为和资源回收，并在 HTTP/1.1 同步场景中与 Requests 比较。基准依赖固定在 `benchmarks/requirements.lock`，不增加 SDK 运行依赖。阶段范围见 [NEXT_PHASE.md](NEXT_PHASE.md)。

## 运行与查看

使用 Python 3.11 创建独立环境。源码模式需要与原生 CI 相同的四个兄弟仓库布局及 cgo 编译器。

```shell
python -m pip install -r benchmarks/requirements.lock
python benchmarks/prepare.py
python benchmarks/run.py --profile smoke --output build/benchmark-smoke
```

默认 `checkout` 模式测量当前源码 SDK 与 dist 库。已发布包使用独立环境安装所需版本及基准依赖，再运行：

```shell
python benchmarks/run.py --fastls-source installed --fixture /absolute/path/to/ffi-fixture --profile smoke --output build/published-smoke
python tests/client_conformance.py --fastls-source installed --fixture /absolute/path/to/ffi-fixture --output build/published-conformance
```

`installed` 模式验证 wheel 版本、build-info 和原生库摘要，记录真实模块路径，不加载当前源码 SDK。显式指定的靶场也记录 SHA256；其源码和构建来源随测量清单保存。不同源码、版本或原生库的结果分别标识，不把当前改进算入旧发行包。

输出目录中的 `report.html` 可直接打开，`results.json` 保存完整数据；输入、结果和错误日志同时保留，不含测试 CA 私钥。用重复的 `--case` 选择场景，名称见 [benchmarks/common.py](benchmarks/common.py)。多份记录可按环境合并：

```shell
python benchmarks/report.py path/to/results-a.json path/to/results-b.json --output comparison.html
```

- `smoke`：256 KiB、少并发、1 轮及 0.15 秒小请求采样，只验证脚本和契约。
- `quick`：64 MiB 正文、32 并发小请求、2 轮及 1 秒采样，用于诊断。
- `full`：至少 5 轮；小请求预热 10 秒、测量 30 秒；大文件按完成次数，慢消费者限速 16 MiB/s，空闲流等待 5 秒。

公共基准仅手动触发，默认 `quick`，不进入每次提交的发布关键路径。完整模式可分 6 组在 Windows/Linux 执行；同一场景的全部客户端和轮次留在同一台 runner。`cases` 输入非空时不分片。完整性能认证另按项目测试策略决定，不因提交或改写历史自动重跑。

## 场景与正确性

H1 同步覆盖 Fastls 和 Requests；H1/H2 异步仅检查 Fastls。场景包括冷热连接、小响应、下载、gzip、上传、慢消费者、空闲 SSE 字节流及浏览器预设。Fastls 原生通知与轮询按同机 paired 场景执行；SSE 解析只统计事件 data 字节，单独展示。Requests 的 H2 和异步接口标记为不适用。

每个客户端、场景和轮次使用独立子进程；同步线程独立创建 Session，异步场景共享 AsyncSession。关闭环境代理、重试和重定向，验证同一临时 CA，并要求错误 CA 确实触发证书错误。行为检查覆盖 Cookie、会话隔离、重定向、截断恢复、超时取消、CONNECT 代理及异步兄弟流隔离。提前关闭未能取消远端正文视为失败。

独立服务端记录实际协议、连接 ID 和 TLS 信息。冷热组分别验证新建和复用；上传与下载均检查长度及 SHA256。正文是重复的 `00 01 80 ff`，压缩率不能外推到真实网页。通用组使用 portable-h1/h2，浏览器组使用 `chrome/156/windows`；不同协议和预设分别展示。

## 指标与限制

TTFB 从实际发起请求到迭代器取得第一个非空正文块计时。gzip 统计解压后的业务字节，大文件测量包含摘要计算。小请求包含截止前已发起请求的完整收尾；超时和外部截止时间保证有界。Requests 使用连接/读取预算，Fastls 使用总预算，正常测量不触及预算。

延迟使用固定大小的 2% 乘法桶，展示 p50/p95 桶上界；不足 20 个成功样本不展示 p95。任一计划轮失败或缺失，保留原始失败及错误计数，不生成该组性能汇总；不同主机、协议、工具版本和靶场来源分组。smoke/quick 不作为性能结论，共享 runner 也不设速度倍率门槛。

外部采样约每 50 ms 记录整个客户端进程树及独立服务端的 CPU、RSS、句柄和线程。阶段边界通过握手确认，关闭后采样在进程退出前完成，避免遗漏 Windows 启动器后的实际解释器。慢消费者另记录已消费字节和可观测缓冲；无法取得的值留空。Fastls 请求、worker、通知和会话登记必须归零，不强制 GC，也不承诺 RSS 立即回到初值。

历史记录保留其原有失败和测量限制，不能用新结果覆盖旧结论。测量期间修改源码会使该轮无效。

## 工具检查

基准靶场通过 Go 文件 overlay 将 H2 SETTINGS ACK 的布尔状态改为计数器，以逐帧确认连续设置。构建固定 Go 版本及源码 SHA256，未知输入明确拒绝；不修改 GOROOT 或 SDK。输出靶场及构建清单，报告按靶场来源分组。

```shell
python scripts/build_benchmark_fixture.py
python tests/probe_h2_settings_ack.py --fixture build/ffi-fixture-benchmark.exe --output build/settings-ack.json
```

`benchmarks/prepare.py` 执行独立 ACK 黑盒检查。`tests/client_resource_soak.py` 保留下载、待响应取消、慢上传及同会话恢复等资源检查，按风险需要单独运行；日常工具清理不默认执行完整轮次。

`tests/test_benchmarks.py` 检查采样、分位数、失败保留、环境隔离、场景范围、客户端选择及离线报告转义。独立的回归与观察工具测试继续检查失败拦截和子进程清理。
