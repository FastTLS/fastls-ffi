# Fastls Node.js SDK

> 发布状态（2026-10-27）：0.10.1 已正式发布至 npm，latest 指向该稳定版本。主包与五个平台原生包均已核对摘要。

Node.js 22+ 的 Fastls C ABI v1 绑定，提供 Promise API、TypeScript 声明、ESM 和 CommonJS 入口。本文对应正式版 **0.10.1**，发行文件与版本状态以 npm registry 为准。

支持 HTTP/1.1、HTTP/2、流式上传下载、双工、SSE、WebSocket、会话 Cookie 和 HTTP/HTTPS/SOCKS 代理。网络请求由 Go 执行，通过 Koffi 在当前 Node 进程中调用；使用者无需 Python 或 RPC 服务。

只支持 Node.js 主线程。暂不支持 worker_threads、Electron、Bun 或浏览器。默认校验证书；生成的客户端身份不代表已获得与真实浏览器完全相同的线上指纹。

- 用户 API 与安装方式：本文。
- Python 行为对应、语言差异与测试证据：[PARITY.md](https://github.com/FastTLS/fastls-ffi/blob/main/node/PARITY.md)。
- 维护者打包、首次发布和 Trusted Publisher：[NPM_RELEASE.md](https://github.com/FastTLS/fastls-ffi/blob/main/node/NPM_RELEASE.md)。
- 当前交付状态及后续批次：[NEXT_PHASE.md](https://github.com/FastTLS/fastls-ffi/blob/main/node/NEXT_PHASE.md)。

## 安装与快速开始

安装指定版本：

```sh
npm install @fastls/fastls@0.10.1
```

主包通过同版本的可选依赖自动安装当前平台的 Fastls 原生库。保留 optional dependencies；支持 Windows x64、Linux glibc x64/arm64、macOS x64/arm64。Linux musl 和其它架构没有内置包。具体系统最低版本见原生包的 `native/manifest.json`。

npm 分发包没有 Fastls 编译或下载脚本；安装成功后不需要 Go、Python、C 编译器或 `FASTLS_LIBRARY`。Koffi 自身也使用对应平台的原生组件。

```js
import { Session } from '@fastls/fastls';
// CommonJS: const { Session } = require('@fastls/fastls');

const session = new Session({ profile: 'chrome/156/windows', timeoutMs: 30_000 });
try {
  const response = await session.get('https://example.com', {
    headers: { 'User-Agent': 'my-client/1.0' },
    signal: AbortSignal.timeout(5_000),
  });
  response.raiseForStatus();
  console.log(response.status, response.url, response.httpVersion, response.text());
} finally {
  await session.closeAsync();
}
```

后续片段假定 `session` 已创建且未关闭；`consume`、`produceBytes` 等是应用自行实现的业务函数。每次实际使用均应关闭会话和未消费完的响应。

源码构建、测试和发包见 [开发指南](DEVELOPMENT.md)。普通 npm 用户不需要这些步骤。

## 设置浏览器请求头

Python 0.1.4 / Node 0.10.1 所用的默认 `chrome/156/windows` 配置没有为 `Accept-Encoding`、`Accept-Language` 和 `Priority` 设置默认值。下面的示例显式补充它们；其它配置的默认值可能不同。

```js
import { fetch } from '@fastls/fastls';

const response = await fetch('https://tls.peet.ws/api/all', {
  profile: 'chrome/156/windows',
  headers: {
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
    'Priority': 'u=0, i',
  },
});
if (!response.ok) throw new Error(`HTTP ${response.status}`);
console.log(await response.json());
```

`Accept-Language` 表示语言偏好，请按应用需要调整；`Priority` 的值也与请求场景有关，示例不是所有浏览器请求的固定模板。请求头顺序配置只排列已有字段，不会为其中的字段自动生成值。支持自动解压 gzip/deflate/br/zstd，不等于会自动发送对应的 `Accept-Encoding`。显式传入的头值优先于配置默认值；复用会话时，也可通过 Session 的 `headers` 设置。

## 从 Node fetch 迁移

新增 `fetch` 入口沿用 Node 原生 fetch 的主要调用习惯，并增加指纹、代理和证书配置：

```js
import { fetch } from '@fastls/fastls';

const response = await fetch('https://tls.peet.ws/api/all', {
  profile: 'chrome/156/windows',
  headers: new Headers({ Accept: 'application/json' }),
  signal: AbortSignal.timeout(10_000),
});
if (!response.ok) throw new Error(`HTTP ${response.status}`);
console.log(await response.json());
```

接受字符串、URL、Request，以及标准 Headers、FormData、URLSearchParams、Blob、字节和可读流正文。流式请求使用 `duplex: 'half'`，一次性正文不会为 307/308 自动重放。返回 Node 标准 `Response`，在响应头到达时返回；`text/json/arrayBuffer()` 为异步方法，`body` 为 Web ReadableStream，`ok` 为 200–299。正文只消费一次；不用时必须 `await response.body?.cancel()`。另附只读 `match` 和 `httpVersion`。

顶层 `fetch` 与 Node 一样不保存 Cookie，每次请求的临时资源会在正文读完或取消后关闭。需要 Cookie 和连接长期复用时，使用 `session.fetch(url, options)`，最后 `await session.closeAsync()`。会话级 `library/caFile` 应在构造 Session 时设置。HTTP 错误状态仍返回响应；`redirect` 支持 `follow/manual/error`。默认最多跟随 30 次，可设置 `maxRedirects`。

这不是浏览器环境：不实现 CORS、缓存、自动认证、referrer、integrity 或 keepalive 策略。显式传入 `cache/credentials/integrity/keepalive/mode/referrer/referrerPolicy` 会报错。默认校验 TLS，不支持关闭验证；不接收 Node http/https Agent、dispatcher 或 Axios 配置。超时以毫秒计，使用 `timeoutMs` 或 AbortSignal。

Fastls 原有 `Session.request/stream/duplex/sse/websocket` 保留，适合需要分阶段超时、原始字节、Trailer 和更精细资源控制的场景。Cookie、重定向和默认参数由 JavaScript SDK 管理；动态库负责网络传输、指纹、证书与连接池。旧 C Cookie 接口仅供旧客户端兼容，新 SDK 不使用原生 Cookie 存储。

## 请求和响应

- `await session.request(method, url, options)`，以及 `get/post/put/patch/delete/head/options`，返回完整缓存的 Response。
- 请求选项包括 `profile`、`headers`、`trailers`、`params`、`json`、`form`、`files`、`body`、`timeoutMs`、`allowRedirects`、`maxRedirects`、`signal`、`proxy`、`proxyCaFile`、`decodeContent`、`maxDecodedBytes`、`duplex`。会话级选项还包括 `library`、`caFile`、`cookies`、`notifications`；以公开 TypeScript 类型为准。
- `body` 为字符串、Buffer、Uint8Array、同步/异步字节迭代器、Node Readable 或接收 AbortSignal 的生产者工厂。超过512 KiB的字符串/字节自动使用上传通道。
- `timeoutMs` 为毫秒整数或 `{ total, connect?, write?, headers?, read? }`，每项范围1..86400000；默认总时限30000。请求级数字覆盖会清除会话分层限制，结构化覆盖替换整份策略。
- 会话默认头和请求头按大小写不敏感方式合并，请求值优先，包括空字符串。
- `Response` 提供 `status`、`ok`、`headers`、`wireHeaders`、`decoded`、`match`、`httpVersion`、`url`、`trailers`、`content: Buffer`，以及同步 `text()` / `json()`。

`ok` 表示状态小于400，与Python一致，因此302/304也为true。
`raiseForStatus()` 只对400–599抛出 `FastlsHTTPError`，异常包含原响应 `response` 和 `status`，消息仅含状态码；流式响应可在读取正文前检查，随后仍需关闭。
`encoding` 从Content-Type的charset初始化，缺省utf-8，可修改；`text()`替换坏字节，未知编码报错。Latin-1保留完整字节映射，其它字符集使用Node内置TextDecoder的WHATWG编码表，不自动猜测。
`json()`直接读取原始字节，支持UTF-8/16/32及BOM，独立于encoding；JSON数字仍受JavaScript数字精度限制。
- 4xx/5xx 仍返回 Response；参数、网络、TLS 和原生错误抛出异常。
- `Headers.get(name)` 返回第一个值或 undefined；`getAll(name)` 保留所有重复值，不按逗号拆分。
  `entries()` / 默认迭代返回每个字段的首值，`multiItems()` 返回全部字段值。
- `listProfiles()` 返回规范 ID；`stats()` 返回 Go 运行时只读快照，其统计范围是该动态库实例，不仅是当前会话。

未知 profile 报错；Match 保留生成身份和协议基线信息，不承诺与真实浏览器指纹完全等价。

`request('PUT', url, options)` 等可使用其它合法方法；便捷方法包括 `get/post/put/patch/delete/head/options`。请求头的值为字符串；`trailers` 的值为字符串或只读字符串数组，名称和值由原生层继续验证，且要求 `request_trailers` 能力。

`url` 是最终响应 URL，`httpVersion` 是实际 HTTP 协议版本。缓存响应已经读到 EOF，包含最终 `trailers`；流式响应先消费正文，再读取最终尾部字段。提前关闭不代表已经收到全部 trailers。

## 请求参数

```ts
const response = await session.get('https://example.com/search?old=a%20b#fragment', {
  params: [['q', '中文'], ['tag', ['a', 'b']], ['empty', '']],
});
response.raiseForStatus();
await session.post('https://example.com/api', { json: null });
await session.post('https://example.com/form', { form: { tag: ['a', 'b'], enabled: true } });

// FileHandle归调用方拥有；适配read签名，从当前位置读取，始终由调用方关闭。
const file = await open('/path/to/archive.bin', 'r'); // import { open } from 'node:fs/promises'
try {
  await session.post('https://example.com/upload', {
    form: { description: '附件' },
    files: { file: ['archive.bin', {
      async read(maxBytes, signal) {
        signal.throwIfAborted();
        const buffer = Buffer.allocUnsafe(maxBytes);
        const { bytesRead } = await file.read(buffer, 0, maxBytes, null);
        return buffer.subarray(0, bytesRead);
      },
    }, 'application/octet-stream'] },
  });
} finally { await file.close(); }
```

- `params`/`form`接受普通对象、Map或键值对数组。列表值展开成重复键；null/undefined值省略，空字符串保留；布尔值与Python一致为True/False，Unicode用UTF-8，空格为+。键可用字符串/Uint8Array，值可用字符串/Uint8Array、有限数或布尔值。与Python不同，查询及表单拒绝非有限数。
- `params`另接受不含#的已编码ASCII字符串，原查询编码和fragment保留。fragment不发送到服务器。
- `json`只接受严格JSON值、普通对象及数组，拒绝NaN/Infinity、undefined、bigint、稀疏数组、循环和toJSON。显式null发送null；省略json无正文。`json`与body/form/files冲突；`body`与form/files冲突。不要把Python的data映射直接传给body；Node用form显式编码，现有字节块数组仍是原始body。
- JSON/表单在内存中编码；超过512KiB自动转为流式上传，不是大型数据增量编码器。默认Content-Type仅在没有会话/请求自定义头时设置；编码正文拒绝Content-Length/Transfer-Encoding。原始body沿用原有规则。
- `files`接受对象、Map或重复字段对数组。每个文件是Uint8Array、`MultipartReader`，或`[filename, body, contentType?]`；filename为null省略filename参数。默认从读取器name/字段名取文件名；内置固定MIME推断只覆盖txt/json/html/csv/png/jpg/jpeg/pdf，其它类型请显式指定，缺省application/octet-stream。不支持自定义分部头。
- multipart可配合form字段，每次read最多64KiB，空Uint8Array表示EOF；支持同步/Promise读取器。无需seek/长度/临时文件，不全量缓存文件；调用方负责close和重新定位，成功、失败、取消均保留读取器所有权。提供给read的AbortSignal可中断自定义等待；不可取消的Promise会延后closeAsync清理。Node Readable及FileHandle不能直接作为此读取器，应适配其读取接口；现有原始body的Readable销毁规则不变。
- multipart管理随机boundary及Content-Type，拒绝会话或请求中显式Content-Type；元数据拒绝控制字符。正文仅使用一次，307/308不自动重放，301/302/303按 SDK 规则转换方法和丢弃正文。
- request/get/post/stream/sse与自动duplex共享参数处理；multipart不支持duplex。手动duplex只接受params，正文继续send()/finishUpload()。自动双工支持JSON/表单且关闭重定向。

## 流式下载和取消

```js
const controller = new AbortController();
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
默认在Go侧逐块解码gzip、deflate、br及zstd，`decodeContent: false`取得原始字节；`text()` 按响应 `encoding` 解码，缺省 UTF-8，替换非法字节；JSON 解码规则与缓存响应相同。
解码时headers移除Content-Encoding/Content-Length，wireHeaders保留线上字段；decoded表示解码视图。
`maxDecodedBytes`默认256 MiB，范围1..1 TiB，只限制解码输出；普通缓存32 MiB限制仍独立适用。未知编码、校验失败、截断或超限抛出FastlsDecodeError。

正常 EOF、读取异常或提前 `break` 会关闭请求。尚未开始迭代的响应需要显式 `close()`，
也可调用 `cancel()` 或中止 AbortSignal。AbortSignal 在等待响应头和正文读取期间都有效，
抛出 `AbortError`（`code: 'ABORT_ERR'`，`cause` 保留 signal.reason）。超时抛出 FastlsTimeoutError，其phase为connect/write/headers/read/total。解码错误、超时错误和AbortError均继承FastlsError。
`session.close()` 取消并关闭所有活动请求，重复关闭无害；不依赖 GC 清理资源。

## 连接池、Cookie 和证书

Session 复用原生会话的 H1 keep-alive 和 H2 多路复用；不同 Session 隔离。
`session.closeIdleConnections()` 释放空闲连接，保留 Cookie。
Cookie 自动管理和重定向处理均由 JavaScript SDK 完成；原生上下文只负责传输：

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

## 代理

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

Session和请求的`proxy`接受URL字符串或null。请求省略或undefined继承会话默认值；null或空字符串直连。普通请求、stream、sse、自动/手动duplex遵循相同规则。传入其它类型会报TypeError。显式代理要求原生`proxy_tunnels`能力；当前 SDK 始终要求 transport_context_v1；缺少代理能力时，代理请求在登记句柄前报错。

`http://`对HTTP和HTTPS目标均使用HTTP/1.1 CONNECT，默认端口80。`socks5://`本机解析目标域名，`socks5h://`交给代理解析，默认端口1080。支持HTTP Basic和SOCKS用户名/密码认证，凭据按URL规则转义，例如密码中的@写成%40。不读取HTTP_PROXY、HTTPS_PROXY、ALL_PROXY、NO_PROXY及其小写形式。`https://`在代理TLS中运行HTTP/1.1 CONNECT，默认端口443，证书和主机名验证始终开启，不回退到明文。HTTPS代理另要求原生`https_proxy_tls`能力；暂不支持SOCKS4或PAC，详情见上级PROXY.md。

`proxyCaFile`只控制HTTPS代理信任，`caFile`只控制目标信任，两者互不代替。未设置代理CA使用系统根；提供文件则替换系统根。构造Session时读取默认代理CA一次，请求指定路径时重新读取；请求undefined继承默认，null恢复系统根。直连、HTTP和SOCKS忽略代理CA默认值及请求覆盖。代理地址及凭据、profile、代理CA和目标CA共同隔离连接池。目标证书仍需验证；目标请求禁止设置Proxy-Authorization和Proxy-Connection。认证只用于代理握手，错误消息不回显凭据或代理任意响应短语。握手计入connect和total预算，AbortSignal取消会关闭未建立的隧道；取消单条H2流继续复用健康的共享隧道。代理拒绝后不降级或自动重试。

代理测试使用Node测试进程中的独立、仅访问loopback的CONNECT/RFC1928/RFC1929靶场；SDK本身不依赖Python或代理辅助进程。16 组 Python 行为映射由源码、内置库候选及 npm 拆分包统一验收；语言适配见PARITY.md。

`npm run test:proxy:soak`默认以32路并发运行60秒，轮换正常请求、共享H2流取消、错误认证和停滞握手取消；四种scheme分别使用真实loopback代理。FASTLS_NODE_PROXY_SOAK_SECONDS可设为1..300。Linux当前Go/Node组合执行该独立测试，沿用现有RSS、Go堆、goroutine及句柄阈值，逐批记录写入build/node-proxy-recovery-*.json，不计入231项主套件。

代理独立长测与SSE长测一样，显式使用`--max-semi-space-size=16`固定测试进程的V8半空间上限。本机Windows Node24默认策略在持续分配时将new_space保留空间从4MiB扩大至128MiB，而Go堆、原生请求和隧道已回收。该参数仅作用于独立测试进程，不修改SDK用户进程的运行配置，也不放宽RSS或句柄阈值。证据保存运行参数、V8空间快照，以及阈值判定之后的完整GC诊断；判定使用GC前的最终采样。

私有HTTPS代理示例：`new Session({ proxy: 'https://proxy.example:443', proxyCaFile: '/path/to/proxy-ca.pem', caFile: '/path/to/target-ca.pem' })`。两类CA分别验证代理与目标；请求可用`{ proxyCaFile: null }`恢复代理系统信任，或指定其它代理CA文件，均不会复用不相符的已有隧道。

代理长测按靶场握手阶段同步取消，避免固定毫秒计时在慢runner上提前中止TLS而未触达待测CONNECT阶段。HTTPS靶场延迟50毫秒处理CONNECT，确保该时序在本地也能覆盖。H2可合并同一代理的在途建连；每批每种scheme至少触发一次真实握手取消，并逐批核对启动/回收计数，不依赖请求数等于物理建连数。资源阈值保持不变。

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
  const reading = exchange.response.then(response => consumeResponse(response.body));
  const writing = (async () => {
    await exchange.send(Buffer.from('first'));
    await exchange.finishUpload();
  })();
  await Promise.all([reading, writing]);
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

## WebSocket

```ts
const ws = await session.websocket('wss://example.com/chat', {
  subprotocols: ['chat.v1'], handshakeTimeoutMs: 30000,
  readTimeoutMs: null, writeTimeoutMs: 30000, closeTimeoutMs: 1000,
  maxMessageBytes: 1024 * 1024,
});
try {
  await ws.send('你好');
  const message = await ws.recv(); // string或Buffer，空消息也是有效消息
} finally { await ws.closeAsync(1000, 'done'); }
```

send/ping/pong等到原生写入完成；同时最多一名读者和一名写入者，重叠调用立即报错。recv返回完整消息，入站分片重组由原生完成。默认1MiB消息上限，可设置1..16MiB；控制帧最多125字节。Node组装、复制和返回当前消息另占有界内存，业务持有的消息不计入原生队列。不是无限文件流接口。

正常远端关闭抛出WebSocketClosed(code/reason)，连接保留closeCode/closeReason。closeAsync的整个握手受closeTimeoutMs约束（最多60000），超时直接释放；abort立即中断。连接级AbortSignal中断当前及后续操作，Session关闭回收全部所属WS。读/写/握手超时使连接终止，FastlsTimeoutError.phase为read/write/handshake；HTTP会话总超时不限定WS寿命。

复用Session的profile、头、CA、代理与代理CA；params、请求级覆盖/显式直连沿用HTTP规则。wss始终验证证书和主机名，ALPN适配HTTP/1.1；不承诺适配后的Upgrade头序与浏览器完全一致。使用websocket_v1和三个可选ABI符号，当前 SDK 还要求 transport_context_v1；ABI 号相同不能代替能力检查。通知默认共享一条会话连接，poll可显式选择，不阻塞主线程或增加网络线程。

默认与 HTTP 共享 SDK Cookie 罐；原生库不保存 Cookie。cookieMode:"manual" 可禁用本次握手的自动 Cookie 收发。显式Cookie头（包括空值）优先，默认仍接受握手Set-Cookie；Session的cookies:false禁用自动收发。域/路径/Secure/过期规则与HTTP一致。有效101及普通HTTP拒绝/重定向响应可写回；无效101或TLS失败不写回。Authorization/Origin由调用方设置。无压缩、H2/H3 CONNECT、握手重定向、自动心跳/重连或消息重放。ping只证明写入完成，可用info.websocket.pongs/last_pong_base64观察响应。协议细节与原生资源限制见根目录WEBSOCKET.md。

npm run test:quick先运行类型、清单及WS/原生用例；test:ws针对25项WS和16项共享Cookie；test:ws:soak运行独立60秒/32路资源回收。快速反馈不能替代231项源码/安装包完整回归与六组平台验收。

0.10.1 SDK 使用不含 Cookie 的原生传输上下文，并在 JavaScript 中完成 Cookie 选择及响应更新。旧原生 Cookie 能力只为历史客户端保留。npm run test:compat 验证当前完整库的收发、显式头和失败握手。

## 原生就绪通知

默认使用`notifications: 'auto'`：新库带session_notifications能力且符号完整时启用，缺少通知能力时轮询，但库仍须满足 transport_context_v1 等 SDK 基础能力。`required`缺少能力时在创建原生会话前报错；`poll`显式使用原调度器。

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
显式 poll 或配套库缺少通知能力时使用共享约 10ms 定时器，保留每轮处理数量及时间上限。

句柄使用 bigint，无损解析和序列化 ABI JSON 数字；返回的 C 字符串逐次由 fastls_free 释放。
外部动态库基础能力要求为 `transport_context_v1`、`stream_decode`、`phase_timeouts`、`stream_upload`、`write_timeout`；双工、代理、WebSocket 和请求 trailers 在调用时另检查对应能力。加载时检查 ABI 及所需能力，并使用系统级固定加载（Windows PIN / POSIX NODELETE），避免Node环境清理时卸载仍有Go后台线程的动态库。
原生限制仍适用：最多 64 会话、256 未关闭请求，每会话最多 16 种传输配置。
代理支持 HTTP/HTTPS CONNECT、socks5 及 socks5h，见代理章节；WebSocket见对应章节。

## 与 Python 的语言差异

Node统一使用Promise API，不提供Python同步Session或共享同步会话多线程调用；对应场景采用同步迭代器输入和96路Promise请求验证，不宣称线程安全。Node Readable按自身流的生命周期管理文件句柄，区别于Python保留调用方文件对象。
JavaScript无法强行中断任意Promise：取消会立即释放原生请求，并请求迭代器return/destroy；生产者正在等待的自定义I/O必须支持取消，才能保证其finally及时执行。推荐使用接收AbortSignal的上传工厂。Python的协程强制取消场景对应为工厂信号取消，报告中明确标记适配。

## 文档、许可证与使用声明

[完整 Node 文档](https://fasttls.github.io/node/) · [Apache-2.0](LICENSE)。原生第三方声明随平台包提供。

## 使用边界与免责声明

仅在拥有权限的系统、数据和网络上使用，并遵守适用法律、服务条款及隐私要求。软件按许可证约定以“现状”提供，不承诺可用性、特定用途适用性、第三方服务放行、匿名性或与真实浏览器完全一致。浏览器名称和版本用于描述配置，不表示获得相应厂商认证或背书。部署方负责访问控制、密钥和个人信息保护、请求限额及数据备份；担保排除与责任限制以随附许可证及适用法律为准。本说明不改变许可证授予的权利或新增许可限制。


## 动态库选择与安装排错

加载优先级：显式 SessionOptions.library → FASTLS_LIBRARY → 包内 manifest → 同版本平台可选依赖；源码目录可回退仓库 dist，已安装 thin 包不回退仓库。普通 npm 用户应保留 optionalDependencies，不必设置外部库路径。外部旧库可能覆盖新包，ABI 号相同也不保证扩展能力相同。Linux glibc 与 macOS 最低版本、摘要及动态依赖以 native/manifest.json 为准。更多排错见 [在线帮助](https://fasttls.github.io/help/)。
