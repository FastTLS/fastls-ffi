# Fastls Node.js SDK

未发布的开发版本。Node.js 22+，TypeScript 类型声明、ESM 和 CommonJS 入口。
通过 Koffi 复用 fastls-ffi 的 C ABI v1，无需 Python 进程或 RPC 服务。
当前仅支持 Node.js 主线程；暂不支持 worker_threads、Electron、Bun 或浏览器。

0.2 开发版本对齐 Python 的解压、分层超时和上传能力：默认解压响应，大于512 KiB的字符串/字节自动流式上传。新版SDK要求带 stream_decode、phase_timeouts、stream_upload 和 write_timeout 的动态库。
0.3 增加显式 HTTP/2 双工读写；使用时另检查原生库的 duplex_h2 能力。
0.4 增加 SSE 事件流，按需解析 UTF-8 事件，支持手动恢复、事件大小限制及与双工上传组合。

## 本地构建与使用

先按上级 README 构建 Go 动态库，再在本目录执行：

```shell
npm ci
npm run build
```

源码默认查找仓库 `dist/`。安装到其它项目后，设置 `FASTLS_LIBRARY` 为动态库绝对路径，
或向 Session 传 `library`。普通 `npm pack` 仍生成需外部动态库的轻量包。
0.6新增内置本机动态库的候选包，见下节；两类包均不会自动下载或编译Go库。
Koffi 自身需要对应平台的原生组件，其依赖不等同于 Fastls 动态库。

可运行 `npm pack` 生成本地安装包，再在目标项目中执行 `npm install <安装包绝对路径>`。
包名暂定 `@fasttls/fastls`，不表示已占用 npm 名称或已发布。

```js
import { Session } from '@fasttls/fastls';
// CommonJS: const { Session } = require('@fasttls/fastls');

const session = new Session({
  profile: 'chrome/156/windows',
  timeoutMs: 30_000,
  // library: '/absolute/path/to/libfastls.so',
});
try {
  const response = await session.get('https://example.com', {
    headers: { 'User-Agent': 'my-client/1.0' },
    signal: AbortSignal.timeout(5_000),
  });
  console.log(response.status, response.text(), response.match);
} finally {
  session.close();
}
```

## 请求和响应

- `await session.request(method, url, options)`，以及 `get`、`post`，返回完整缓存的 Response。
- 请求选项：`profile`、`headers`、`body`、`timeoutMs`、`allowRedirects`、`signal`。
- `body` 为字符串、Buffer、Uint8Array、同步/异步字节迭代器、Node Readable 或接收 AbortSignal 的生产者工厂。超过512 KiB的字符串/字节自动使用上传通道。
- `timeoutMs` 为毫秒整数或 `{ total, connect?, write?, headers?, read? }`，每项范围1..86400000；默认总时限30000。请求级数字覆盖会清除会话分层限制，结构化覆盖替换整份策略。
- 会话默认头和请求头按大小写不敏感方式合并，请求值优先，包括空字符串。
- `Response` 提供 `status`、`ok`、`headers`、`wireHeaders`、`decoded`、`match`、`content: Buffer`，以及同步 `text()` / `json()`。
- 4xx/5xx 仍返回 Response；参数、网络、TLS 和原生错误抛出异常。
- `Headers.get(name)` 返回第一个值或 undefined；`getAll(name)` 保留所有重复值，不按逗号拆分。
  `entries()` / 默认迭代返回每个字段的首值，`multiItems()` 返回全部字段值。
- `listProfiles()` 返回规范 ID；`stats()` 返回 Go 运行时只读快照，其统计范围是该动态库实例，不仅是当前会话。

未知 profile 报错；Match 保留生成身份和协议基线信息，不承诺与真实浏览器指纹完全等价。

## 流式下载和取消

```js
const response = await session.stream('GET', 'https://example.com/large-file', {
  signal: controller.signal,
});
try {
  for await (const chunk of response.body) {
    await consume(chunk);
  }
} finally {
  response.close();
}
```

