# 流式解压与分层超时

Python Session/AsyncSession默认逐块解码gzip、deflate（zlib封装或常见raw deflate）、Brotli与Zstandard响应。数据仍通过原有有界队列传输，iter_bytes/aiter_bytes返回解码后的字节，不先收集整个压缩响应。不会额外修改profile或调用方提供的Accept-Encoding。

```python
from fastls import Session, Timeout, FastlsTimeoutError

with Session(profile="chrome/156/windows",
             timeout=Timeout(total=120, connect=5, headers=15, read=20)) as session:
    try:
        with session.stream("GET", "https://example.com/large-file") as response:
            print(response.decoded, response.wire_headers.get("content-encoding"))
            for chunk in response.iter_bytes():
                pass  # 逐块处理解码后的数据
    except FastlsTimeoutError as error:
        print(error.phase)  # connect / headers / read / total
```

## 解码及限制

- decode_content默认True，可在Session或单个请求设为False，直接取得完全相同的压缩正文。该选项不改变连接池身份。
- 实际使用解码时，response.headers移除Content-Encoding与Content-Length，response.wire_headers保留解析后的线上头及多值字段。response.decoded表示正在使用解码视图；完整性要以成功读到EOF为准。HEAD、204、304和identity不启用解码。
- 多个Content-Encoding按逆序解码，最多四层；未知编码明确报FastlsDecodeError。gzip支持串接成员；校验和错误、截断流及超限都不能作为成功EOF返回。
- max_decoded_bytes默认256MiB，可在会话或请求指定1..1TiB的正整数，只约束实际解码后的输出。普通非流式读取的32MiB上限仍然存在，并作用于解码后内容。关闭解码时，此限制不作用于原始字节。
- Zstandard解码器使用单线程及64MiB窗口上限；不支持调用方提供外部字典。流式队列最多保留原有约3×64KiB正文数据，统计不包括协议缓冲和各解码器内部状态。多层编码的解码器各自有内部缓冲，输出或窗口上限不等同于整个进程的内存上限。
- 初始化和每层读取检查请求取消。首次返回响应头不会等待压缩正文到齐；读取压缩头时同样适用正文读取超时。

截断测试复现了旧Brotli读取器在部分数据结束处返回普通EOF的问题；依赖已固定为v1.2.0，其[读取器增加了未完成状态的EOF检查](https://github.com/andybalholm/brotli/blob/v1.2.0/reader.go)。该问题作为H1/H2必需回归保留。

## 超时语义

原有数字timeout仍代表整个请求总时限，默认30秒；也可传Timeout(total=30,connect=None,headers=None,read=None)。所有已指定的值都是秒，必须大于0且不超过86400，None关闭该阶段限制；total始终必需。请求级timeout替换整份会话策略，数字覆盖会清除原先的分层限制。

- connect：每一跳开始，到获得可用连接为止，包含协议发现等待、连接池等待、DNS、TCP及TLS。复用连接通常立即结束此阶段。
- headers：请求及其完整上传正文写完后，等待最终响应头；1xx不结束此阶段。当前没有独立上传写入超时，发送正文仍受total约束。
- read：原生工作线程每次等待网络正文数据的时限；获得数据后重置，不是整个正文下载时间。消费者暂停本身不启动计时；若后台预读仍在等待网络，该预读仍会超时。队列已满后的背压等待不计入read。
- total：贯穿建连、重定向、正文解码、调用方暂停与等待的总时限。每次重定向重新计算阶段预算，total不会重置；已经正常观察到的EOF保持稳定。

FastlsTimeoutError继承FastlsError，phase区分四种超时；解码失败使用同样继承FastlsError的FastlsDecodeError。取消和普通网络错误仍使用FastlsError。阶段取消只影响当前请求/HTTP2流，不改共享连接的读写deadline。

分层预算属于请求级配置，不拆分连接池。HTTP2共享建连沿用已有的30秒内部安全上限；较短请求预算会先结束，较长connect设置不会扩大该内部上限。本阶段验证TCP上的HTTP1与HTTP2，不扩展到代理、HTTP3、流式上传或WebSocket。

## C ABI及验证

ABI仍为1。fastls_features()新增能力发现，返回stream_decode与phase_timeouts等字符串列表。fastls_start增加decode_content、max_decoded_bytes、connect_timeout_ms、headers_timeout_ms、read_timeout_ms；阶段毫秒值0关闭限制，max_decoded_bytes=0使用默认上限。未传decode_content的C客户端继续获取原来的正文模式。

fastls_info新增decoded、wire_header_values及结构化错误error_code/timeout_phase。所有结果仍使用既有JSON信封及fastls_free；新版Python包需要配套的新动态库。

tests/test_decode_timeouts.py的15项真实动态库验收覆盖独立压缩向量、叠加编码、串接gzip、原始模式、截断/校验错误、大小边界、64MiB解压后SHA256、慢消费者、分层及重定向超时、异步循环和H2流隔离。固定向量由Python标准库及独立Brotli/Zstandard库生成；CI无需额外Python依赖，维护者可运行scripts/generate_decode_vectors.py重新生成。

源码与wheel均纳入六组原生CI，核心定时器及解码回归纳入race；继续执行原有所有必需检查和Linux300秒持续测试，证据保存在build/stability-decode-timeouts-*.json。
