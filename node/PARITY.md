# Python / Node 验收对应关系

当前范围是十五个 Python 行为套件的 209 个场景，加 2 项原生绑定和 14 项 Node 专有检查，共 225 项。源码、GitHub 内置库候选和 npm 拆分包分别运行完整套件。TypeScript、真实库 Cookie 兼容、安装门禁和 9 项发布边界检查另计。

## Python 专有入口

Python 0.1.4 新增模块级 request/get/post 等一次性同步调用，独立的 tests/test_api.py 覆盖临时会话、响应缓存与异常清理。该套件在 Python 源码和 wheel 验收中执行；Node 当前没有对应模块级便捷导出，未计入下方 209 项跨语言场景，不宣称该入口已在 Node 提供。

## 行为清单与基线

`tests/python-baseline.json` 固定源文件 SHA-256，基础 revision 为 `2325fec`，Cookie 集成来源另记。`scripts/check-parity.mjs` 检查源文件哈希和逐名对应关系；Python 新增、删除、改名或修改断言，都必须先复核 Node 测试再更新基线。清单一致不代表测试通过。

- `test_python.py`：7 项。
- `test_stability.py`：11 项。
- `test_network.py`：12 项。
- `test_pooling.py`：6 项。
- `test_cookies.py`：13 项。
- `test_decode_timeouts.py`：15 项。
- `test_upload.py`：16 项。
- `test_duplex.py`：13 项。
- `test_sse.py`：23 项。
- `test_notifications.py`：21 项。
- `test_proxy.py`：14 项。
- `test_request_options.py`：16 项。
- `test_websocket.py`：25 项。
- `test_websocket_cookies.py`：16 项。
- `test_response_metadata.py`：1 项。

`test_network.py` 已含请求尾部字段；`test_websocket.py` 包括 main 的四项补充用例；`test_response_metadata.py` 验证最终 URL、协议和 EOF trailers。不能沿用早期 21 项 WebSocket、217/219 项完整套件的旧计数。

## 语言适配

当前 43 个场景在证据的 `adaptation` 字段中记录语言差异。Node 统一采用 Promise/异步迭代和 AbortSignal，不提供 Python 同步 API、GIL 或跨线程共享 Session 的承诺。Readable 及 multipart 调用方读取器的所有权不同；任意未配合信号的 Promise 无法被强制中断。

以下内容与实际测试记录一致：

