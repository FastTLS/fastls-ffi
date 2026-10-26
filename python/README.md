# fastls Python SDK

通过原生 Fastls 动态库发送带浏览器配置的 HTTP 请求，支持同步、asyncio、流式上传下载、HTTP/2 全双工、SSE 和 WebSocket。Python 发行包名与导入名均为 `fastls`，仓库名为 `fastls-ffi`。当前正式版本为 [0.1.3](https://pypi.org/project/fastls/0.1.3/)。

## 安装与平台

需要 Python 3.10+。推荐在虚拟环境中安装：

```shell
python -m pip install --only-binary=:all: fastls==0.1.3
```

平台 wheel 已包含动态库，无需 Go、C 编译器或额外下载 DLL。`--only-binary=:all:` 确保没有匹配平台时明确失败，避免误装不含动态库的源码包。

- Windows：x86-64 Python，`win_amd64`。
- Linux：x86-64 / ARM64，`manylinux_2_39_x86_64` / `manylinux_2_39_aarch64`，要求 glibc 2.39+；不支持 Alpine/musl。
- macOS：Intel / Apple Silicon，`macosx_15_0_x86_64` / `macosx_15_0_arm64`，要求 macOS 15+。

按 Python 解释器运行的平台选择 wheel。Linux 上也能使用 `chrome/156/windows`；配置名不会改变动态库的系统要求。历史 GitHub v0.1.2 附件仍使用 `fastls_ffi` 名称和 `linux_*` 标签，不要重命名旧附件。

从旧发行包迁移时，先执行 `python -m pip uninstall fastls-ffi`，再安装 `fastls`；两者提供同一个导入目录，不应并存。确认版本：

```shell
python -c "from importlib.metadata import version; print(version('fastls'))"
```

GitHub 直装、升级卸载及排错见[完整安装指南](https://github.com/FastTLS/fastls-ffi/blob/main/INSTALL.md)。

## 同步请求

```python
from fastls import Session

with Session(profile="chrome/156/windows") as session:
    response = session.get("https://example.com/", params={"page": 1})
    response.raise_for_status()
    print(response.status_code, response.http_version, response.url)
    print(response.text)
```

使用 `session.list_profiles()` 获取可用配置；创建会话会实际加载动态库，仅 `import fastls` 不足以验证安装。复用 Session 可复用连接池与 Cookie，退出上下文会关闭所属请求。浏览器配置选择不代表与真实浏览器的全部网络行为一致，可通过 `response.match` 查看选择结果。

请求可设置 `headers`、`params`、`json`、`data`、`files`、`trailers`、`timeout`、`allow_redirects` 和单次 `profile`。任意方法使用 `session.request(method, url, ...)`；便利方法为 `get()` 和 `post()`。JSON 示例：

```python
from fastls import Session

with Session() as session:
    response = session.post("https://example.com/api", json={"hello": "world"})
    response.raise_for_status()
    print(response.json())  # 目标服务需要返回有效 JSON
```

`json` 不能与 `data` 或 `files` 同时使用。`data` 支持字符串、字节、表单映射、二进制文件和字节迭代器；`files={"file": ("name.bin", binary_file, "application/octet-stream")}` 用于 multipart，文件由调用方关闭。编码正文的长度、传输编码以及 multipart 的 Content-Type 由 SDK 管理。

响应提供 `.content`、`.text`、`.json()`、`.encoding`、`.headers`、`.wire_headers` 和 `.trailers`。响应头大小写不敏感，`headers.get_list("Set-Cookie")` 保留重复值；Trailer 在正文读取完成后更新。`.text` 使用 Content-Type charset，缺省 UTF-8，可修改 `.encoding`。HTTP 错误状态不会自动抛出异常，需调用 `.raise_for_status()`。

## 流式下载

普通 `request/get/post` 会缓存响应，最多 **32 MiB**。下载更大的正文时逐块消费：

```python
from fastls import Session, Timeout

with Session() as session:
    with session.stream("GET", "https://example.com/large.bin",
                        timeout=Timeout(total=300, read=30)) as response:
        response.raise_for_status()
        with open("large.bin", "wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
```

始终使用上下文管理器，确保提前退出也释放资源。同一响应只允许一个读取者；块边界不对应行、消息或 HTTP 帧。已逐块消费的数据不会自动保留为完整 `.content`，不要混用读取方式。

默认自动流式解压 gzip、deflate、br、zstd，解码正文上限为 256 MiB，包含流式读取。可按需设置 `max_decoded_bytes`；`decode_content=False` 返回原始正文。原始头在 `.wire_headers`，解码后的头在 `.headers`。完整规则见[流式与超时](https://github.com/FastTLS/fastls-ffi/blob/main/STREAMING_TIMEOUTS.md)。

## asyncio

```python
import asyncio
from fastls import AsyncSession

async def main():
    async with AsyncSession(profile="chrome/156/windows") as session:
        response = await session.get("https://example.com/")
        response.raise_for_status()
        print(response.text)
        async with session.stream("GET", "https://example.com/large.bin") as streamed:
            streamed.raise_for_status()
            async for chunk in streamed.aiter_bytes():
                print(len(chunk))

asyncio.run(main())
```

`await session.get/post/request(...)` 返回已缓存响应；异步流式接口使用 `aiter_bytes()` 或 `await response.aread()`，显式关闭用 `await response.aclose()`。不要为同一 AsyncSession 混用不同事件循环。任务取消和上下文退出会清理请求。

原生能力可用时，异步等待使用每会话的本机通知通道，不为网络请求增加 Python 工作线程或 Go→Python 回调。`notifications="required"` 要求通知能力；`"auto"` 自动选择，`"poll"` 使用轮询。用户同步文件读取和迭代器中的阻塞操作仍会阻塞当前线程。

## 流式上传与 HTTP/2 全双工

```python
from fastls import Session, Timeout

with Session() as session:
    with open("large.bin", "rb") as source:
        response = session.post("https://example.com/upload", data=source,
                                timeout=Timeout(total=300, write=30))
        response.raise_for_status()
```

二进制文件和字节迭代器可逐块上传；异步字节迭代器需使用 AsyncSession。上传队列应用背压，文件所有权不转移。流式正文不能自动重放：需要保留正文的 307/308 返回原响应。不要手动设置流式正文的 Content-Length 或 Transfer-Encoding。

需要边上传边接收时，AsyncSession 使用 `duplex=True`，并选择支持全双工的 HTTP/2 服务：

```python
import asyncio
from fastls import AsyncSession, Timeout

async def outgoing():
    for block in (b"first\n", b"second\n"):
        yield block
        await asyncio.sleep(0.1)

async def main():
    async with AsyncSession(profile="chrome/156/windows") as session:
        async with session.stream("POST", "https://example.com/exchange",
                                  data=outgoing(), duplex=True,
                                  timeout=Timeout(total=300, read=30, write=30)) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                print(chunk)

asyncio.run(main())
```

全双工仅支持所选指纹传输的 HTTP/2，不自动跟随重定向。响应 EOF 会结束交换并取消未完成上传；上传 EOF 后仍可读响应。`await response.wait_upload()` 只表示生产者结束且本地上传 EOF 已被接受，等待时仍需并发消费响应。同步手动交互使用 `Session.duplex()` 的 `send()`、`finish_upload()` 与 `.response`；同步 `stream(..., duplex=True)` 不支持。详见[上传](https://github.com/FastTLS/fastls-ffi/blob/main/STREAMING_UPLOAD.md)和[全双工](https://github.com/FastTLS/fastls-ffi/blob/main/DUPLEX.md)。

## SSE

```python
from fastls import Session, Timeout

with Session() as session:
    with session.sse("GET", "https://example.com/events",
                     timeout=Timeout(total=3600, read=45)) as events:
        for event in events:
            print(event.event, event.id, event.data)
            if event.data == "[DONE]":
                break
```

异步使用 `async with session.sse(...)` 和 `async for event in events`。支持 `event.json()`、`events.last_event_id`、`events.retry` 和请求参数 `last_event_id`。`retry` 单位为毫秒；SDK 不自动重连或重放，`[DONE]` 由应用决定如何处理。`max_event_bytes` 默认 1 MiB，约束单行及累计事件数据。详见 [SSE](https://github.com/FastTLS/fastls-ffi/blob/main/SSE.md)。

## WebSocket

```python
from fastls import Session

with Session(profile="chrome/156/windows") as session:
    with session.websocket("wss://example.com/chat") as websocket:
        websocket.send("hello")
        print(websocket.recv())
```

异步使用 `async with session.websocket(...)`，并 `await websocket.send(...)` / `await websocket.recv()`。字符串作为文本消息，bytes-like 作为二进制消息；收到正常关闭时 `recv()` 抛出 `WebSocketClosed`，携带 `.code` 和 `.reason`。手动连接用 `ws_connect()`（异步需 await），并调用 `close()` / `await aclose()`。

支持 `subprotocols`、`headers`、`params`、代理和 Session Cookie；`cookie_mode="manual"` 禁用本次自动 Cookie 读写。`max_message_bytes` 默认 1 MiB，最大 16 MiB，消息会完整缓存，不能代替无限长度文件流。同一连接允许一个读取者与一个发送者并行，不支持重叠读取或重叠发送。

常规 RFC6455 Upgrade 使用 HTTP/1.1，TLS 配方为适配 WebSocket 调整 ALPN，不等同于完整浏览器握手。不支持 WebSocket 压缩、HTTP/2/3 扩展 CONNECT 或自动重连。握手、读写和关闭使用独立秒制参数 `handshake_timeout`、`read_timeout`、`write_timeout`、`close_timeout`，不以 HTTP 总超时限制连接寿命。详见 [WebSocket](https://github.com/FastTLS/fastls-ffi/blob/main/WEBSOCKET.md)。

## 配置、错误与资源管理

- **超时**：默认 HTTP 总时限 30 秒，可设置 `Timeout(total=300, connect=10, headers=20, read=30, write=30)`；所有值单位为秒。total 必须大于 0 且不超过 86400，不能用 `None` 关闭；其它预算默认 `None`。read/write 为对应阶段的进度停顿预算，不是完整正文的累计时长。
- **错误**：`FastlsTimeoutError.phase` 标识超时阶段；`FastlsDecodeError` 表示解码失败；`FastlsHTTPError` 由 `raise_for_status()` 抛出并保留 `.response`。它们均继承 `FastlsError`；参数校验及 JSON 解析也可能抛出标准 Python 异常。
- **Cookie / 连接池**：Session 默认自动管理 Cookie，使用 `session.cookies.get_dict(url)`、`set(name, value, url=...)`、`delete(name, url=...)`、`clear()`；`cookies=False` 禁用自动管理。不同 Session 的 Cookie 与连接池隔离，`close_idle_connections()` 释放空闲连接。
- **TLS**：默认校验证书与主机名，没有 `verify=False`。`ca_file` 指定目标根证书，文件中的根替代系统根；只影响本 Session，不修改系统信任。
- **代理**：会话或单次请求传 `proxy="http://127.0.0.1:8080"`；支持 HTTP/HTTPS CONNECT、SOCKS5、SOCKS5h。单次 `proxy=None` 显式直连，不读取环境代理。`proxy_ca_file` 只设置 HTTPS 代理根证书，与目标 `ca_file` 分离。详见[代理](https://github.com/FastTLS/fastls-ffi/blob/main/PROXY.md)。
- **关闭**：Session、响应、SSE 和 WebSocket 都应使用对应同步/异步上下文；提前退出也要关闭。`response.cancel()` 取消请求，Session 关闭取消所属活动请求。

这里的 URL 为示例占位，上传、SSE、WebSocket 和全双工需要目标服务提供对应协议。SDK 不承诺完整兼容 requests/httpx，也不开放完整自定义 TLS Spec。

## 源码开发与许可证

SDK-only sdist 不包含动态库，也不会在安装或导入时下载、编译它。源码开发需先按[构建说明](https://github.com/FastTLS/fastls-ffi#本地构建)生成库，在仓库根目录执行 `python -m pip install -e ./python`。独立源码安装需自行提供匹配 SDK 所需能力的 ABI v1 库；仅 ABI 版本相同不代表所有新增能力可用。

加载顺序为 `Session(library="绝对路径")` → `FASTLS_LIBRARY` → wheel 内动态库 → 源码开发目录 `dist/`。普通 wheel 用户无需设置库路径；遗留环境变量可能覆盖新安装的库。

项目许可证为 [Apache-2.0](https://github.com/FastTLS/fastls-ffi/blob/main/python/LICENSE)，第三方原生依赖许可证见 [THIRD_PARTY_LICENSES.txt](https://github.com/FastTLS/fastls-ffi/blob/main/python/THIRD_PARTY_LICENSES.txt)。两份文件随发行包提供。正式发布过程见 [PyPI 维护说明](https://github.com/FastTLS/fastls-ffi/blob/main/PYPI.md)。
