# fastls-ffi

Fastls 的 C ABI 动态库和 Python SDK。当前是未发布的开发版本。
指纹生成复用独立 `FastTLS/imitate`，请求侧复用 `FastTLS/fastls`；Python 无需手动接入 Provider。

## 本地构建

需要 Go 1.24+ 与目标平台的 cgo C 编译器。当前上游依赖是未发布候选，需要本地兄弟仓库：

```text
workspace/
  fastls/          # 含 profile 子模块与本阶段核心改动
  fhttp/
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

Python 3.10+，无第三方运行依赖。安装本地 SDK：

```shell
python -m pip install -e ./python
```

本地源码默认查找本仓库 `dist/`。安装后建议设置 `FASTLS_LIBRARY` 为动态库绝对路径，或向 Session 传 `library=...`。当前 Python 包尚未内置平台二进制，尚未发布 PyPI。

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

单次调用可以覆盖 `profile`、`headers`、`timeout`、`allow_redirects`。
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
SDK 定时轮询非阻塞状态/读取接口，间隔至少10ms，并大于平台时钟精度以避免短定时器忙等；不用逐请求 Python 线程，Go 网络工作在 goroutine 中执行。
这是首版调度策略，高并发效率尚未做性能承诺，后续将评估完成通知。

## C ABI v1

以构建生成的 `fastls.h` 为准确声明。接口均为 C 调用约定：

- `fastls_abi_version()`：当前为1。
- `fastls_profiles()`：返回规范ID数组的JSON结果。
- `fastls_stats()`：返回只读诊断JSON结果，新增符号，不改变已有函数的ABI。
- `fastls_start(data, length)`：输入UTF-8 JSON，立即返回请求句柄的JSON结果。
- `fastls_info(handle)`：返回状态pending/headers/eof/error、状态码、头、Match或错误。
- `fastls_read(handle, buffer, capacity, &length)`：非阻塞二进制读取。0=数据、1=等待、2=EOF、-1=请求失败、-2=非法参数或句柄。
- `fastls_cancel(handle)`：取消并保留句柄以便查询。
- `fastls_close(handle)`：移除句柄并取消，多次调用无害。
- `fastls_free(pointer)`：释放上述JSON结果。可传NULL；不得释放别处的内存或重复释放。

JSON结果为 `{"ok":true,"result":...}` 或 `{"ok":false,"error":"..."}`。
每次获取的JSON指针都必须释放。输入和读取缓冲仅在调用期间使用；不返回Go指针。
start输入字段：url、method、profile、headers、body或body_base64、timeout_ms、disable_redirect、可选ca_pem及session。未知字段拒绝。
输入上限1MiB，解码上传上限512KiB，未关闭请求上限256；单次读取1..1MiB。
同一响应只支持单个读取者，控制接口可并发调用；完成后仍须close。库应随进程长期加载。

## 当前边界与验证

Session 管理默认配置、活动请求及独立的连接池，支持HTTP1 keep-alive和HTTP2多路复用。不同Session、profile或CA配置相互隔离；可调用session.close_idle_connections()主动释放空闲连接。使用方法、限制和C ABI扩展见[POOLING.md](POOLING.md)。
当前不提供自动Cookie jar、流式上传、代理、自定义完整Spec或WebSocket。
响应头沿用核心单值表示，重复头保留不完整；响应体保持核心返回的编码，不自动解压Content-Encoding；text按UTF-8解码，不提供完整requests/httpx兼容。
指纹TCP TLS请求始终启用证书校验；未提供关闭校验选项。
本仓库尚未公开发布；许可证和上游发布门禁仍待处理，不能把本地构建当成远端候选已发布。

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
