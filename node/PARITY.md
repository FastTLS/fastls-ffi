# Python / Node 验收对应关系

范围：十四个 Python SDK 行为套件的 203 个用例，基线为 7bea602，保留原始文件名和用例名作为 Node 测试 ID。
对应关系由 `scripts/check-parity.mjs` 检查；Python 用例新增、删除或改名时，映射未更新会失败。
`tests/python-baseline.json` 同时固定十三个 Python 源文件的哈希；即使用例名称不变，断言改变也必须复核 Node 对应测试后更新基线。
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
- test_sse.py：23项，逐字节及所有分割位置、UTF-8/BOM/行尾、id/retry、大小边界、H1/H2、gzip校验、状态及头、恢复、取消、心跳超时、40MiB背压和双工上传组合。

- test_notifications.py：21项，空闲时无轮询、代次竞态、共享等待取消、128路混合请求、会话隔离、握手认证/取消、断线、旧库门禁、显式poll及资源回收、32×32独立会话创建关闭。

- test_proxy.py：14项，HTTP/HTTPS CONNECT、独立代理CA与主机名验证、SOCKS5本地/代理DNS、认证与目标头隔离、连接/凭据池隔离、显式直连、旧库门禁、目标CA、环境代理忽略、超时取消、64MiB同步/异步上传下载、慢消费者、SSE与双工。

- test_request_options.py：16项，查询、JSON、表单、multipart、64MiB同步/异步读源、慢消费者、取消、失败、重定向、结束分隔符竞态、编码与状态检查。

test_websocket.py：21项，消息、分片、控制帧、TLS/ALPN、四种代理、关闭、预算、通知/轮询、取消与兄弟请求隔离。已加入映射与源文件哈希。

加上原有 2 项原生绑定测试和 14 项 Node 基础测试，完整 Node 套件为 219 项。
TypeScript 的 ESM/CommonJS 类型检查及安装包内容/哈希检查独立于上述用例数。
共享的Go测试、race检查和Python依赖门禁仍由原CI执行，不用Node重写。

## 语言适配范围

以下41个场景在逐项记录的 `adaptation` 字段中说明差异：

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
- test_sse.py::test_sync_real_h1_h2_fragmented_vectors：统一Promise和异步迭代API，验证相同H1/H2事件、响应头和恢复状态。
- test_sse.py::test_first_event_and_cancel_preserve_active_h2_sibling：AbortSignal取消挂起事件读取，替代asyncio任务取消，同一H2连接的活动流继续完成。
- test_sse.py::test_sync_early_break_closes_network：for-await提前break关闭迭代器和原生响应，替代Python同步上下文管理器。

- test_notifications.py::test_sync_wait_releases_gil_and_does_not_poll_body：用非阻塞Promise读取计数和事件循环进度代替Python GIL及同步阻塞等待。
- test_notifications.py::test_async_idle_body_has_no_timer_polling_or_python_threads：Node主线程接收唤醒，无新增JavaScript worker；不把Node/Go运行时线程等同于Python线程枚举。
- test_notifications.py::test_response_close_from_other_thread_wakes_reader：另一事件循环回调关闭响应，替代Python跨线程关闭。
- test_notifications.py::test_session_close_from_other_thread_wakes_all：异步关闭会话替代Python跨线程关闭，Node仍只支持主线程。
- test_notifications.py::test_explicit_poll_mode_sync_async_and_sse：统一Promise API覆盖Python同步及异步poll场景。
- test_notifications.py::test_context_initializes_before_body_and_cleans_failed_entry：Session.open在返回前初始化，失败关闭新会话，代替async上下文进入。
- test_notifications.py::test_different_event_loop_rejected_and_sync_close_allowed：Node只支持主线程事件循环；验证worker加载被拒绝，代替Python跨循环复用。

- test_proxy.py::test_large_upload_download_sse_and_duplex：统一Promise API，使用同步及异步上传生产者、即时与慢速下载消费者，覆盖Python同步/异步组合。

这些适配不记为“与Python接口完全相同”。原生请求始终按取消/超时立即释放；未配合取消的自定义Promise不具备强制finalize保证。

## 持续运行与证据

