# fastls-ffi

Fastls 的 C ABI 动态库和 Python SDK。当前是未发布的开发版本。
指纹生成复用独立 `FastTLS/imitate`，请求侧复用 `FastTLS/fastls`；Python 无需手动接入 Provider。

## 本地构建

需要 Go 1.26+ 与目标平台的 cgo C 编译器。当前上游依赖是未发布候选，需要本地兄弟仓库：

```text
workspace/
  fastls/          # 含 profile 子模块与本阶段核心改动
  ftransport/
  imitate/
  fastls-ffi/
```

在本仓库运行：

```shell
python scripts/build.py --local
```

编译器可通过 `CC` 或 `--cc` 指定。Windows 需兼容 Go cgo 的编译器，例如 MinGW-w64；已有 Zig 也可以使用 `--cc "<zig路径> cc -target x86_64-windows.win10-gnu"`。
产物位于 `dist/`，包含动态库、生成的 C 头文件及构建说明。各系统和架构需分别构建并运行验证。
`--local` 只写本仓库被忽略的 go.work，不修改全局 Go 配置或上游 go.mod。
不带 `--local` 的构建要求依赖候选已能从模块源获取；目前不承诺可用。

Python 3.10+，无第三方Python运行依赖。安装本地 SDK：

```shell
python -m pip install -e ./python
```

本地源码和SDK-only sdist不编译或下载原生库，默认可查找本仓库 `dist/`；独立安装后需设置 `FASTLS_LIBRARY` 为动态库绝对路径，或向 Session 传 `library=...`。候选平台wheel已内置对应动态库，但尚未发布PyPI；平台与系统边界见[PACKAGING.md](PACKAGING.md)。

## 同步调用

```python
from fastls import Session

with Session(profile="chrome/156/windows") as session:
    response = session.get(
        "https://tls.peet.ws/api/all",
        headers={"User-Agent": "my-client/1.0"},
    )
    print(response.status_code, response.json())
    print(response.match)
```

单次调用可以覆盖 `profile`、`headers`、`trailers`、`timeout`、`allow_redirects`。响应通过 `http_version`、`url` 和 `trailers` 暴露最终协议、重定向后 URL 与正文结束后的 Trailer。
`data` 接受 str 或 bytes。timeout 单位秒，覆盖从发起到流结束的整个请求，默认30秒。
用户头值优先，包含空值；未知规范 ID 报错。目录可通过 `session.list_profiles()` 获取。
`chrome/156/windows` 的生成身份和协议基线不是同一概念，以 Match 与上游证据为准。

私有CA可使用 `Session(ca_file="test-ca.pem")` 或 `AsyncSession(ca_file=...)`。该信任列表只作用于此Session的请求，替代系统根；证书链与主机名校验仍然开启，不修改系统证书。文件在创建Session时读取。
`session.stats()` 返回当前Go运行时的只读诊断快照：未关闭会话及请求、会话传输配置数、网络worker、桥接缓冲字节、goroutine及Go堆；不是性能指标API，也不会主动触发GC。新版SDK需要同步更新动态库。

## 流式下载

```python
with Session() as session:
    with session.stream("GET", "https://example.com/large-file") as response:
        with open("download.bin", "wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
```

使用上下文管理器确保提前退出也释放资源。块边界不对应行、SSE事件或HTTP帧。
普通 request/get/post 会缓存响应体，最多32MiB；更大响应使用 stream。
Go 桥接层每请求最多预读两个64KiB块，加上调用方未消费的至多一个块；不含协议栈与操作系统缓冲。

## asyncio

```python
from fastls import AsyncSession

async def download(url):
    async with AsyncSession(profile="chrome/156/windows") as session:
        async with session.stream("GET", url) as response:
            async for chunk in response.aiter_bytes():
                await consume(chunk)
```

`await session.get(url)` 返回已缓存的响应；流式响应使用 `await response.aread()`。
任务取消或上下文退出会关闭请求；`response.cancel()` 也可以直接取消。
新动态库默认使用状态通知：同步等待原生就绪，asyncio每会话共用本机唤醒通道，不增加Python工作线程或Go→Python回调。可用notifications='required'要求新能力，或notifications='poll'保留轮询；旧库auto模式继续轮询。生命周期和C ABI见[NOTIFICATIONS.md](NOTIFICATIONS.md)。

## C ABI v1

以构建生成的 `fastls.h` 为准确声明。接口均为 C 调用约定：

