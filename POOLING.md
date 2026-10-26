# 可复用会话与连接池

Python 的 Session、AsyncSession 和 ClientSession 各自拥有原生传输上下文；Cookie、默认参数和重定向由语言 SDK 管理。相同会话、相同profile及相同CA配置的请求共享传输层；HTTP/1.1允许keep-alive，HTTP/2允许在一条连接上并发处理多个响应。不同Session不共享连接。

```python
from fastls import Session

with Session(profile="chrome/156/windows") as session:
    first = session.get("https://example.com/a")
    second = session.get("https://example.com/b")
    # 可主动释放空闲连接，后续请求会重新建立连接。
    session.close_idle_connections()
```

调用方式和profile选择不变。会话默认头仍在Python逐请求合并，自定义请求头不改变协议配置；首次请求的UA、Cookie或其它头不会成为连接池的默认头。

## 生命周期与隔离

- 读取到正文结束或关闭响应后，连接可在协议允许时复用。关闭一个HTTP2响应只取消该流，不关闭其它活动流。
- Session.close取消该会话的活动请求、释放请求登记并关闭空闲连接；活动请求清理完成后释放其剩余连接。重复关闭无害，关闭后拒绝新请求。
- close_idle_connections只处理空闲连接，不打断活动响应；AsyncSession也可直接调用此方法。
- profile ID与CA PEM内容摘要共同决定会话内的传输配置；不同配置不会复用彼此的连接。每个配置按协议、主机和端口区分来源，不进行跨域HTTP2连接合并。
- 请求超时继续作为包含响应体读取在内的总截止时间。等待协议发现和HTTP2共用建连时也可取消；取消一个等待者不会取消其它等待者，最后一个等待者退出时才取消共用建连。

## 有界资源

原生运行时最多64个未关闭会话、256个未关闭请求。每个会话最多16种profile/CA配置，每种配置最多128个来源；达到限制时明确报错，不悄悄复用不匹配的配置。释放整个Session可清空这些配置与来源登记，close_idle_connections不清除配置登记。

每个HTTP1来源最多保留8条空闲连接；HTTP1和HTTP2的空闲连接超时为90秒。活动并发受请求句柄上限约束，8不是活动连接上限。HTTP2的TCP/TLS建连另有30秒上限，并受等待者取消和会话关闭控制。

## C ABI兼容

ABI版本仍为1，新增三个JSON返回函数，返回内存仍由fastls_free释放：

- fastls_transport_create() 返回不保存应用 Cookie 的 uint64 传输上下文句柄，供当前 SDK 使用；fastls_session_create() 保留给历史 C 客户端。
- fastls_session_close(handle)幂等关闭会话及其请求。
- fastls_session_close_idle(handle)释放该会话的空闲连接。

fastls_start的JSON新增可选session字段。不传或传0时维持原来的单次请求模式；旧C调用方无需修改。会话句柄与请求句柄属于不同类型，不可混用。诊断统计增加open_sessions及session_pools。

Python 0.1.4 还要求 fastls_transport_create 和 transport_context_v1 能力；遇到旧库会明确提示更新，不能只升级wheel而继续加载旧DLL。

## 验证

核心Go回归验证H1/H2连接复用、主动关闭空闲连接后重连、关闭后拒绝请求。ftransport的共用建连取消隔离及恢复测试纳入race检查。

tests/test_pooling.py的6项真实动态库测试通过独立Go靶场分配的连接ID验证三种协议的顺序复用、Session/profile/CA隔离、首次请求头不残留、24路HTTP2共用一条连接并取消一半、活动流期间关闭空闲连接、会话回收及建连等待者的独立截止时间。源码和wheel均为六组原生CI的必需检查；同时重跑基础、稳定性、网络异常及Linux300秒持续测试。

相关功能见 [Cookie 及多值头](COOKIES_HEADERS.md)、[流式解码及分层超时](STREAMING_TIMEOUTS.md)、[流式上传](STREAMING_UPLOAD.md) 和 [WebSocket](WEBSOCKET.md)。这些功能已经实现；安装版本与发布状态见 [安装指南](INSTALL.md)。