源码和安装包分别默认运行30秒、32路并发的资源回收测试。Linux当前Go组合额外运行300秒。
同一Linux组合另跑60秒、32路并发的双工混合长测，轮换自动上传、取消、手动交换与提前拒绝；支持设置FASTLS_NODE_DUPLEX_SOAK_SECONDS=300延长。该独立检查不计入219项主套件。
该组合另跑60秒、32路并发的SSE混合长测，轮换正常/压缩事件流、提前break、非法响应头、解析上限及解码错误；设置FASTLS_NODE_SSE_SOAK_SECONDS=300可延长。同样不计入219项主套件，证据为build/node-sse-recovery-source-*.json。
独立SSE长测显式设置V8的`--max-semi-space-size=16`，以统一不同主机的年轻代容量。Windows Node24默认策略在高分配速率下可将年轻代保留空间扩至128MiB，即使完整GC后存活对象恢复仍保留该空间，影响从冷进程预热基线计算的RSS增量。该参数只约束测试进程的年轻代，不限制老年代、不改变SDK运行配置；RSS、Go堆、goroutine与句柄阈值均保持不变，证据另存运行参数和V8堆空间快照。
预热后检查goroutine增量不超过16、Go heap_inuse峰值增量不超过96MiB、RSS峰值增量不超过128MiB、最终RSS增量不超过96MiB、句柄/描述符增量不超过64，与Python阈值一致。
记录请求数、完整时间序列和基线；阈值是回归保护，不是无内存泄漏的证明。
Node读取Windows GetProcessHandleCount、Linux /proc/self/fd 或macOS /dev/fd，RSS采用当前进程工作集；GC可用时在预热后执行一次。

```shell
npm test
npm run test:package
# 设置 FASTLS_NODE_SOAK_SECONDS=300 后：
npm run test:soak
npm run test:duplex:soak
npm run test:sse:soak
```

代理扩展另提供60秒、32路并发的混合回收检查，覆盖四种代理的正常H2、单流取消、认证拒绝和握手取消；Linux当前组合纳入CI，支持FASTLS_NODE_PROXY_SOAK_SECONDS=300。独立证据为build/node-proxy-recovery-source-*.json，沿用上述回收阈值，不计入219项主套件。

代理独立长测与SSE长测一样，显式使用`--max-semi-space-size=16`固定测试进程的V8半空间上限。本机Windows Node24默认策略在持续分配时将new_space保留空间从4MiB扩大至128MiB，而Go堆、原生请求和隧道已回收。该参数仅作用于独立测试进程，不修改SDK用户进程的运行配置，也不放宽RSS或句柄阈值。证据保存运行参数、V8空间快照，以及阈值判定之后的完整GC诊断；判定使用GC前的最终采样。

代理长测按靶场握手阶段同步取消，避免固定毫秒计时在慢runner上提前中止TLS而未触达待测CONNECT阶段。HTTPS靶场延迟50毫秒处理CONNECT，确保该时序在本地也能覆盖。H2可合并同一代理的在途建连；每批每种scheme至少触发一次真实握手取消，并逐批核对启动/回收计数，不依赖请求数等于物理建连数。资源阈值保持不变。

## 0.8 请求参数适配

新增9个场景的adaptation字段明确说明：Node保留body，以form显式编码表单；multipart接受调用方拥有的read(maxBytes, signal)读取器，读取可同步或返回Promise，不自动close。Python同步与异步入口在Node统一为Promise。其余7项沿用相同行为断言。0.8时166项行为映射分为12个套件；0.9新增21项WS后为187项/13套件，完整测试203项。

## 0.9 WebSocket适配

新增5项adaptation记录：blocked-send、single-reader/thread、idle notification、cancel-recv/siblings、handshake/session/poll。以Promise/AbortSignal代替Python线程及asyncio任务；未增加worker_threads承诺。源码/安装包均运行21项；test:ws:soak独立运行60秒/32路混合负载，不计入203项。

## 0.10 WebSocket共享Cookie

新增test_websocket_cookies.py的14项逐名映射：HTTP登录→WS认证、握手Cookie→HTTP及删除、域/路径/Secure/同名顺序、显式和空Cookie、禁用/manual模式、HTTP失败响应、无效101/TLS、并发与会话隔离、四种代理、取消/关闭、旧库能力门禁、异步往返及非法模式。并发、取消、异步往返、异步manual四项标注Promise/AbortSignal适配。总计201项Python行为、41项适配、14套件，加16项Node测试共217项。基线文件为新增套件单独记录来源说明及SHA，原13套件仍使用7bea602。WS长测同时检验登录Cookie、成功握手写回及非法101不污染Cookie罐。

## 0.10.1能力兼容回归

新增control能力独立识别和automatic-only库拒绝manual两项Python/Node映射。总计203项行为、14套件、41项适配，加16项Node检查为219项。真实库兼容检查另计，包含当前Node库、修复后的Python分发库、原始8ced375分发库及0.9旧库；不把能力模拟等同于真实跨库验收。
