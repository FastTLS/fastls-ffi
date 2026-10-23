# 流式上传

Python同步、异步会话现在可以逐块发送请求体，保持原有的浏览器配置选择方式。HTTP/1.1使用分块编码，HTTP/2使用DATA帧，不必预先计算完整正文长度。

```python
from fastls import Session, AsyncSession, Timeout

with Session(profile="chrome/156/windows") as session:
    with open("large.bin", "rb") as file:
        response = session.post("https://example.com/upload", data=file,
                                timeout=Timeout(total=300, write=20, headers=15))

async def blocks():
    for _ in range(1024):
        yield b"x" * 65536

async def upload():
    async with AsyncSession(profile="chrome/156/windows") as session:
        return await session.post("https://example.com/upload", data=blocks(),
                                  timeout=Timeout(total=300, write=20))
```

支持二进制文件、bytes迭代器、异步bytes迭代器，以及bytearray和memoryview块。单块可以大于64KiB，Python会拆分后提交；空块跳过。超过512KiB的bytes或UTF-8字符串也自动改用上传通道，较小正文保留原有JSON接口。文件仍由调用方关闭。异步迭代器需要AsyncSession；同步文件和迭代器的read/next会在当前线程执行，耗时读取应由调用方提供异步迭代器。同步生成器阻塞在next时，原生超时仍会取消网络，但Python只能在next返回后报告错误。

桥接层最多保留一个64KiB块，包含传输层尚未消费的余量。队列满时提交立即返回pending，Python等待后重试，既不为每个请求增加Python线程，也不跨C边界保留调用方指针。Python适配器另外持有当前块和调用方给出的对象；协议、TLS、内核缓冲及调用方自行分配的大块不在此上限内。stats新增buffered_upload_bytes，可观察桥接层排队量。

取消请求、关闭会话、生产者异常及超时都会终止上传并释放句柄。服务器提前给出3xx/4xx/5xx响应时，停止继续生产上传数据并返回响应。若正文尚未提交完就收到2xx，Python明确抛出包含duplex的FastlsError并关闭请求，避免悄悄截断上传或等待总超时；本接口不提供同时上传、下载的全双工操作。上传迭代器完成或中止时调用其close/aclose；文件的所有权不转移。

流式正文不能重放：307/308需要保留正文时直接返回重定向响应，不重新迭代或缓存正文。301/302/303按HTTP客户端规则可转换为GET并丢弃正文；info的upload_discarded标记这种转换，避免与提前2xx的全双工情况混淆。上传通道管理正文长度和传输编码，传入Content-Length或Transfer-Encoding会在创建请求之前报错；本阶段只支持未知长度上传，不提供固定长度流及自动重试。

## 写入超时

Timeout新增write，单位为秒；默认None关闭。它从获得连接开始计时，在传输层每次读取到上传数据时重置，到完整请求发送后转入headers预算。write包括等待生产者提供下一块，以及传输层因对端不读取而停止消费正文的时间；不是整段上传的累计时长，也不是服务器确认接收或持久化数据的证明。排队到桥接层本身不重置此时钟。

超时使用FastlsTimeoutError，phase为write。仍可用total限制从开始请求到响应读取的全部时间。HTTP/2按请求取消，不设置共享连接的写入deadline。需要同时更新Python包和动态库：fastls_features新增stream_upload及write_timeout。

## C ABI v1 增量

fastls_start的JSON增加stream_body=true，与body/body_base64互斥；write_timeout_ms=0关闭写入限制。创建后可轮询info并提交数据：

```c
int fastls_write(uint64_t handle, void *buffer, size_t length, int end);
```

每次length最多65536，end为0或1；零长度只有end=1合法。返回0表示整块已复制，可立即复用调用方缓冲；1表示背压，整块未消费；2表示传输层已关闭上传，停止提交并继续读取info/response；-1表示失败，-2表示参数或句柄非法。end=1可与最后一块同时提交，也可单独提交空块。已接受EOF后不得继续写入。此调用不等待网络、不会部分消费一块数据。

## 验证范围

tests/test_upload.py共16项真实动态库验收，包含H1/H2的64MiB同步上传及SHA256、64MiB异步上传、文件及大正文、空流/超大块、慢接收方、取消及H2并行请求、生产者异常、提前拒绝、重定向、总时限、写入停顿及进度重置。源码与wheel均纳入六组原生Actions，原有回归及Linux300秒持续测试继续执行。Go队列复制/背压/EOF/取消与阻塞正文关闭回归纳入race。fhttp修复了提前拒绝或取消时仅唤醒流量控制、无法解除Body.Read阻塞的问题；现在在锁外关闭正文，保留提前响应，并保证只关闭一次。证据文件为build/stability-upload-*.json。
