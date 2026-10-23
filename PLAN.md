# Fastls 跨语言封装实施规划

## Node.js 接入

新增 `node/` TypeScript SDK，通过 Koffi 复用现有 C ABI，无新增 Go 导出或 RPC 服务。
提供 Session、Promise 请求、单消费者 AsyncIterable 正文、AbortSignal、多值头、原生 Cookie 和连接池管理。
无损 JSON 和 bigint 保护 uint64 句柄精度；C 字符串逐次释放，动态库进程级持有。
共享定时器和受限立即调用处理非阻塞轮询，正常 EOF、取消、异常与提前退出均回收句柄。
Node 0.2默认流式解码，提供wireHeaders/decoded、结构化阶段超时、字节迭代器/Readable/AbortSignal工厂上传及大正文自动流式化。
九组Python行为测试的115个场景已有对应Node用例，加原有16项共131项；持续测试及语言差异见node/PARITY.md。
Node 0.3新增HTTP/2双工的生产者与手动交换、半关闭、waitUpload和异步清理。
Node 0.4新增SSE事件流、事件大小限制和手动恢复；源码及隔离安装包使用相同131项套件和ESM/CommonJS类型检查，按提交保存逐项验收证据。
已配置跨平台 Node 24 和最低 Node 22 CI；npm 预编译 Go 库分发与公开发布仍待实施。
用法和范围见 [node/README.md](node/README.md)。

日期：2026-10-24。状态：第一阶段及三轮测试扩展已完成；第二阶段已实现会话/连接池、多值响应头、Cookie、流式解码及分层超时。第三阶段已完成流式上传、独立写入预算、HTTP/2全双工、SSE工具和状态通知，WebSocket和发布阶段待实施。开发候选，未发布。

## 目标与仓库边界

Python 直接指定 `chrome/156/windows`，使用 Fastls 的 TLS/HTTP 配置；用户无需管理 Provider。
独立 FastTLS/imitate 负责目录和配方；Fastls 负责请求执行；本仓库负责 C ABI、生命周期及语言 SDK。
不复制指纹数据或在 Python 中实现版本选择。未知 ID 报错，自定义请求头优先。
浏览器产品版本与协议基线分别保留 Match 信息，不声称生成身份等于已验证真实浏览器。

## 第一阶段：可运行的端到端开发版本

1. 核心新增 DoContext，旧 Do 委托给它。请求句柄在响应头到达前创建，允许早期取消。
2. C ABI v1：版本、目录、开始请求、查询状态、非阻塞读取、取消、关闭、释放结果。
3. 配置/状态用 UTF-8 JSON；响应体用调用方缓冲区，不使用 Base64。上传暂为完整 UTF-8 或 Base64 字节。
4. Go 后台执行网络操作。每请求单个读协程、一个容量为1的块队列，块大小64KiB；不在消费者慢时无限缓存。
5. Python 同步与 asyncio 分别适配同一非阻塞 ABI；asyncio 使用可取消的短定时轮询，无逐请求 Python 后台线程，无 Go→Python 回调。
6. Session 是默认配置和活动响应的作用域，关闭时取消所有活动请求；第一阶段没有连接池和 Cookie jar。
7. Windows x64 实际构建 DLL、头文件和 Python 包；其它平台提供构建入口，运行结果单独验收。
8. 默认验证服务器证书。核心历史指纹路径关闭证书验证，新增 VerifyTLS 选项供本封装始终开启；不改旧调用默认值。

验收：真实 DLL 的 Python 同步/异步请求，首块先于服务器结束到达；事件循环持续运行；慢消费者的桥接缓冲有界；二进制无损；未知配置、取消、超时、提前关闭、重复关闭、非法句柄及未受信证书拒绝。核心现有包测试通过。

## ABI 契约

- uint64 单调递增句柄，0无效；不跨 ABI 暴露 Go 指针。
- 所有返回字符串由库分配，仅 fastls_free 释放；输入只在调用期间借用。函数不得保留调用方缓冲区。
- read 返回0=数据、1=尚无数据、2=EOF，负值=失败。长度独立返回；EOF 与 pending 明确区分。
- close 从句柄表移除并取消；多次关闭无害，后续查询/读取明确报告无效句柄。
- cancel 保留句柄用于查询错误；close 才释放登记。查询/取消/关闭可以并发；每响应只允许一个消费者。
- EOF/错误应保持可重复观测；响应头后发生读取错误不能伪装成正常 EOF。
- 默认最多256个未关闭请求；JSON输入最多1MiB；完整JSON上传解码最多512KiB，流式上传队列最多64KiB；单次read最多1MiB。
- panic 在导出边界转为失败，不跨 C ABI；无效非空C指针属于调用方违反契约，无法通过 recover 保证安全。
- 动态库由进程长期持有，活动请求期间不得卸载；轮询间隔与 ABI 版本检查由SDK处理。

## 后续阶段

第二阶段在核心实现可复用会话、按协议配置隔离的连接池、Cookie jar、原始多值响应头、流式内容解压和完整超时分层；再映射到 SDK。
第三阶段增加流式上传、完成通知替代定时轮询、SSE便利工具与WebSocket；验证背压和取消竞态。
第四阶段各平台实际 CI、wheel 内置动态库、C#/Rust等绑定、稳定ABI兼容测试和发布。尚未发布的上游依赖及许可证确认仍是发布前置条件。
用户后续要求提前接入GitHub Actions并授权整理、推送：工作流已加入6组原生平台/版本测试配置，上游v2分支已推送并固定SHA；见CI.md，不将配置存在计为平台测试通过。

## 实施前审查结论

