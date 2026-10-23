# Python / Node.js SSE 事件流

同步和asyncio会话新增`session.sse(method, url, ...)`，继续支持`chrome/156/windows`等配置，复用现有HTTP/1.1、HTTP/2连接池、Cookie、解压、超时和取消。

```python
from fastls import Session, Timeout

with Session(profile="chrome/156/windows") as session:
    with session.sse("GET", "https://example.com/events",
                     timeout=Timeout(total=3600, read=45)) as events:
        for event in events:
            print(event.event, event.id, event.data)
```

```python
from fastls import AsyncSession, Timeout

async def receive(url):
    async with AsyncSession(profile="chrome/156/windows") as session:
        async with session.sse("POST", url, data='{"stream":true}',
                               headers={"Content-Type": "application/json"},
                               timeout=Timeout(total=3600, read=45)) as events:
            async for event in events:
                if event.data == "[DONE]":
                    break
                await consume(event)
```

`[DONE]`作为普通事件数据返回，是否结束由应用决定。`event.json()`可解析JSON数据，非JSON内容会抛出标准`JSONDecodeError`。事件是不可变的`ServerSentEvent`，字段为`data`、`event`、`id`及可选`retry`；默认事件类型为`message`。

应使用上下文管理器，确保提前退出时释放响应。`events.response`可查看状态、头与Match信息。每个事件流只允许一个消费者；不要同时读取底层`response`正文。

## 协议处理

增量解析遵循[WHATWG事件流解释规则](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation)：按UTF-8解码，非法字节替换为U+FFFD，只去掉流开头的一次BOM；支持任意跨块分割、CR、LF、CRLF、多行data、空data、注释、未知字段与事件类型。

空行结束一个事件；EOF不会提交尚未结束的事件。`id`跨事件保留，空值重置，包含NUL的字段忽略。`events.last_event_id`在完整块结束时更新，只有id而没有data的完整块也能更新它；不完整尾部中的id不会被当作已提交状态。

`events.retry`保留最近合法的服务器重试提示，单位为毫秒，尚未收到时为None。`event.retry`只表示当前块内的有效retry字段。只接受ASCII十进制数字，超过2147483647毫秒的值截到此上限，避免超大整数转换和后续定时器溢出。

正常响应必须为HTTP200且Content-Type为`text/event-stream`，允许MIME参数并始终使用UTF-8。HTTP204返回空事件流；其它状态、错误或重复Content-Type明确报错。压缩流沿用自动解压与完整性检查；关闭解压而收到压缩正文时拒绝解析，校验错误不会被当作正常结束。

请求默认设置`Accept: text/event-stream`与`Cache-Control: no-cache`，请求级headers可覆盖。会话默认的其它头照常保留。

## 手动恢复与超时

本接口是一次请求的事件流，**不自动重连或重放请求**。应用可以保存`events.last_event_id`，再用`last_event_id=saved_id`发起下一次请求：此参数设置`Last-Event-ID`头，并作为新解析器的初始id。禁止NUL、CR、LF，支持显式空字符串。重连次数、延迟、POST是否可重复提交及事件去重均由应用决定。

Session默认total仍为30秒；长连接可显式增加总时限，例如`Timeout(total=3600, read=45)`，再由应用决定何时取消。当前ABI的total范围为大于0且最多24小时，不支持None或关闭总超时。`read`限制网络正文停顿，注释心跳也算网络进度；`total`不会因心跳而重置。关闭会话、取消异步读取任务、`events.cancel()`或退出上下文都会沿用原生请求清理；异步显式关闭可`await events.aclose()`。

`AsyncSession.sse(..., data=source(), duplex=True)`也可在HTTP/2中边上传边接收SSE；上传/响应结束规则与异常传播见[DUPLEX.md](DUPLEX.md)。同步SSE可以发送普通或流式上传正文；同步双向手动交互仍使用Session.duplex接口。

## 内存与验证边界

事件按需解析，无后台事件队列。`max_event_bytes`默认1048576，可设1..67108864；它分别限制原始单行字节数与累计data的UTF-8字节数（包含多行之间的换行），超限关闭请求并报FastlsError。解析器仍会持有id、事件类型、当前块和字符串解码开销，不声称整个进程只使用这一大小。原生下载缓冲继续有界；整个事件流可超过普通缓存请求的32MiB上限。

`tests/test_sse.py`共23项验收，包含所有分割位置与逐字节向量、UTF-8/BOM/行尾、id/retry控制块、精确边界与超大整数、H1/H2真实库、gzip及校验失败、204/错误类型、POST与手动恢复、首事件早到、取消时同连接活动流继续完成、会话关闭、心跳与超时、40MiB事件流与背压，以及SSE和全双工组合。源码和wheel均加入六组原生Actions，证据为`build/stability-sse-*.json`。

Node 0.4提供`await session.sse(method, url, options)`及`for await (const event of events)`；配置使用`maxEventBytes`、`lastEventId`和毫秒制`timeoutMs`，状态使用`lastEventId`，显式异步关闭为`closeAsync()`。23项Python SSE验收均有对应Node用例，源码与隔离npm安装包执行相同151项完整套件，包含40MiB事件流哈希、取消和双工组合。Promise与同步接口的差异记录在[node/PARITY.md](node/PARITY.md)，示例和生命周期见[node/README.md](node/README.md)。

SSE属于语言便利接口，无新增C符号或ABI版本变化，不包含浏览器EventSource对象、自动重连或WebSocket。
