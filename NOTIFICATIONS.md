# 原生状态通知与等待

Python同步请求改用原生状态等待，asyncio会话使用共用的本机唤醒通道。响应头到达、正文块可读、上传队列释放、错误或取消会唤醒对应请求；唤醒后仍由原有C接口获取状态、复制数据。ABI版本保持1，新增能力标记`session_notifications`。

## Python使用

原有请求代码可以继续使用，`notifications="auto"`为默认值：在配套动态库支持通知时启用，否则使用轮询。Python 0.1.4 始终要求 SDK 传输上下文能力；通知回退不意味着任意旧 ABI-v1 库都可使用。可以显式选择：

```python
from fastls import Session, AsyncSession

with Session(profile="chrome/156/windows", notifications="required") as session:
    print(session.notification_mode)  # native-wait
    response = session.get("https://example.com/")

async def receive(url):
    async with AsyncSession(profile="chrome/156/windows",
                            notifications="required") as session:
        print(session.notification_mode)  # async-events
        async with session.stream("GET", url) as response:
            async for chunk in response.aiter_bytes():
                await consume(chunk)
```

- `auto`：在满足当前 SDK 必需能力的前提下，按通知能力选择原生等待或轮询。
- `required`：缺少通知能力时明确报错，适用于验收和部署版本门禁。
- `poll`：显式使用原有轮询，适用于排障或不允许本机连接的环境。

新库的通道初始化失败或运行中断开会报错并唤醒等待者，不默默退回轮询。可以关闭会话后改用`notifications="poll"`新建会话。

`async with`进入时完成通道初始化，随后并发请求共享它；直接使用AsyncSession而不进入上下文时，在首次请求前初始化。初始化有独立的3秒限制，HTTP总预算从原生请求创建后开始。AsyncSession的通知绑定创建它的事件循环，不能跨循环复用；应在同一循环中`await session.aclose()`或退出上下文。

同步等待会释放GIL，并在状态变化时立即返回；每次最多等待1秒，使调用线程能定期处理Python信号。asyncio等待无需定时查询状态。两种模式均不增加Python工作线程，也不使用Go→Python函数指针回调。上传、SSE、HTTP/2全双工和原有超时/取消接口继续适用。

## 通道与有界资源

每个活跃AsyncSession只有一条IPv4本机TCP控制连接和一个Python接收任务。监听地址固定为127.0.0.1的临时端口，连接经32字节随机令牌认证后关闭监听器；同时握手的候选连接最多2个。控制连接只传握手及唤醒字节，HTTP状态和正文仍通过C ABI传递。

使用本机TCP是为了兼容Windows默认事件循环及POSIX平台的标准asyncio流接口。每个原生会话有一个发送协程、一个容量为1的唤醒队列，以及最多256个合并后的请求ID；通知合并不会累积完整事件历史。集合溢出会要求消费方重查本会话的活动请求。关闭会话会取消请求并释放控制连接；候选握手尚未执行就被取消时也会主动关闭连接。

原生同步等待在检查状态前取得变化代次，避免检查与等待之间漏掉唤醒。Python异步等待同样比较代次，多个等待者共用future且独立取消，供全双工上传/下载使用。通知是“请重查状态”的提示，不能作为读写一定成功的保证。

## C ABI v1增量

以构建生成的fastls.h为准确声明：

```c
int fastls_wait(uint64_t request, uint32_t events, uint32_t timeout_ms);
char *fastls_session_watch(uint64_t session, uint16_t port,
                          void *token, size_t token_length);
char *fastls_session_events(uint64_t session);
char *fastls_session_unwatch(uint64_t session);
```

`fastls_wait`的events位标记为1=响应头、2=正文可读、4=上传可写，可组合。错误、关闭或结束也可能使操作就绪；返回后应重新调用info/read/write。timeout_ms允许0..60000，0为立即检查。返回0表示就绪，1表示等待超时，-1为内部失败，-2为非法句柄或参数。此函数允许阻塞当前调用线程，不能直接用于阻塞事件循环。

异步绑定先自行建立本机监听器，再调用session_watch。port必须为非零端口，令牌恰为32字节，原生库立即复制令牌，不保留调用方指针；原生后台连接固定127.0.0.1并发送这32字节握手，之后发送值为1的唤醒字节。注册必须在该会话没有活动请求时进行，重复注册报错。

收到唤醒后调用session_events，结果形如`{"ok":true,"result":{"handles":[123,124],"overflow":false}}`。返回集合后清空登记；忽略已经关闭的请求ID。overflow为true时须重查本会话的全部活动请求。网络缓冲可能仍有较早的合并唤醒，允许空集合或虚假唤醒。session_unwatch停止登记且幂等，session_close也自动清理。所有返回JSON仍需fastls_free释放。

跨语言使用者可以选原生阻塞等待，或将控制连接接入自己的事件循环。Node SDK 0.5已接入这组接口：默认auto、显式required/poll，notificationMode返回async-events或poll。同步构造后首次请求自动初始化，或使用await Session.open()/session.ready()；手动duplex在初始化完成后才创建请求。20项Python通知验收均有Node对应项，语言差异见[node/PARITY.md](node/PARITY.md)，用法见[node/README.md](node/README.md)。

## 检查范围

`tests/test_notifications.py`包含20项真实库验收：空闲正文及响应头不周期查询、无新增Python线程、GIL释放、1000次检查/等待竞态、共享等待者独立取消、128路混合请求、会话隔离、首次并发初始化、握手前取消、认证失败与重试、上下文进入失败清理、跨线程关闭、通道断开、溢出重查、旧库能力门禁、显式poll、跨循环拒绝及25次会话回收。

Go另检查就绪/超时/EOF/取消、队列释放、集合容量、合并与溢出、令牌握手和会话所有权，并纳入race。`examples/c/wait.c`由独立C进程通过新接口读取并校验64KiB二进制正文。源码/wheel各20项与C消费者加入六组原生Actions，原有全部回归及Linux300秒持续测试继续执行；证据为`build/stability-notifications-*.json`与`build/stability-c-wait-*.json`。

这些检查是回归与资源上限验证，性能结论应以具体工作负载测量为准。WebSocket 和平台分发已经实现，分别见 [WebSocket](WEBSOCKET.md) 与 [安装指南](INSTALL.md)。
