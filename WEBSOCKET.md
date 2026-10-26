# WebSocket：C 与 Python 开发版接口

实现常规 RFC6455 `ws://` / `wss://` 客户端，Python 同步、异步与 C 共用原生消息句柄。不是 SSE 或 HTTP/2 双工的别名。

```python
from fastls import Session

with Session(profile="chrome/156/windows") as session:
    with session.websocket("wss://example.com/chat",
                           headers={"Origin": "https://example.com"},
                           subprotocols=["chat.v1"]) as ws:
        ws.send("你好")               # str → text；bytes-like → binary
        message = ws.recv()            # 完整消息，返回 str 或 bytes
        ws.ping(b"health")             # 发送完成，不代表已收到 pong
```

```python
import asyncio
from fastls import AsyncSession

async def main():
    async with AsyncSession(profile="chrome/156/windows") as session:
        async with session.websocket("wss://example.com/chat") as ws:
            await ws.send(b"binary payload")
            message = await ws.recv()

asyncio.run(main())
```

`ws_connect()` 直接返回连接（异步版需 await），用于手动管理生命周期；对应 `close()` / `await aclose()`。上下文退出会尝试关闭握手。`abort()` / `cancel()` 立即中断，丢弃未读/未发送数据。

## 参数和协议边界

- 会话默认 `profile`、自定义头、目标 `ca_file` 和代理配置继承到握手；支持请求级 `profile`、`headers`、`params`、`proxy`、`proxy_ca_file`。`proxy=None` 显式直连。HTTP/HTTPS CONNECT、SOCKS5 本地 DNS、SOCKS5h 代理 DNS 复用已验收的隧道实现。
- `wss` 始终验证证书和主机名。HTTPS 代理与目标的 CA 独立。新 SDK 路径使用核心的 `DialWebSocketContext`，不使用旧 Go WebSocket 构造器。
- Chrome 等选择器继续选择对应 TLS 配方和默认身份头；为常规 Upgrade，ALPN 仅提供 `http/1.1`，移除 h2 的 ALPS 扩展，Upgrade 头顺序由 Gorilla 管理。原始 profile match 记录表示选择结果，不表示修改后的握手与浏览器完全一致。`info["websocket"]["alpn_policy"]` 记录此适配；服务器可不协商 ALPN，此时仍是 HTTP/1.1。
- 支持 text/binary、空消息、入站分片重组、分片间控制帧、自动回应 ping、主动 ping/pong，以及关闭握手。`subprotocols` 是最多 16 个合法 token 的列表/元组；`.subprotocol` 返回服务器选择。拒绝未提供的子协议/扩展。
- 首版不支持 permessage-deflate、其它压缩扩展、HTTP/2/3 扩展 CONNECT、自动重连、自动重放、握手重定向或环境代理。握手响应最多读取 64 KiB。
- 动态库提供 `websocket_cookies_v1` 时，握手自动共享 Session 的 HTTP Cookie 罐，`ws/wss` 分别按 `http/https` 规则匹配域名、路径、Secure 和有效期。显式 `Cookie`（包括空值）完整覆盖本次自动发送，不合并；`cookies=False` 禁止自动读写，手动认证头仍可使用。成功和失败握手响应的多个 `Set-Cookie` 均按原 URL 写回，与 HTTP 非成功响应一致；不跟随重定向。旧库仅有 `websocket_v1` 时仍需手动传递 Cookie。
- Origin 由调用方设置。`Connection`、`Upgrade`、`Sec-WebSocket-*`、长度/传输头及代理认证头由库管理，不能手动覆盖。

## 资源与并发

