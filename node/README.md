# Fastls Node.js SDK

未发布的开发版本。Node.js 22+，TypeScript 类型声明、ESM 和 CommonJS 入口。
通过 Koffi 复用 fastls-ffi 的 C ABI v1，无需 Python 进程或 RPC 服务。
当前仅支持 Node.js 主线程；暂不支持 worker_threads、Electron、Bun 或浏览器。

## 本地构建与使用

先按上级 README 构建 Go 动态库，再在本目录执行：

```shell
npm ci
npm run build
```

源码默认查找仓库 `dist/`。安装到其它项目后，设置 `FASTLS_LIBRARY` 为动态库绝对路径，
或向 Session 传 `library`。当前 npm 包不内置 Go 动态库，不会自动下载或编译 Go 库。
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
- `body` 为字符串、Buffer 或 Uint8Array，完整上传上限 512 KiB。
- `timeoutMs` 为 1..86400000 的整数，默认 30000，覆盖发起请求到正文消费完成。
- 会话默认头和请求头按大小写不敏感方式合并，请求值优先，包括空字符串。
- `Response` 提供 `status`、`ok`、`headers`、`match`、`content: Buffer`，以及同步 `text()` / `json()`。
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
响应体保持底层库返回的编码，本 SDK 不主动解压；`text()` 按 UTF-8 解码，替换非法字节。

正常 EOF、读取异常或提前 `break` 会关闭请求。尚未开始迭代的响应需要显式 `close()`，
也可调用 `cancel()` 或中止 AbortSignal。AbortSignal 在等待响应头和正文读取期间都有效，
抛出 `AbortError`（`code: 'ABORT_ERR'`，`cause` 保留 signal.reason）。原生超时当前抛出 FastlsError。
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

## 调度与边界

网络由 Go goroutine 执行。SDK 通过共享约 10 ms 定时器轮询非阻塞 C 接口，
每轮限制处理数量和时间，并限制连续立即调用，防止热循环长时间占用事件循环。
这不是完成通知模型，不承诺固定吞吐量或延迟；初始化、原生控制调用及 JSON/Buffer 转换仍在主线程执行。

句柄使用 bigint，无损解析和序列化 ABI JSON 数字；返回的 C 字符串逐次由 fastls_free 释放。
加载时检查 ABI 版本和所需符号，动态库保持进程级引用，不主动卸载。
原生限制仍适用：最多 64 会话、256 未关闭请求，每会话最多 16 种传输配置。
首版不提供流式上传、代理、WebSocket、自动解压或分层超时选项。

## 验证

仓库中需先构建 `build/ffi-fixture[.exe]`（`go build -o build/ffi-fixture ./cmd/ffi-fixture`；Windows 使用 .exe 后缀）。

```shell
npm test
npm run test:package
```

测试使用真实动态库和本地靶场，覆盖 H1/H2、证书、连接池、Cookie、多值头、二进制、取消、超时、
异常正文、64 MiB 下载、32 MiB 缓存限制和 128 路并发。安装包测试在临时项目安装 npm tarball，
重新执行全部测试并检查包名导入和 TypeScript 声明。证据写入仓库 build/node-*.json。
Windows x64 / Node 22.23.4 和 24.18.0 的源码与隔离安装包各 16 项测试已通过。
已配置 Linux x64/ARM64、macOS Intel/ARM64 和 Windows x64 的原生 CI，远端结果以实际运行证据为准。
