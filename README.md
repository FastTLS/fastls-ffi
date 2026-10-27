# Fastls Python / Node.js SDK

[English documentation](https://fasttls.github.io/en/guide/)

[完整文档](https://fasttls.github.io/) · [Python 使用指南](python/README.md) · [Node 使用指南](node/README.md)

在 Python 或 Node.js 进程内使用 Fastls 原生 HTTP 客户端，不需要运行 RPC 服务。平台包内置动态库，普通安装无需 Go 或 C 编译器。

## Python：安装与使用

需要 Python 3.10+；当前稳定版为 `fastls==0.1.4`，发行名和导入名均为 `fastls`。

```sh
python -m pip install --only-binary=:all: fastls==0.1.4
```

```python
import fastls

response = fastls.get("https://example.com/", profile="chrome/156/windows")
response.raise_for_status()
print(response.status_code, response.text)
```

支持 Windows x64、Linux glibc 2.39+ x64/ARM64、macOS 15+ Intel/Apple Silicon；不提供 musl/Alpine、Windows ARM64 或其它平台的内置 wheel。从旧 `fastls-ffi` 迁移前先卸载旧包，两者不可共存。安装验证应实际创建 Session，而不只是 import，详见 [安装指南](INSTALL.md)。

0.1.4 新增 `fastls.request()` / `fastls.get()` 等一次性请求入口，自动关闭临时会话，并提供 IDE 参数和响应类型提示。连续请求、连接复用或登录态请使用 [Session](python/README.md#同步请求)。

## Node.js：安装与使用

需要 Node.js 22+。以下命令安装正式版 `@fastls/fastls@0.10.1`。

```sh
npm install @fastls/fastls@0.10.1
```

```js
import { Session } from '@fastls/fastls';
// CommonJS: const { Session } = require('@fastls/fastls');

const session = new Session({ profile: 'chrome/156/windows', timeoutMs: 30_000 });
try {
  const response = await session.get('https://example.com/');
  response.raiseForStatus();
  console.log(response.status, response.text());
} finally {
  await session.closeAsync();
}
```

支持 ESM、CommonJS、TypeScript，以及 Windows x64、Linux glibc x64/ARM64、macOS x64/ARM64 平台包。保留 npm optional dependencies；系统最低版本以包内 native/manifest.json 为准。只支持 Node 主线程，不支持 worker_threads、Electron、Bun 或浏览器。GitHub 历史 `@fasttls/fastls` 候选不是 npm 的 `@fastls/fastls`。

## 支持什么

- HTTP/1.1、HTTP/2、浏览器配置、Cookie、HTTP/HTTPS CONNECT 和 SOCKS5/SOCKS5h 代理。
- Python 同步/asyncio；Node Promise；请求参数、JSON、表单、文件与流式上传下载。
- 超时与取消、自动解压、请求/响应 Trailer、SSE、WS/WSS 和 HTTP/2 全双工。
- 默认校验证书和主机名，可独立设置目标与 HTTPS 代理的 CA。

## 限制与资源管理

- 不开放完整自定义 TLS Spec，不承诺 requests/httpx/fetch 的完整兼容，也不提供完整浏览器运行时。
- 普通 HTTP 响应缓存上限 32 MiB，更大正文使用 stream；解压后默认上限 256 MiB，流式读取也受约束。
- Python 超时单位为秒，Node 为毫秒；HTTP 错误状态通过 raise_for_status()/raiseForStatus() 主动检查。
- Session、流式响应、SSE、WebSocket 必须关闭；同一响应仅允许一个读取者。
- WebSocket 不支持压缩、自动重连或 H2/H3 Extended CONNECT；SSE 不自动重连。
- SDK 文档的正式功能范围为 H1/H2，不因 Go 核心存在 H3 路径就承诺 SDK 提供 H3 指纹能力。
- ABI 扩展按能力检测；显式加载外部库时须匹配 SDK，不能只检查 ABI 版本号。

## 更多用法

[Python 完整指南](python/README.md) · [Node 完整指南](node/README.md) · [安装排错](INSTALL.md) · [代理](PROXY.md) · [流式与超时](STREAMING_TIMEOUTS.md) · [上传](STREAMING_UPLOAD.md) · [双工](DUPLEX.md) · [SSE](SSE.md) · [WebSocket](WEBSOCKET.md)

维护者入口：[源码开发](DEVELOPMENT.md) · [C ABI](ABI.md) · [CI](CI.md)。

## 详细文档与问题反馈

- [使用文档](https://fasttls.github.io/guide/)：安装、功能、参数与排错。
- [版本与发布说明](https://github.com/FastTLS/fastls-ffi/releases)：各版本的变更和下载文件。
- [问题反馈](https://github.com/FastTLS/fastls-ffi/issues)：请提供版本、系统架构、脱敏后的最小示例与错误；不要提交凭据、Cookie、私钥或个人数据。

## 开源协议与第三方声明

自有代码采用 [Apache-2.0](LICENSE)。Python 分发的第三方声明见 [THIRD_PARTY_LICENSES.txt](python/THIRD_PARTY_LICENSES.txt)，Node 原生依赖声明及摘要随平台包提供。第三方材料保留原授权。

## 使用边界与免责声明

仅在拥有权限的系统、数据和网络上使用，并遵守适用法律、服务条款及隐私要求。软件按许可证约定以“现状”提供，不承诺可用性、特定用途适用性、第三方服务放行、匿名性或与真实浏览器完全一致。浏览器名称和版本用于描述配置，不表示获得相应厂商认证或背书。部署方负责访问控制、密钥和个人信息保护、请求限额及数据备份；担保排除与责任限制以随附许可证及适用法律为准。本说明不改变许可证授予的权利或新增许可限制。


## 本地构建

源码开发、编译器要求及 ABI 说明已整理到 [DEVELOPMENT.md](DEVELOPMENT.md) 和 [ABI.md](ABI.md)。普通 wheel/npm 用户无需构建。