响应头到达后 `stream()` 即返回。`body` 是单次消费的 `AsyncIterable<Buffer>`，每个块独立持有内存，
可保留供后续使用。停止拉取时 SDK 不继续读取；块边界不代表行、SSE 事件或 HTTP 帧。
可通过 `Readable.from(response.body, { objectMode: false })` 接入 Node 流并使用 `pipeline()`。

`StreamResponse.read()` / `text()` / `json()` 异步读取完整正文，上限同普通请求的 32 MiB；更大响应必须逐块消费。
默认在Go侧逐块解码gzip、deflate、br及zstd，`decodeContent: false`取得原始字节；`text()` 按 UTF-8 解码，替换非法字节。
解码时headers移除Content-Encoding/Content-Length，wireHeaders保留线上字段；decoded表示解码视图。
`maxDecodedBytes`默认256 MiB，范围1..1 TiB，只限制解码输出；普通缓存32 MiB限制仍独立适用。未知编码、校验失败、截断或超限抛出FastlsDecodeError。

正常 EOF、读取异常或提前 `break` 会关闭请求。尚未开始迭代的响应需要显式 `close()`，
也可调用 `cancel()` 或中止 AbortSignal。AbortSignal 在等待响应头和正文读取期间都有效，
抛出 `AbortError`（`code: 'ABORT_ERR'`，`cause` 保留 signal.reason）。超时抛出 FastlsTimeoutError，其phase为connect/write/headers/read/total。解码错误、超时错误和AbortError均继承FastlsError。
`session.close()` 取消并关闭所有活动请求，重复关闭无害；不依赖 GC 清理资源。

## 连接池、Cookie 和证书

Session 复用原生会话的 H1 keep-alive 和 H2 多路复用；不同 Session 隔离。
`session.closeIdleConnections()` 释放空闲连接，保留 Cookie。
Cookie 自动管理和重定向处理均由 Go 完成：

```js
session.cookies.set('token', 'value', { url: 'https://example.com', path: '/', secure: true });
session.cookies.get('token', 'https://example.com/account');
session.cookies.getAll('https://example.com/account'); // [{ name, value }, ...]
session.cookies.delete('token', { url: 'https://example.com', path: '/' });
session.cookies.clear();
```

设置选项还包括 `domain`、`httpOnly`、`maxAge`（秒）和 `expires`（RFC3339）。
`cookies: false` 禁用自动收发。显式 Cookie 请求头优先。规则和存储限制见上级 COOKIES_HEADERS.md。

始终校验证书。可通过 `caFile` 使用仅属于本会话的私有根证书（替代系统根）；文件在构造时读取。
不修改系统信任库，不提供关闭 TLS 验证选项。

## HTTP/2 双工

`duplex: true` 允许响应头到达后继续上传，可同时读取响应。仅支持 HTTP/2，并始终禁止自动重定向；普通上传仍拒绝未完成上传时提前返回的 2xx。

```js
const response = await session.stream('POST', url, {
  duplex: true,
  body: signal => produceBytes(signal),
});
try {
  for await (const chunk of response.body) await consume(chunk);
  await response.waitUpload();
} finally {
  await response.closeAsync();
}
```

`waitUpload()` 等待原生上传 EOF 接受；需要与响应读取并行，单独等待可能被 HTTP/2 流量控制阻塞。
手动模式可先发送数据，再等待响应头，也可在获得响应后交替收发：

```js
const exchange = session.duplex('POST', url);
try {
  const response = await exchange.response;
  const reading = consumeResponse(response.body);
  await exchange.send(Buffer.from('first'));
  await exchange.finishUpload();
  await reading;
} finally {
  exchange.close();
}
```

`send()` 接受 Buffer/Uint8Array，按64 KiB分块等待原生队列容量；同一 exchange 同时只允许一个 send/finish 调用，重叠调用会被拒绝。发送完成前调用方应保持输入字节不变。
`finishUpload()` 只关闭上传方向，响应仍可继续读取；响应EOF、取消、超时或会话关闭会停止生产者。
服务器提前结束上传时，手动send会报错，已返回的响应仍可读取；waitUpload会指出上传EOF未被接受。
响应头到达后的生产者异常会中止读取并保留原异常。

