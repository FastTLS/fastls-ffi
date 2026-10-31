# H3 受控配置：Python 0.7.0 / Node 0.16.0

SDK 固定采用 Go v0.2.0，原传输、profile 和生成器 pin 保持。新增可选能力 http3_config_v1，以及两个 ABI-v1 查询符号；transport JSON schema_version 保持1。旧用法继续兼容，旧原生库缺能力或查询符号时，在 owner/请求分配前报告 unsupported_capability。

```python
from fastls import Session, HTTP3Config, http3_capabilities
config = HTTP3Config(initial_stream_receive_window=65536,
    max_stream_receive_window=1048576,
    initial_connection_receive_window=131072,
    max_connection_receive_window=2097152,
    max_idle_timeout_ms=30000, keep_alive_period_ms=0,
    max_response_header_bytes=65536)
with Session(http_version="h3", http3_config=config) as session:
    print(dict(session.effective_http3_config()))
    response = session.get("https://your-h3-origin.example/")
    response.raise_for_status()
print(http3_capabilities()["fields"])
```

请使用获准且实际支持H3的地址。AsyncSession/ClientSession 使用同一创建选项，顶层 get/request 可以与 http_version="h3" 一起配置并持有临时会话。Node 对应 HTTP3Config 驼峰字段、Session({httpVersion:'h3',http3Config:config})、effectiveHTTP3Config() 和 http3Capabilities()，独立 fetch 同样支持创建配置。完整 Node 示例见 [English contract](H3_CONFIG.md)。Session.fetch 和逐请求选项不能改写配置。

HTTP3Config 是不可变强类型对象，不接受普通字典/对象替代；输入会复制，to_dict/toJSON 返回副本，查询结果不能改 owner。Python 返回不可变映射，Node 返回冻结对象；auto owner 的查询为 None/undefined，关闭后的查询失败。查询不联网，有效值仅表示本地规范化，不是对端协商结果。

- 流初始/最大窗口默认 512KiB/6MiB，连接初始/最大窗口默认 512KiB/15MiB；四项零或省略选默认，显式允许4096..67108864字节，规范化后初始不大于对应最大值。它们限制未消费的流控额度，不是累计传输/进程内存上限。
- max_idle_timeout_ms / maxIdleTimeoutMs：零或省略为30000，显式100..86400000整毫秒。属于本地握手后空闲限制；后端恢复及对端时限影响实际时间，固定后端解析收到的对端 idle 至少按5000ms显示。
- keep_alive_period_ms / keepAlivePeriodMs：省略为 min(10000, 本地idle/2)，显式零关闭周期保活，其它值10..idle/2。输入为整毫秒；当idle为奇数时，有效默认可能是半毫秒，如101->50.5，保留Go行为不截断。关闭周期保活仍可能有QUIC恢复/PTO PING。
- max_response_header_bytes / maxResponseHeaderBytes：零或省略为10485760，显式1024..16777216字节。后端限制各编码HEADERS帧及初始解码字段（含字段开销），响应Trailer只有编码帧限制，没有同等解码合计上限。不是正文/解压/总内存限额。

Python/ABI使用蛇形名称，Node使用驼峰。所有输入数值都是整数，时间单位是毫秒；布尔、字符串、非有限数、小数、负数和溢出拒绝。原生JSON拒绝未知/重复/大小写别名与null字段。Python保活None/Node undefined表示省略，不编码成JSON null。

配置只接受显式H3创建；H1/H2 Spec、代理、头序、请求Trailer、双工与WebSocket冲突保持。证书/SNI、可信地址策略、批准的数字UDP目标及迁移限制不改变，SDK仍启用证书验证。保活不延长请求总/阶段期限，正文读取和 max_decoded_bytes 独立设置。

C调用 fastls_transport_create_with_options，输入 schema_version=1、http_version=h3 和 http3_config 七项；fastls_http3_capabilities(void) 返回逐字段信息，fastls_transport_http3_config(uint64_t handle) 返回固定H3 owner的有效值，auto owner返回null，零/关闭句柄拒绝。返回值沿用已分配JSON envelope，调用 fastls_free 释放；普通请求不能注入创建配置，不暴露任意QUIC/TLS对象或回调。

五平台源码/实际安装、Linux race与原五分钟资源阈值集中验收；新增真实对端窗口/idle观测、2MiB正文、头部/Trailer拒绝、policy组合、异步/一次请求、旧库和取消/回收证明。固定Go发行已有的窗口增长/空闲/保活证明按其来源复用，同后端fixture不冒充独立协议栈。包含Go的头部错误连接通知对端关闭修正，不把此交付写成浏览器H3指纹、任意SETTINGS/QPACK/头序、0-RTT、UDP代理、新平台或服务字段接入。
