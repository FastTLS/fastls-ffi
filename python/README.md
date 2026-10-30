# fastls Python SDK

Python 0.5.0 增加完整 Spec 的 H2 首流编号和初始流窗口配置，省略时保留旧行为；需要 `http2.stream.v1` 和原生 `http2_stream_v1`。JA3/H2 转换、Bundle/Store 及异步入口保留这些字段，旧原生库明确拒绝。窗口额度及候选采用边界见 [中文契约](../FINGERPRINTS.zh-CN.md)。

Python 0.2.0 起支持 Session `address_policy`、`profile_spec=ProfileSpec(...)` 和
`http_version="h3"`，同步/异步入口共用契约。请求也可指定完整 Spec 或显式协议；
策略只在创建 Session 时配置。较旧原生库缺能力时会明确报错。
完整范围和限制见 [能力契约](../CAPABILITIES.md)。

```python
import json
from pathlib import Path
from fastls import Session, ProfileSpec

spec = ProfileSpec(json.loads(Path("browser.json").read_text()))
with Session(profile_spec=spec) as session:
    response = session.get("https://example.com")
with Session(http_version="h3") as session:
    response = session.get("https://example.com")  # 必须由目标支持 H3
```

通过原生 Fastls 动态库发送带浏览器配置的 HTTP 请求，支持同步、asyncio、流式上传下载、HTTP/2 全双工、SSE 和 WebSocket。Python 发行包名与导入名均为 `fastls`，仓库名为 `fastls-ffi`。本文对应 Python **0.4.0**，发行文件与版本状态以 PyPI 为准。

## 安装与平台

需要 Python 3.10+。推荐在虚拟环境中安装：

```shell
python -m pip install --only-binary=:all: fastls==0.4.0
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

## 直接发起请求

> 0.1.4 新增模块级便捷方法；使用 0.1.3 或更早版本时，请先升级，或使用下方的 `Session` 示例。

```python
import fastls

resp = fastls.request("GET", "https://tls.peet.ws/api/all")
resp.raise_for_status()
print(resp.json())

# GET 的简写：
resp = fastls.get("https://tls.peet.ws/api/all", timeout=15)
print(resp.status_code, resp.text)
```

提供 `request()`、`get()`、`post()`、`put()`、`patch()`、`delete()`、`head()` 和 `options()`。请求参数沿用 `Session.request()`，例如 `params`、`headers`、`json`、`data`、`files`、`profile`、`proxy` 和 `timeout`。`library`、`ca_file`、`cookies` 与 `notifications` 用于配置本次临时会话，其中 `cookies` 是是否自动管理 Cookie 的布尔开关。

这些入口附带完整关键字参数提示，返回类型为 `Response`；IDE 可识别 `.status_code: int`、`.text: str`、`.content: bytes` 和响应头类型。`.json()` 的结果取决于目标返回的 JSON，保留为 `Any`。类型提示支持 Python 3.10+，类型提示本身无需额外运行依赖；Cookie 公共后缀检查使用随包安装的 `publicsuffixlist`。

默认每次调用创建临时会话，完整缓存响应后关闭请求与会话；返回后仍可读取 `.content`、`.text`、`.json()` 和响应头。HTTP 4xx/5xx 默认返回响应，由 `raise_for_status()` 决定是否抛错。`head()` 默认不跟随重定向，与 requests 的习惯一致；可设置 `allow_redirects=True`。

各次调用之间不保留 Cookie 或复用连接。连续请求、登录态、流式下载、SSE 或 WebSocket 请使用 `Session`；异步可选 `ClientSession` 或已有 `AsyncSession`。普通响应受 32 MiB 缓存上限约束；`stream=True` 返回未缓存响应，必须通过 `with` 或 `close()` 关闭。也可继续使用 `Session.stream()`。

## 设置浏览器请求头

Python 0.1.4 / Node 0.10.1 所用的默认 `chrome/156/windows` 配置没有为 `Accept-Encoding`、`Accept-Language` 和 `Priority` 设置默认值。下面的示例显式补充它们；其它配置的默认值可能不同。

```python
import fastls

response = fastls.get(
    "https://tls.peet.ws/api/all",
    profile="chrome/156/windows",
    headers={
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "Priority": "u=0, i",
    },
)
response.raise_for_status()
print(response.json())
```

`Accept-Language` 表示语言偏好，请按应用需要调整；`Priority` 的值也与请求场景有关，示例不是所有浏览器请求的固定模板。请求头顺序配置只排列已有字段，不会为其中的字段自动生成值。支持自动解压 gzip/deflate/br/zstd，不等于会自动发送对应的 `Accept-Encoding`。显式传入的头值优先于配置默认值；复用会话时，也可通过 Session 的 `headers` 设置。

## 从 requests / aiohttp 迁移

同步代码通常可把 `import requests` 改为 `import fastls as requests`；`get`、`Session`、`params/json/data/files`、`status_code/text/content/json()` 和 `raise_for_status()` 保持熟悉的写法。流式响应也可使用：

```python
import fastls

with fastls.get("https://example.com/data", stream=True, profile="chrome/156/windows") as response:
    response.raise_for_status()
    for chunk in response.iter_content(65536):
        print(len(chunk))