- `test_upload.py::test_64_mib_sync_h1_h2_bounded_queue`：Synchronous iterators feed the Promise API; Node has no blocking Session API.
- `test_upload.py::test_files_large_bytes_and_unicode`：Node file streams follow Readable ownership and auto-close semantics; Python file objects remain caller-owned.
- `test_upload.py::test_async_cancel_waiting_producer_and_h2_sibling`：Use the upload factory AbortSignal to cancel producer I/O; JavaScript cannot forcibly interrupt an arbitrary awaited Promise.
- `test_upload.py::test_invalid_chunks_headers_and_async_in_sync`：Node has one Promise API, so async iterators are accepted rather than rejected by a nonexistent synchronous API.
- `test_upload.py::test_early_response_stops_async_producer`：Abort-aware factories support active generator finalization; return() is also called on unstarted iterators, whose finally has not begun. Uncooperative promises remain caller-owned.
- `test_duplex.py::test_sync_manual_exchange_no_worker_threads`：Manual send/receive uses Promises on the main thread; Node has no blocking synchronous exchange API.
- `test_duplex.py::test_cancel_read_and_h2_sibling_survives`：AbortSignal cancels a pending read and cooperative upload factory; arbitrary JavaScript promises cannot be forcibly interrupted.
- `test_duplex.py::test_close_session_cleans_paused_producer`：close() releases native handles synchronously; closeAsync() additionally waits for cooperative producer finalization.
- `test_duplex.py::test_reject_redirect_and_response_eof_stops_upload`：Response EOF cancels the factory and calls iterator.return(); a generator closed before its first next() has not entered its finally.
- `test_duplex.py::test_h1_and_invalid_options_fail`：Node uses one Promise API and accepts automatic duplex producers; the manual exchange separately rejects body/duplex options.
- `test_sse.py::test_sync_real_h1_h2_fragmented_vectors`：Node uses Promise requests and async iteration for both H1 and H2; it has no blocking synchronous SSE API.
- `test_sse.py::test_first_event_and_cancel_preserve_active_h2_sibling`：AbortSignal cancels the pending event read; Node has no asyncio task cancellation. The active sibling stays on the same H2 connection.
- `test_sse.py::test_sync_early_break_closes_network`：Early break in for-await closes the event iterator and native response; Node has no synchronous context manager.
- `test_notifications.py::test_sync_wait_releases_gil_and_does_not_poll_body`：Node has one nonblocking Promise API; idle-read counts and timer progress replace Python GIL and blocking-wait checks.
- `test_notifications.py::test_async_idle_body_has_no_timer_polling_or_python_threads`：Node attaches its main-thread event loop to the wakeup socket; no JavaScript worker is created. Native Go and Node runtime threads are not Python thread counts.
- `test_notifications.py::test_response_close_from_other_thread_wakes_reader`：Node sessions are main-thread-only; close from another event-loop callback replaces Python cross-thread close.
- `test_notifications.py::test_session_close_from_other_thread_wakes_all`：Node sessions are main-thread-only; asynchronous session close replaces Python cross-thread close.
- `test_notifications.py::test_explicit_poll_mode_sync_async_and_sse`：The same Promise API covers the synchronous and asynchronous Python poll-mode cases.
- `test_notifications.py::test_context_initializes_before_body_and_cleans_failed_entry`：Session.open() initializes before returning and closes on failed initialization, replacing Python async-context entry.
- `test_notifications.py::test_different_event_loop_rejected_and_sync_close_allowed`：Node has one main-thread event loop per supported process. A worker is rejected at library load instead of testing Python cross-loop reuse.
- `test_stability.py::test_multithreaded_sync_session`：Adapted to 96 concurrent Promise requests. Shared synchronous sessions and worker_threads are outside the Node API; this is not a thread-safety claim.
- `test_python.py::test_sync_stream_first_chunk_before_completion`：Promise-based streaming replaces the Python blocking context manager.
- `test_proxy.py::test_large_upload_download_sse_and_duplex`：Node uses one Promise API; synchronous and asynchronous producers plus immediate/slow streaming consumers cover the corresponding Python sync/async combinations.
- `test_request_options.py::test_form_encoding_and_raw_body_compatibility`：Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.
- `test_request_options.py::test_invalid_options_fail_without_native_handle_or_file_read`：Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.
- `test_request_options.py::test_multipart_fields_repeated_files_unicode_and_empty_body`：Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.
- `test_request_options.py::test_multipart_64mib_slow_consumer_bounded_reads_and_memory`：Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.
- `test_request_options.py::test_multipart_read_failure_keeps_file_open_and_cleans_request`：Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.
- `test_request_options.py::test_async_query_json_form_and_charset`：Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.
- `test_request_options.py::test_async_multipart_file_failure_and_success`：Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.
- `test_request_options.py::test_async_64mib_multipart_bounded_stream`：Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.
- `test_request_options.py::test_async_cancel_slow_multipart_keeps_file_and_session_usable`：Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.
- `test_websocket.py::test_async_cancel_blocked_send_and_single_writer`：Node uses Promise/AbortSignal and one main-thread reader/writer instead of Python blocking APIs, threads or asyncio task cancellation.
- `test_websocket.py::test_single_reader_and_close_from_other_thread`：Node uses Promise/AbortSignal and one main-thread reader/writer instead of Python blocking APIs, threads or asyncio task cancellation.
- `test_websocket.py::test_async_messages_and_default_notification_idle`：Node uses Promise/AbortSignal and one main-thread reader/writer instead of Python blocking APIs, threads or asyncio task cancellation.
- `test_websocket.py::test_async_cancel_recv_preserves_sibling_and_http`：Node uses Promise/AbortSignal and one main-thread reader/writer instead of Python blocking APIs, threads or asyncio task cancellation.
- `test_websocket.py::test_async_handshake_cancel_session_close_and_poll_fallback`：Node uses Promise/AbortSignal and one main-thread reader/writer instead of Python blocking APIs, threads or asyncio task cancellation.
- `test_websocket.py::test_async_http_ws_cookie_exchange_and_parallel_sessions`：Node uses Promise/AbortSignal and one main-thread reader/writer instead of Python blocking APIs, threads or asyncio task cancellation.
- `test_websocket_cookies.py::test_session_isolation_and_concurrent_http_ws`：Node Promise/AbortSignal APIs exercise the Python asyncio scenario on the main thread.
- `test_websocket_cookies.py::test_cancelled_handshake_and_closed_session_recover`：Node Promise/AbortSignal APIs exercise the Python asyncio scenario on the main thread.
- `test_websocket_cookies.py::test_async_login_and_response_cookie_roundtrip`：Node Promise/AbortSignal APIs exercise the Python asyncio scenario on the main thread.
- `test_websocket_cookies.py::test_async_manual_mode_and_explicit_headers`：Node Promise/AbortSignal APIs exercise the Python asyncio scenario on the main thread.
- `test_response_metadata.py::test_metadata_and_trailers_refresh_at_eof`：Node refreshes metadata when the Promise-based body iterator reaches EOF.