`close()` 立即释放原生请求；`response.closeAsync()` / `session.closeAsync()` 还等待生产者清理，并报告清理中的异常。
应使用接收 AbortSignal 的工厂或可销毁 Readable 来取消挂起I/O。JavaScript不能中断任意Promise；不配合信号的生产者可能使等待清理的Promise持续挂起，但原生请求仍会及时释放。双工 `read()`、`request()` 和迭代结束也会等待清理。
示例见 `examples/duplex.mjs`。

重复收发、取消和提前拒绝可运行 `npm run test:duplex:soak`（默认60秒，FASTLS_NODE_DUPLEX_SOAK_SECONDS可设为1..300）。Linux当前Go组合把这项独立压力检查纳入CI，检查原生请求、JS生产者任务、RSS、句柄和goroutine的回收。

## SSE 事件流

```js
const events = await session.sse('GET', 'https://example.com/events', {
  timeoutMs: { total: 3_600_000, read: 45_000 },
  lastEventId: '41', // 可选：上次完整提交的事件 ID
});
try {
  for await (const event of events) {
    console.log(event.event, event.id, event.data);
    if (event.data === '[DONE]') break;
  }
} finally { await events.closeAsync(); }
```

`session.sse(method, url, options)` 返回单消费者 `SSEStream`，复用普通请求的配置、Cookie、连接池和 AbortSignal。
事件为不可变的 `ServerSentEvent`，含 `data`、`event`（默认message）、`id` 和可选的 `retry`（毫秒）；`event.json()` 解析 JSON，非法 JSON 抛出 SyntaxError。
`[DONE]` 是普通数据，由应用决定是否停止。`events.response` 提供响应元数据，请勿同时读取其正文。

解析支持任意字节分块、UTF-8替换、初始BOM、CR/LF/CRLF、多行及空data；空行提交事件，EOF丢弃未完成的事件。
ID跨事件保留，空值重置，含NUL的ID忽略。`events.lastEventId` 仅在完整块结束时更新，包括仅含ID的控制块；`events.retry` 保留最近合法的重试提示。
retry只接受ASCII数字并截到2147483647；单个事件的retry只反映其当前块。
`lastEventId` 同时设置请求头和初始解析状态，拒绝NUL/CR/LF。SDK不会自动重连或重放请求。

默认请求头为Accept:text/event-stream和Cache-Control:no-cache，请求headers可覆盖，优先于同名会话默认头。
HTTP200要求唯一的text/event-stream内容类型（可带MIME参数）；HTTP204返回空流，其它状态拒绝。
始终按UTF-8解析；压缩沿用原生解码及完整性检查，收到压缩流时不能关闭decodeContent。

`maxEventBytes` 默认1MiB，范围1..64MiB，分别限制原始单行及累计data的UTF-8字节数（含多行分隔符）；超限关闭请求。
无后台事件队列，停止拉取时停止解析和原生读取，整个事件流可超过32MiB缓存上限。解析器还持有当前块、ID、事件类型及解码开销。
事件之间让出事件循环，确保同一网络块包含大量事件时，计时器和取消仍能执行。

默认总超时仍为30秒；可显式增加total（最长24小时），目前不支持null或关闭总超时。
注释心跳刷新网络read预算，total仍从请求开始计时。
`cancel()`、`close()`、`closeAsync()` 和提前break沿用响应清理；未开始迭代时也应显式关闭。
HTTP/2可组合 `{ duplex: true, body: signal => source(signal) }` 边上传边接收事件；上传完成检查为 `events.response.waitUpload()`。
可运行示例 `examples/sse.mjs`，完整解析规则与Python对应验收见上级SSE.md和本目录PARITY.md。
`npm run test:sse:soak` 默认以32路并发重复运行60秒，验证正常结束、提前退出、错误头、解析超限和解码失败后的资源回收；可设置FASTLS_NODE_SSE_SOAK_SECONDS为1..300。Linux当前Go组合纳入CI。
该独立长测固定V8年轻代参数以比较RSS增量，保留原有资源阈值，记录运行参数和堆空间快照；不会更改SDK用户进程的配置，说明见PARITY.md。