```

异步推荐 `ClientSession`，支持 `async with session.get(...)` 和异步读取：

```python
import asyncio
import fastls

async def main():
    async with fastls.ClientSession(profile="chrome/156/windows", timeout=30) as session:
        async with session.get("https://tls.peet.ws/api/all") as response:
            response.raise_for_status()
            print(response.status, await response.json())

asyncio.run(main())
```

`await response.read()/text()/json()` 与 `response.content.iter_chunked(n)` 是该入口的常用读取方式；`response.release()` 或 `await response.wait_for_close()` 可显式释放。`ClientSession.close()` 为异步方法。已有 `AsyncSession` 保留其缓冲响应、`aread/aiter_bytes`、双工、SSE 和 WebSocket 接口。

兼容以常用流程为目标，不承诺完整替换：认证接受 BasicAuth、BearerAuth 或用户名/密码元组；不接受 hooks/adapters/verify=False，也不自动读取环境代理。没有 aiohttp 的连接器、TraceConfig 或完整 StreamReader API。超时使用 Fastls 的秒数或 `Timeout`，证书使用 `ca_file`，代理使用 `proxy`。逐请求 Cookie 映射使用 `request_cookies={...}`；原有 `cookies: bool` 继续控制会话自动管理。Cookie 以 URL 范围访问，不是完整 RequestsCookieJar 兼容对象。未支持的关键字会报错。

## 认证、可选重试与离线配置

Python 0.3.0 新增 BasicAuth/BearerAuth、逐请求 request_cookies、Cookies.update、RetryPolicy 和 body_factory。同步、异步及兼容入口使用一致契约；新增重试默认关闭，取消、策略/证书拒绝、不可重放正文和响应正文读取错误不重试。ProfileBundle/ProfileStore 与 `python -m fastls profile` 提供离线校验、导入导出、固定和回滚。

完整参数、优先级、限制与示例：[中文指南](https://github.com/FastTLS/fastls-ffi/blob/main/RELIABILITY.zh-CN.md) · [English contract](https://github.com/FastTLS/fastls-ffi/blob/main/RELIABILITY.md)。

## 会话职责与重定向

Python 管理默认参数、Cookie、重定向策略和响应历史；动态库只保留传输上下文、连接池、TLS/指纹、证书校验及底层收发。Node 使用独立的 JavaScript Cookie 罐，不与 Python 共享应用会话。关闭会话会清空 Cookie 并释放其底层资源；不写入磁盘。

默认最多跟随 30 次重定向，可用 `max_redirects` 调整，或 `allow_redirects=False` 返回当前响应。总超时跨跳转累计；跨来源移除敏感请求头，再按目标 URL 选择 Cookie。307/308 不重放一次性流式上传。`response.history` 保存之前的响应，正文已读取且请求已关闭。

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

请求可设置 `headers`、`params`、`json`、`data`、`files`、`trailers`、`timeout`、`allow_redirects` 和单次 `profile`。任意方法使用 `session.request(method, url, ...)`；便利方法包括 `get/post/put/patch/delete/head/options`。JSON 示例：

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

SDK-only sdist 不包含动态库，也不会在安装或导入时下载、编译它。源码开发需先按[构建说明](https://github.com/FastTLS/fastls-ffi/blob/main/DEVELOPMENT.md)生成库，在仓库根目录执行 `python -m pip install -e ./python`。独立源码安装需自行提供匹配 SDK 所需能力的 ABI v1 库；仅 ABI 版本相同不代表所有新增能力可用。

加载顺序为 `Session(library="绝对路径")` → `FASTLS_LIBRARY` → wheel 内动态库 → 源码开发目录 `dist/`。普通 wheel 用户无需设置库路径；遗留环境变量可能覆盖新安装的库。

项目许可证为 [Apache-2.0](https://github.com/FastTLS/fastls-ffi/blob/main/python/LICENSE)，第三方原生依赖许可证见 [THIRD_PARTY_LICENSES.txt](https://github.com/FastTLS/fastls-ffi/blob/main/python/THIRD_PARTY_LICENSES.txt)。两份文件随发行包提供。正式发布过程见 [PyPI 维护说明](https://github.com/FastTLS/fastls-ffi/blob/main/PYPI.md)。


[在线完整文档](https://fasttls.github.io/python/)

## 使用边界与免责声明

仅在拥有权限的系统、数据和网络上使用，并遵守适用法律、服务条款及隐私要求。软件按许可证约定以“现状”提供，不承诺可用性、特定用途适用性、第三方服务放行、匿名性或与真实浏览器完全一致。浏览器名称和版本用于描述配置，不表示获得相应厂商认证或背书。部署方负责访问控制、密钥和个人信息保护、请求限额及数据备份；担保排除与责任限制以随附许可证及适用法律为准。本说明不改变许可证授予的权利或新增许可限制。

`ProfileSpec.from_fingerprints(base, ja3=..., h2=...)` 与 `python -m fastls profile adapt` 支持离线配置转换，生成的标准 Spec/Bundle 可用于已有同步、异步及 ProfileStore 接口。详细[中文说明](https://github.com/FastTLS/fastls-ffi/blob/main/FINGERPRINTS.zh-CN.md) · [English contract](https://github.com/FastTLS/fastls-ffi/blob/main/FINGERPRINTS.md)。