## 命令与证据位置

从仓库根目录先构建动态库和 `build/ffi-fixture[.exe]`，再执行：

```sh
npm run test:parity --prefix node
npm run test:quick --prefix node
npm test --prefix node
npm run test:package --prefix node
npm run test:npm --prefix node
python node/scripts/test-npm-release.py
```

源码逐项证据为 `build/node-parity-source-*.json` 和 `build/node-source-*.json`。内置候选安装证据为 `build/node-parity-package-*.json`、`build/node-package-*.json`，归档及 `node-package-info.json` 在 `dist/`。拆分包的独立安装证据和 `npm-package-info.json` 在 `dist/npm/`，避免与内置候选证据混淆。

原生绑定测试同样在完整进程退出状态中判定；它们不计入 209 项 Python 映射。发布汇总还要求同提交 main CI 成功、五平台齐备、包摘要与内容相符、SDK/行为证据完整、授权材料无待审项。GitHub artifact 存在或任务单独绿色不足以代替这些条件。

主 CI 并行覆盖 Windows x64、Linux glibc x64/arm64、macOS x64/arm64 的 Node 24，并加 Linux x64 的 Node 22 / Go 1.27.3 / Python 3.10；其余组合使用 Go 1.26.10 / Python 3.11。共享 Go 契约、race、C 和 Python 门禁由原流水线执行，不用 Node 重写。

## 资源回收与独立长测

完整功能套件内的资源回收测试默认持续 30 秒、32 路并发。Linux 当前工具链另执行通用 300 秒检查；双工、SSE、代理、WebSocket 分别在独立任务运行 60 秒混合长测，不计入 225 项功能用例。

```sh
npm run test:soak --prefix node
npm run test:duplex:soak --prefix node
npm run test:sse:soak --prefix node
npm run test:proxy:soak --prefix node
npm run test:ws:soak --prefix node
```

通用时间由 `FASTLS_NODE_SOAK_SECONDS` 控制；各独立长测分别使用 `FASTLS_NODE_DUPLEX_SOAK_SECONDS`、`FASTLS_NODE_SSE_SOAK_SECONDS`、`FASTLS_NODE_PROXY_SOAK_SECONDS`、`FASTLS_NODE_WS_SOAK_SECONDS`，范围 1..300 秒。设置为 300 可延长，不因此改变阈值。

预热后，goroutine 增量不超过 16、Go heap_inuse 峰值增量不超过 96 MiB、RSS 峰值增量不超过 128 MiB、最终 RSS 增量不超过 96 MiB、句柄/描述符增量不超过 64。使用完整时间序列判定，不把阈值通过当作不存在内存泄漏的证明。Windows 使用 GetProcessHandleCount，Linux/macOS 使用 /proc/self/fd 或 /dev/fd；可用时在预热后执行一次完整 GC。

SSE、代理和 WebSocket 独立测试固定 V8 `--max-semi-space-size=16`，避免不同主机年轻代保留空间改变 RSS 基线；不更改用户进程配置或放宽阈值。证据保留进程参数、V8 快照及诊断采样。资源任务使用独立 runner，不能提高共享 harness 的 test-concurrency 来并行执行有全局句柄断言的用例。

取消与代理握手按照测试服务的实际阶段同步；事件循环检查要求读取仍挂起时观察到心跳。Windows 计时器可能合并采样，80 ms 后仍未完成的检查保留，再在最多 1 秒内观察三次心跳；128 路取消用例在目标句柄存活时观察心跳再取消。内存、回收和延迟门禁仍独立判定。