## 原生就绪通知

0.5默认使用`notifications: 'auto'`：新库带session_notifications能力且符号完整时启用，旧ABI-v1库继续轮询。`required`缺少能力时在创建原生会话前报错；`poll`显式使用原调度器。

```js
const session = await Session.open({ notifications: 'required' });
try {
  console.log(session.notificationMode); // async-events
  const response = await session.get(url);
} finally { await session.closeAsync(); }
```

普通`new Session()`仍是同步构造；首次请求自动初始化，也可先`await session.ready(signal)`。Session.open初始化失败会关闭新会话，ready失败允许重试。首次并发请求共享初始化；取消一个等待者不会取消其它等待者，全部退出时关闭未就绪的候选通道。HTTP总预算从创建原生请求开始，通道初始化另限3秒。

每会话最多一条经32字节随机令牌认证的IPv4本机TCP连接，监听地址为127.0.0.1临时端口，认证后关闭监听器；候选连接最多两个。线上只有握手和唤醒字节，HTTP数据仍经原ABI读取。没有Go到JavaScript回调或JavaScript工作线程。

`notificationMode`为async-events或poll。通道运行中断开会报错并唤醒等待者，不静默回退；关闭后可显式poll重建会话。关闭会话释放通道，closeAsync还等待候选socket关闭及上传清理。状态集合只含活动请求，溢出提示重查本会话全部请求。

手动duplex仍同步返回交换对象，send/response在通道就绪后才创建请求；初始化期间关闭交换不会发送HTTP请求。上传、SSE、总超时及AbortSignal继续适用。验收记录包含空闲请求的ABI调用次数，不以单次套件运行时间证明性能优劣。

## 调度与边界

网络由 Go goroutine 执行。新库默认通过每会话一条本机TCP连接接收就绪提示，Node事件循环随后重查非阻塞C接口，不定时查询空闲请求。
通知是重查提示，不保证读写一定成功；通过代次快照防止漏唤醒，并独立取消双工的读写等待。
立即调用会让出事件循环；不承诺固定吞吐量或延迟，原生控制调用及JSON/Buffer转换仍在主线程执行。
显式poll或旧库回退使用共享约10ms定时器，保留每轮处理数量及时间上限。

句柄使用 bigint，无损解析和序列化 ABI JSON 数字；返回的 C 字符串逐次由 fastls_free 释放。
加载时检查ABI及所需能力，并使用系统级固定加载（Windows PIN / POSIX NODELETE），避免Node环境清理时卸载仍有Go后台线程的动态库。
原生限制仍适用：最多 64 会话、256 未关闭请求，每会话最多 16 种传输配置。
代理支持HTTP/HTTPS CONNECT、socks5及socks5h，见下节；不提供WebSocket。

## 验证

仓库中需先构建 `build/ffi-fixture[.exe]`（`go build -o build/ffi-fixture ./cmd/ffi-fixture`；Windows 使用 .exe 后缀）。

```shell
npm test
npm run test:package
```

测试使用真实动态库和本地靶场，覆盖 H1/H2、证书、连接池、Cookie、多值头、二进制、取消、超时、
异常正文、64 MiB 下载、32 MiB 缓存限制和 128 路并发。安装包测试在临时项目安装 npm tarball，
重新执行全部测试并检查包名导入和 TypeScript 声明。证据写入仓库 build/node-*.json。
十一组Python套件的150个场景已有对应项，包含13项双工、23项SSE、21项通知和14项代理验收，连同原有16项，共166项。映射检查由 `npm run test:parity` 执行；Python新增用例未映射会使检查失败。源码和隔离安装包均运行同一套测试，逐项结果与适配说明写入 `build/node-parity-*.json`。
持续测试默认30秒，检查RSS、操作系统句柄/描述符、Go堆和goroutine回收。Linux当前Go/Node组合另跑300秒；设置 `FASTLS_NODE_SOAK_SECONDS=300` 后执行 `npm run test:soak` 可复现。

