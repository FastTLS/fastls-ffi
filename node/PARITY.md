# Python / Node 验收对应关系

范围：八个 Python SDK 行为套件的 92 个用例，基线为 a7c90ec，保留原始文件名和用例名作为 Node 测试 ID。
对应关系由 `scripts/check-parity.mjs` 检查；Python 用例新增、删除或改名时，映射未更新会失败。
`tests/python-baseline.json` 同时固定八个 Python 源文件的哈希；即使用例名称不变，断言改变也必须复核 Node 对应测试后更新基线。
每项在真实动态库和独立本地靶场上执行，结果写入 `build/node-parity-source-*.json` / `node-parity-package-*.json`。
清单对应关系本身不表示用例通过，应检查同一提交的测试退出状态和逐项结果。

- test_python.py：7项，基础ABI、目录、响应、取消、流式和事件循环。
- test_stability.py：11项，TLS、背压、256请求限制、SSE、128路混合请求、资源回收。
- test_network.py：11项，静默TCP/TLS、畸形HTTP、分块报文、截断、重定向方法与敏感头。
- test_pooling.py：6项，连接复用、profile/CA隔离、24路H2共享连接、取消和发现等待。
- test_cookies.py：13项，多值头、域/路径/Secure、重复名称、过期优先级、重定向、并发与非法输入。
- test_decode_timeouts.py：15项，独立gzip/deflate/br/zstd及叠加向量、校验/截断/上限、64MiB解码、各阶段及每跳预算。
- test_upload.py：16项，64MiB同步/异步生产者、文件、大正文、背压、取消、生产者错误、提前响应、写入预算和重定向。
- test_duplex.py：13项，HTTP/2同时收发、手动send/半关闭、64MiB双向哈希、双向背压、响应头后异常及超时、取消和清理。

加上原有 2 项原生绑定测试和 14 项 Node 基础测试，完整 Node 套件为 108 项。
TypeScript 的 ESM/CommonJS 类型检查及安装包内容/哈希检查独立于上述用例数。
共享的Go测试、race检查和Python依赖门禁仍由原CI执行，不用Node重写。

## 语言适配范围

以下12个场景在逐项记录的 `adaptation` 字段中说明差异：

- test_python.py::test_sync_stream_first_chunk_before_completion：使用Promise和异步迭代，验证相同的首块时序。
- test_stability.py::test_multithreaded_sync_session：以96路Promise并发验证请求共享；不提供共享同步Session或worker_threads线程安全承诺。
- test_upload.py::test_64_mib_sync_h1_h2_bounded_queue：同步字节迭代器输入Promise API，验证相同字节、协议及64KiB原生上传队列上限。
- test_upload.py::test_files_large_bytes_and_unicode：Node文件流按Readable生命周期关闭；区别于Python保留调用方文件对象。
- test_upload.py::test_async_cancel_waiting_producer_and_h2_sibling：使用工厂信号中断生产者等待，保留同连接其它H2流；另验证生产者清理期间仍能取消请求。
- test_upload.py::test_invalid_chunks_headers_and_async_in_sync：非法块和受管头仍拒绝；Node统一Promise API接受异步迭代器，没有Python同步API的拒绝分支。
- test_upload.py::test_early_response_stops_async_producer：信号工厂及Readable支持及时清理；未启动迭代器也调用return，但生成器尚未进入的finally不会执行。普通任意Promise不能被JavaScript强行中断，调用方需提供可取消的生产者。
- test_duplex.py::test_sync_manual_exchange_no_worker_threads：手动交换采用Promise，不提供阻塞API；同时验证响应头前发送以及重叠写入拒绝。
- test_duplex.py::test_cancel_read_and_h2_sibling_survives：通过AbortSignal取消读取及配合信号的生产者，同连接其它流继续使用。
- test_duplex.py::test_close_session_cleans_paused_producer：close立即释放原生资源，closeAsync等待配合取消的生产者finally；另验证清理异常不会丢失。
- test_duplex.py::test_reject_redirect_and_response_eof_stops_upload：EOF触发工厂信号与return；未启动生成器尚未进入finally。
- test_duplex.py::test_h1_and_invalid_options_fail：Promise API接受自动双工生产者；手动模式拒绝body/duplex字段，没有Python同步接口的拒绝分支。

这些适配不记为“与Python接口完全相同”。原生请求始终按取消/超时立即释放；未配合取消的自定义Promise不具备强制finalize保证。

## 持续运行与证据

源码和安装包分别默认运行30秒、32路并发的资源回收测试。Linux当前Go组合额外运行300秒。
同一Linux组合另跑60秒、32路并发的双工混合长测，轮换自动上传、取消、手动交换与提前拒绝；支持设置FASTLS_NODE_DUPLEX_SOAK_SECONDS=300延长。该独立检查不计入108项主套件。
预热后检查goroutine增量不超过16、Go heap_inuse峰值增量不超过96MiB、RSS峰值增量不超过128MiB、最终RSS增量不超过96MiB、句柄/描述符增量不超过64，与Python阈值一致。
记录请求数、完整时间序列和基线；阈值是回归保护，不是无内存泄漏的证明。
Node读取Windows GetProcessHandleCount、Linux /proc/self/fd 或macOS /dev/fd，RSS采用当前进程工作集；GC可用时在预热后执行一次。

```shell
npm test
npm run test:package
# 设置 FASTLS_NODE_SOAK_SECONDS=300 后：
npm run test:soak
npm run test:duplex:soak
```