- `fastls_abi_version()`：当前为1。
- `fastls_profiles()`：返回规范ID数组的JSON结果。
- `fastls_stats()`：返回只读诊断JSON结果，新增符号，不改变已有函数的ABI。
- `fastls_wait()`与`fastls_session_watch/events/unwatch()`：原生就绪等待及会话通知，见[NOTIFICATIONS.md](NOTIFICATIONS.md)。
- `fastls_start(data, length)`：输入UTF-8 JSON，立即返回请求句柄的JSON结果。
- `fastls_info(handle)`：返回状态 pending/headers/eof/error、状态码、最终协议/URL、头、正文结束后的 Trailer、Match 或错误。
- `fastls_read(handle, buffer, capacity, &length)`：非阻塞二进制读取。0=数据、1=等待、2=EOF、-1=请求失败、-2=非法参数或句柄。
- `fastls_cancel(handle)`：取消并保留句柄以便查询。
- `fastls_close(handle)`：移除句柄并取消，多次调用无害。
- `fastls_free(pointer)`：释放上述JSON结果。可传NULL；不得释放别处的内存或重复释放。

JSON结果为 `{"ok":true,"result":...}` 或 `{"ok":false,"error":"..."}`。
每次获取的JSON指针都必须释放。输入和读取缓冲仅在调用期间使用；不返回Go指针。
start 输入字段：url、method、profile、headers、可选 trailers、body 或 body_base64、timeout_ms、disable_redirect、可选 ca_pem 及 session。未知字段拒绝。
输入上限1MiB，解码上传上限512KiB，未关闭请求上限256；单次读取1..1MiB。
同一响应只支持单个读取者，控制接口可并发调用；完成后仍须close。库应随进程长期加载。

## 当前边界与验证

Session 管理默认配置、活动请求及独立的连接池，支持HTTP1 keep-alive和HTTP2多路复用。不同Session、profile或CA配置相互隔离；可调用session.close_idle_connections()主动释放空闲连接。使用方法、限制和C ABI扩展见[POOLING.md](POOLING.md)。
Session默认自动管理Cookie，提供session.cookies查询、设置、删除与清空；可用cookies=False禁用。response.headers支持大小写不敏感访问和get_list获取重复字段，详细规则见[COOKIES_HEADERS.md](COOKIES_HEADERS.md)。
Python默认流式解码gzip、deflate、br及zstd，可通过decode_content=False取得原始正文。Timeout支持总时限及连接、响应头、正文读取预算；用法、原始头视图与解码上限见[STREAMING_TIMEOUTS.md](STREAMING_TIMEOUTS.md)。
已支持同步/异步流式上传及write超时，见[STREAMING_UPLOAD.md](STREAMING_UPLOAD.md)。HTTP/2支持显式全双工：异步使用duplex=True，同步使用Session.duplex()手动交互，见[DUPLEX.md](DUPLEX.md)。Python同步与asyncio新增session.sse()按事件消费SSE，支持id/retry、手动恢复及大小限制，见[SSE.md](SSE.md)。代理与WebSocket支持分别见[PROXY.md](PROXY.md)和[WEBSOCKET.md](WEBSOCKET.md)；当前不提供自定义完整Spec，text按UTF-8解码，也不提供完整requests/httpx兼容。
指纹TCP TLS请求始终启用证书校验；未提供关闭校验选项。
本仓库尚未公开发布。许可证、sdist、升级/卸载和Linux glibc范围已经纳入release门禁；最终版本号、发布签署及目标索引凭据仍由维护者决定，不能把本地构建或Actions artifact当成已发布版本。

```shell
go test ./internal/bridge
python tests/test_python.py
python scripts/check.py
```

实施规划见 [PLAN.md](PLAN.md)，本地验收记录见 [VALIDATION.md](VALIDATION.md)。

## GitHub Actions

每次push或PR自动运行Windows x64、Linux x64/ARM64及macOS Intel/Apple Silicon的原生构建与测试，另有最低Go/Python版本组合。
构建产物与日志作为artifact保存；依赖必须绑定已推送的审阅提交，不回退到旧main。
上游v2开发源码已推送并固定到提交SHA，无需先发布Go模块标签，配置和启用步骤见 [CI.md](CI.md)。

稳定性验收增加HTTPS H1/H2、128路混合请求/取消、64MiB流式下载、资源回收及持续运行测试；范围、阈值与时间上限见 [TESTING_STAGE2.md](TESTING_STAGE2.md)。这些检查不表示尚未实现的功能已交付。

网络异常测试覆盖握手停滞、截断和畸形响应、HTTP2流重置、读取截止时间、重定向与跨主机敏感头处理；源码及wheel均纳入CI，见[TESTING_STAGE3.md](TESTING_STAGE3.md)。
