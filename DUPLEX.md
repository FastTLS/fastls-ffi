# HTTP/2 全双工

Python可以在上传仍进行时接收响应，继续使用`chrome/156/windows`等配置。需要同时更新Python包与动态库；`fastls_features`增加`duplex_h2`，ABI版本仍为1。

## asyncio

```python
from fastls import AsyncSession, Timeout

async def exchange(url, outgoing, consume):
    async with AsyncSession(profile="chrome/156/windows") as session:
        async with session.stream(
            "POST", url, data=outgoing, duplex=True,
            timeout=Timeout(total=300, write=20, read=20),
        ) as response:
            async for chunk in response.aiter_bytes():
                await consume(chunk)
```

`outgoing`可以是异步bytes迭代器。收到响应头后立即返回响应对象，上传任务继续运行；读取和上传共享会话生命周期。SDK不增加Python工作线程，也不从Go调用Python回调。同步文件或迭代器仍在事件循环线程执行，耗时生产者应提供异步迭代器。

`await response.wait_upload()`等待生产者结束且上传EOF被原生队列接受，不代表服务器已经收到或处理完请求。等待期间仍需并发消费响应，否则有界缓冲可能造成双方等待。`await session.post(..., duplex=True)`也支持该模式，但会缓存响应，受32MiB上限约束。

## 同步手动交互

```python
from fastls import Session

with Session(profile="chrome/156/windows") as session:
    with session.duplex("POST", "https://example.com/exchange") as exchange:
        exchange.send(b"hello")
        response = exchange.response
        chunks = response.iter_bytes()
        first = next(chunks)
        exchange.send(b"next")
        exchange.finish_upload()
        for chunk in chunks:
            consume(chunk)
```

此接口由调用方安排发送、读取顺序，不接受`data`。`send`遇到背压会等待，大块发送可能因尚未消费响应而阻塞；持续双向大流量建议使用asyncio接口。单个响应只允许一个读取者，块边界不等于消息边界。同步`post/stream(duplex=True)`明确报错并提示使用`Session.duplex()`。

## 生命周期与范围

- 只支持指纹HTTP/2传输。HTTP/1.1、未选择指纹的标准传输及HTTP/3明确拒绝，拒绝发生在请求提交给相应HTTP传输之前；TLS协商可能已经进行。代理隧道可以承载目标 H2 双工，配置与限制见 [PROXY.md](PROXY.md)。
- 上传EOF只关闭请求方向，响应可以继续读取。**响应EOF结束整个交换**，取消未完成上传；不支持响应结束后继续发送请求体。
- 全双工模式不自动跟随重定向。3xx/4xx/5xx响应会停止异步上传并返回响应，避免重放或继续发送被拒绝的正文。普通上传默认行为不变。
- 关闭响应、退出上下文、取消读取任务、关闭会话或超时会取消原生请求与上传任务。异步`aclose()`等待生产者清理；上传生产者异常会传递给读取者，避免后台任务静默失败。生产者必须响应任务取消；库无法强行终止用户代码中的同步阻塞或忽略取消的协程。
- `write`预算收到响应头后仍对未完成上传生效，与响应`read`停顿计时独立。`headers`只计完整请求发出后等待响应头的时间；需要限制整个交换时应设置`total`。预算含义见[STREAMING_UPLOAD.md](STREAMING_UPLOAD.md)与[STREAMING_TIMEOUTS.md](STREAMING_TIMEOUTS.md)。

上传桥接队列最多64KiB，下载桥接缓冲最多三个64KiB块；协议栈、TLS、内核与用户对象不在此界限内。上传和下载各自应用背压，不会把完整请求或响应放入无界队列。

## C ABI

`fastls_start`增加`duplex=true`，必须同时设置`stream_body=true`和`disable_redirect=true`。沿用`fastls_write`提交块/EOF、`fastls_info`等待响应头、`fastls_read`读取响应；在响应头到达后仍可写入。每方向只允许一个生产者/消费者，取消、关闭接口可并发调用。写入返回值及内存所有权见[STREAMING_UPLOAD.md](STREAMING_UPLOAD.md)。

## 验证

`tests/test_duplex.py`包含13项真实动态库验收：上传前首条响应、请求半关闭、64MiB双向SHA256与缓冲上限、慢消费者、同步交互且无新增Python线程、生产者后期异常、取消读取及H2并行请求、会话关闭、响应头后的写入超时、持续上传时的独立读取超时、总超时、gzip、提前拒绝/重定向、响应EOF停止上传、空正文及不支持的协议。

源码与wheel用例均加入六组原生Actions。核心另验证响应头后阻塞上传在write超时、响应关闭、会话关闭时解除阻塞，并纳入Linux race。证据写入`build/stability-duplex-*.json`；配置加入与本地通过不等于全部远端平台已通过，应以同一提交的Actions与artifact为准。

Node SDK 提供`stream(..., { duplex: true, body })`以及`session.duplex()`手动交换，对应`send()`、`finishUpload()`、`response.waitUpload()`和异步清理。也支持 SSE 与双工组合；13 项双工、23 项 SSE 和 20 项通知均有对应回归，纳入当前完整 231 项 Node 套件，源码和两种隔离安装形态均执行。接口示例、Promise及生产者取消的差异见[node/README.md](node/README.md)和[node/PARITY.md](node/PARITY.md)。

双工混合持续测试还覆盖同一连接上大量取消后的继续读写。传输层对已取消流的 DATA 处理必须保持本地连接额度不变，并向对端退还对应字节；不能重复增加本地额度，否则正常流最终会等待不会发出的窗口更新。该回归已纳入连接窗口单元测试及Linux race检查。