已排除直接暴露 Go 对象、全量缓存下载、无限队列、用阻塞读取模拟 asyncio、全局错误字符串等设计。
已检查现有代码：Response.Body 可流式读取；Do 缺少 context；每请求构造新客户端；Response.Headers 已压平；SOCKS拨号器忽略 context。
第一阶段暂不开放代理参数，避免承诺尚未验证的取消边界。核心已有部分行为不在此阶段修复。
依赖本地未发布代码，通过被忽略的 go.work 联调；公共 go.mod 不写绝对路径或 replace。无需推送/发布即可完成本地验收。
结论：第一阶段无架构阻碍，按此规划实施。后续阶段不得记为本阶段已完成。

## 第一阶段交付记录

已交付 C ABI v1、Windows x64 DLL、Python同步/asyncio SDK、二进制流式下载、取消/超时、C/Python示例及本地检查脚本。
7项Python集成测试分别从源码和生成的wheel加载真实DLL执行通过；Go桥接4项测试、race及核心3个包测试通过。
公网检测请求返回HTTP200/HTTP2，确认规范ID和自定义UA传递；不是完整浏览器指纹等价性验收。
Windows短定时器提前调度问题已处理，轮询间隔按平台时钟精度调整。
详情及产物位置见 VALIDATION.md。第一阶段本地验收时尚未提交/推送；后续已获授权接入CI并推送开发分支，仍未发布模块或Release。Linux/macOS及后续阶段须以实际验收记录为准。

## 稳定性测试扩展 T2

按用户要求推进现有SDK的下一阶段测试，范围见TESTING_STAGE2.md。该测试阶段与前面的功能实施阶段独立，不将测试完成等同于连接池、Cookie jar、流式上传或WebSocket已实现。
为验证加密请求，新增隔离的私有CA配置和只读运行统计；修复测试复现的HTTP2接收窗口及取消后残余数据问题。每平台源码/wheel各运行30秒，Linux当前Go再运行300秒，所有证据由Actions保存。

## 网络异常测试扩展 T3

现有SDK新增可控的TCP/TLS停滞、异常HTTP1报文、HTTP2流重置、正文截止时间和重定向契约测试。范围见TESTING_STAGE3.md；六组原生CI分别检查源码和wheel，继续执行原有稳定性及持续测试。该测试阶段不改变后续功能实施范围。

## 第二阶段交付：会话复用

核心新增Session及请求级选项；封装新增原生会话句柄，Python的Session/AsyncSession共享各自的传输层。HTTP1启用keep-alive，HTTP2共用连接，按会话、profile、CA及来源隔离。等待建连期间保留请求取消边界，限制会话、配置与来源数量。详情及验收范围见POOLING.md；Cookie、多值头、解压与分层超时仍待实施。

## 第二阶段交付：多值头与Cookie

核心Response新增HeaderValues，封装保留旧headers并增加header_values。Python使用大小写不敏感的只读Headers映射，提供get_list与multi_items。每个原生Session共享一份带公共后缀检查的CookieStore，自动处理重定向与发送范围；Python增加URL范围的查询、写入、删除和清空，并可禁用。详见COOKIES_HEADERS.md；Cookie不持久化，也不实现浏览器SameSite上下文或配额淘汰。解压、分层超时及后续阶段仍待实施。

## 第二阶段交付：流式解码与分层预算

核心请求新增惰性流式解码及请求级阶段预算，不对共享连接设置deadline；Python默认解码并保留wire_headers，可关闭解码或指定输出上限。Timeout区分connect/headers/read/total，结构化超时可查询phase。Brotli升级修复截断误判，新增独立向量、64MiB解码、重定向预算与异步隔离验收。详见STREAMING_TIMEOUTS.md；后续已交付流式上传和独立写入预算，见下节；完成通知及WebSocket仍属后续范围。

## 第三阶段交付：流式上传与写入预算

核心Session支持一次性BodyReader及Write停顿预算；C ABI新增非阻塞fastls_write及stream_body，上传队列最多64KiB。Python接受文件、同步/异步迭代器和较大正文，处理背压、生产者异常、取消、提前响应及不可重放重定向。源码与wheel各16项验收及Go race纳入六组Actions。详见STREAMING_UPLOAD.md；未增加发布产物或完成通知。

## 第三阶段交付：HTTP/2全双工

显式全双工模式收到响应头后仍保持上传任务；异步响应负责上传任务的取消与异常传播，同步使用手动send/finish_upload与响应读取，不增加Python线程。核心拆分上传写入与响应读取时钟，并保持响应头后的写入限制。上传EOF后可继续下载，响应EOF结束整个交换；不支持自动重定向及H1/H3。源码与wheel各13项真实库验收加入六组Actions，详见DUPLEX.md。

## 第三阶段交付：Python SSE事件流

Session.sse与AsyncSession.sse复用现有响应流，逐条返回不可变ServerSentEvent；支持UTF-8/BOM、所有行尾与跨块边界、多行data、id/retry控制、手动Last-Event-ID恢复、单消费者和大小限制。处理204、错误MIME/状态、取消、超时、压缩错误与HTTP/2全双工组合。源码/wheel各23项验收纳入六组Actions，见SSE.md。不自动重连或重放请求；状态通知随后交付，见下节；WebSocket仍待实施。

## 第三阶段交付：原生状态通知

C ABI增加fastls_wait及session_watch/events/unwatch；同步等待原生状态，asyncio使用每会话一个认证的本机控制连接接入事件循环，不增加Python工作线程或Go回调。通知合并且有界，用代次处理检查/等待竞态，覆盖上传可写、响应头/正文、EOF、错误与取消。支持auto/required/poll，旧库保持兼容。异步上下文进入时完成初始化，失败和取消均清理候选连接；源码/wheel各20项、Go race和独立C消费者加入六组Actions，详见NOTIFICATIONS.md。WebSocket及发布仍待实施。