- `max_message_bytes` 缺省 1 MiB，可设 1..16 MiB，按 UTF-8 字节及重组后的完整消息计数；超限、非法 UTF-8 或协议错误使连接终止。此限制同样约束出站 data 消息，ping/pong/close 独立限制为 125 字节。
- 消息 API 会有界地缓存完整消息；不是无限长度文件流接口。原生端最多持有一条未交付队列消息、一条读取/等待入队消息和一条正在通过 C 分块交付的消息；发送最多接受一条消息。Python `recv()` 另缓存当前消息。计数不含协议栈、分配器余量、OS 缓冲或业务持有的数据。
- 慢消费者通过有限队列向网络施加背压。队列阻塞期间，后续控制帧也可能等待消费；关闭握手会解除入队阻塞。与 HTTP 请求共用全局最多 256 个未关闭句柄的限制，使用后必须关闭。
- 一条连接允许一名调用方读取和一名调用方发送，两者可同时进行。重叠 `recv` 或重叠 `send/ping/pong` 立即报错，不隐式积压消息；业务可自行串行化写入。原生自动 pong/close 回复可与应用写入并行。
- Python 不创建网络工作线程。同步等待复用 `fastls_wait`，异步等待复用每会话一条通知连接；`notifications="poll"` 保留兼容路径。取消某条连接不影响同会话其它 WS 或 HTTP 请求；会话关闭中断全部所属句柄。

## 超时与关闭

`handshake_timeout=30` 覆盖连接、代理、TLS 和 HTTP Upgrade；`read_timeout=None` 缺省不限，指定后是每条消息的网络读取预算；`write_timeout=30` 是每条消息写入预算。单位为秒，不把会话的 HTTP 总超时当作 WS 连接寿命。读写超时后连接终止，抛出 `FastlsTimeoutError`，`.phase` 为 `handshake/read/write`。

`close_timeout=1`（最多 60 秒）约束整个关闭握手；超时则直接中断。`close(code=1000, reason="")` 接受合法发送状态码，reason 最多 123 个 UTF-8 字节。`recv()` 在正常收到 close 后抛出 `WebSocketClosed`，提供 `.code/.reason`；连接对象保留 `.close_code/.close_reason`。异常断线或无 close 帧时默认代码 1006，原生错误仍通过 `FastlsError` 传播。

`ping()` 只等待 ping 写入完成，`info["websocket"]["pongs"]` 和 `last_pong_base64` 可观察已接收的 pong，不提供自动心跳或 ping RTT 超时策略。

## C ABI v1 增量

消息能力标记 `websocket_v1`，共享 Cookie 能力标记 `websocket_cookies_v1`；ABI 仍为 v1，生成的 `dist/fastls.h` 包含三个新增函数：

```c
char *fastls_ws_start(char *json, size_t length);
int fastls_ws_send(uint64_t handle, int opcode, void *data, size_t length);
int fastls_ws_read(uint64_t handle, void *buffer, size_t capacity,
                   size_t *length, int *opcode, int *end);
```

`start` 使用现有 `{ok,result/error}` JSON 信封，调用方用 `fastls_free` 释放。输入字段见 `internal/bridge/websocket.go` 的 `WebSocketInput`，毫秒预算与消息上限的默认值和 Python 相同。

`send` opcode 为 1/text、2/binary、8/close、9/ping、10/pong；0 表示原生已复制并接受一条消息，1 表示未消费任何数据，需要等待可写再重试，负数为错误。接受不等于写入网络完成，使用 `fastls_info.websocket.write_pending` 和 `fastls_wait(handle,4,...)` 等待完成。close 负载为两字节网络序状态码加 UTF-8 reason。

`read` capacity 为 1..65536；0/data、1/pending、2/EOF、负数/error。每段给出消息 opcode，`end=1` 表示消息结束，包括 `length=0` 的空消息。不得把消息结束当作连接 EOF。控制帧由原生端处理，不出现在该读取接口。

继续复用 `fastls_info/wait/cancel/close`、会话通知及会话关闭。HTTP 的 `fastls_read/write` 不可用于 WS 句柄。C 调用方必须保证单读者和单写入序列，并在释放句柄前协调并发调用。独立 C 示例见 `examples/c/websocket.c`。

协议依据：[RFC6455](https://www.rfc-editor.org/rfc/rfc6455.html)。消息实现基于仓库已固定的 [Gorilla WebSocket v1.5.3](https://pkg.go.dev/github.com/gorilla/websocket@v1.5.3)，额外实施 UTF-8、协商、长度和生命周期检查。测试靶场独立手写帧，不复用客户端的 WebSocket 库。

Node SDK已合并：Promise接口与HTTP共享Cookie，支持cookieMode:manual。原生websocket_cookie_control_v1保证禁用自动收发及无效101不写回；历史能力标记保留，仅有websocket_cookies_v1的旧库拒绝manual。