## 与Python的语言差异

Node统一使用Promise API，不提供Python同步Session或共享同步会话多线程调用；对应场景采用同步迭代器输入和96路Promise请求验证，不宣称线程安全。Node Readable按自身流的生命周期管理文件句柄，区别于Python保留调用方文件对象。
JavaScript无法强行中断任意Promise：取消会立即释放原生请求，并请求迭代器return/destroy；生产者正在等待的自定义I/O必须支持取消，才能保证其finally及时执行。推荐使用接收AbortSignal的上传工厂。Python的协程强制取消场景对应为工厂信号取消，报告中明确标记适配。
安装包验证检查必需入口、类型声明、平台元数据和文件白名单；只允许本平台动态库、清单和依赖声明进入候选包。
通过后，实际测试过的 tarball、对应 `.sha256` 和 `node-package-info.json` 保存到仓库 `dist/`，
并随平台 CI artifact 一起保留。清单记录 Node 版本、系统/架构、npm 包及所用动态库的 SHA256；
CI 运行时还记录提交 SHA。测试前后会核对动态库未被替换。清单用于匹配测试产物，不代表数字签名或公开发布。

## 内置动态库的候选安装包（0.6）

构建本平台Go动态库后，运行 `npm run pack:native`，产物位于上级 `dist/`。
例如Windows x64：`fasttls-fastls-0.6.0-dev.0-win32-x64.tgz`；Linux包名另带`-glibc`。
在目标项目执行 `npm install <候选包绝对路径> --ignore-scripts`，随后即可 `new Session()`，无需Go、Python、编译器或FASTLS_LIBRARY。
安装时保留npm可选依赖，Koffi使用对应平台的预编译组件。`npm run test:package`在空工具搜索路径、清除FASTLS_LIBRARY且关闭安装脚本的临时项目中重复完整166项测试。靶场是仓库中已构建的独立测试服务，SDK和动态库均从安装目录加载。

当前候选覆盖Windows x64、Linux glibc x64/arm64、macOS x64/arm64，Node22+。每个tarball只含一个系统/架构，不能将同名版本的这些变体当成可直接发布到npm的通用包。npm的os/cpu/libc字段限制安装目标；首次使用还检查平台、ABI声明、库SHA256及Linux的glibc版本。Linux最低glibc从ELF符号版本提取，macOS最低版本从Mach-O提取，具体要求及系统动态依赖见包内`native/manifest.json`，不将CI宿主机上的通过扩大为所有旧系统均已测试。musl不在当前范围。

加载优先级：Session的`library` → FASTLS_LIBRARY → 包内动态库；源码工作区最后才回退到仓库dist目录。显式覆盖仍检查ABI和能力。包内文件损坏、缺失或平台不符会报错，不静默寻找其它库。SHA256用于完整性核对，不能替代签名或信任来源。

清单还保存Go构建来源摘要、模块版本/校验和、FastTLS仓库提交和收集到的许可证文本。`native/THIRD_PARTY_NOTICES.txt`仅是可用材料清单。当前FastTLS各仓库缺少根LICENSE/COPYING，已明确记为公开发布前的待处理项，不能据此推定已有再分发授权。候选包标记private且没有安装/构建脚本；本阶段仅生成CI验收产物，不发布npm或Release。

## 代理（0.7）

```js
const session = new Session({
  proxy: 'http://user:password@127.0.0.1:8080',
  timeoutMs: { total: 30000, connect: 5000 },
});
try {
  await session.get('https://example.com/');
  await session.get('https://example.com/', { proxy: null }); // 显式直连
  await session.get('https://example.com/', { proxy: 'socks5h://127.0.0.1:1080' });
} finally { await session.closeAsync(); }
```

