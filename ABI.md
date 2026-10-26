# C ABI v1


以构建生成的 `fastls.h` 为准确声明。接口均为 C 调用约定：

- `fastls_abi_version()`：当前为1。
- `fastls_transport_create()`：0.1.4 新增，返回不含应用 Cookie 状态的传输上下文句柄，供 Python/Node SDK 使用；以 `fastls_session_close/close_idle` 和通知函数管理其网络资源。SDK 发送单跳请求并自行处理重定向。
- `fastls_session_create/cookies()`：保留给历史客户端，新 SDK 不使用。对 transport_create 创建的句柄调用 session_cookies 会明确报错。扩展能力标记为 `transport_context_v1`，不改变 ABI 号。
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


详见 [通知](NOTIFICATIONS.md)、[连接池](POOLING.md) 和构建产物 fastls.h；新增能力需逐项检测，ABI 号相同不保证全部扩展可用。