Session和请求的`proxy`接受URL字符串或null。请求省略或undefined继承会话默认值；null或空字符串直连。普通请求、stream、sse、自动/手动duplex遵循相同规则。传入其它类型会报TypeError。显式代理要求原生`proxy_tunnels`能力；旧ABI-v1库仍可直连，代理请求在登记请求句柄前报错。

`http://`对HTTP和HTTPS目标均使用HTTP/1.1 CONNECT，默认端口80。`socks5://`本机解析目标域名，`socks5h://`交给代理解析，默认端口1080。支持HTTP Basic和SOCKS用户名/密码认证，凭据按URL规则转义，例如密码中的@写成%40。不读取HTTP_PROXY、HTTPS_PROXY、ALL_PROXY、NO_PROXY及其小写形式。`https://`在代理TLS中运行HTTP/1.1 CONNECT，默认端口443，证书和主机名验证始终开启，不回退到明文。HTTPS代理另要求原生`https_proxy_tls`能力；暂不支持SOCKS4或PAC，详情见上级PROXY.md。

`proxyCaFile`只控制HTTPS代理信任，`caFile`只控制目标信任，两者互不代替。未设置代理CA使用系统根；提供文件则替换系统根。构造Session时读取默认代理CA一次，请求指定路径时重新读取；请求undefined继承默认，null恢复系统根。直连、HTTP和SOCKS忽略代理CA默认值及请求覆盖。代理地址及凭据、profile、代理CA和目标CA共同隔离连接池。目标证书仍需验证；目标请求禁止设置Proxy-Authorization和Proxy-Connection。认证只用于代理握手，错误消息不回显凭据或代理任意响应短语。握手计入connect和total预算，AbortSignal取消会关闭未建立的隧道；取消单条H2流继续复用健康的共享隧道。代理拒绝后不降级或自动重试。

代理测试使用Node测试进程中的独立、仅访问loopback的CONNECT/RFC1928/RFC1929靶场；SDK本身不依赖Python或代理辅助进程。十一组Python行为映射、源码及内置库安装包统一验收；语言适配见PARITY.md。

`npm run test:proxy:soak`默认以32路并发运行60秒，轮换正常请求、共享H2流取消、错误认证和停滞握手取消；四种scheme分别使用真实loopback代理。FASTLS_NODE_PROXY_SOAK_SECONDS可设为1..300。Linux当前Go/Node组合执行该独立测试，沿用现有RSS、Go堆、goroutine及句柄阈值，逐批记录写入build/node-proxy-recovery-*.json，不计入166项主套件。

代理独立长测与SSE长测一样，显式使用`--max-semi-space-size=16`固定测试进程的V8半空间上限。本机Windows Node24默认策略在持续分配时将new_space保留空间从4MiB扩大至128MiB，而Go堆、原生请求和隧道已回收。该参数仅作用于独立测试进程，不修改SDK用户进程的运行配置，也不放宽RSS或句柄阈值。证据保存运行参数、V8空间快照，以及阈值判定之后的完整GC诊断；判定使用GC前的最终采样。

私有HTTPS代理示例：`new Session({ proxy: 'https://proxy.example:443', proxyCaFile: '/path/to/proxy-ca.pem', caFile: '/path/to/target-ca.pem' })`。两类CA分别验证代理与目标；请求可用`{ proxyCaFile: null }`恢复代理系统信任，或指定其它代理CA文件，均不会复用不相符的已有隧道。

代理长测按靶场握手阶段同步取消，避免固定毫秒计时在慢runner上提前中止TLS而未触达待测CONNECT阶段。HTTPS靶场延迟50毫秒处理CONNECT，确保该时序在本地也能覆盖。H2可合并同一代理的在途建连；每批每种scheme至少触发一次真实握手取消，并逐批核对启动/回收计数，不依赖请求数等于物理建连数。资源阈值保持不变。
