# HTTP CONNECT 与 SOCKS5 代理

Python 源码与 wheel 共用 C ABI 的 `proxy_tunnels` 能力。目标证书验证始终开启，浏览器配置保持原来的选择方式。

```python
from fastls import Session, AsyncSession, Timeout

with Session(profile="chrome/156/windows",
             proxy="http://user:password@127.0.0.1:8080") as session:
    response = session.get("https://example.com/")
    direct = session.get("https://example.com/", proxy=None)
    other = session.get("https://example.com/",
                        proxy="socks5h://127.0.0.1:1080",
                        timeout=Timeout(total=30, connect=5))

# AsyncSession、stream、sse 和 duplex 同样接受 proxy 参数。
```

## 明确的行为

- `http://` 使用 HTTP/1.1 CONNECT 隧道，默认代理端口 80。HTTP 与 HTTPS 目标均使用 CONNECT；代理必须允许目标端口，不使用普通正向代理的 absolute-form 请求，也不在拒绝后降级或重试。
- `socks5://` 使用 SOCKS5 CONNECT，本机解析目标域名，优先选择返回的 IPv4 地址，其次使用 IPv6；首版不遍历多个解析结果重试。`socks5h://` 把目标域名交给代理解析。IP 字面量直接传 IP。两者默认代理端口 1080。
- HTTP 代理支持 URL 用户名/密码对应的 Basic 认证；SOCKS5 支持无认证及用户名/密码认证。配置了 SOCKS5 凭据时要求用户名/密码方法，不降级为无认证。用户名和密码按 URL 规则转义，例如密码中的 `@` 写为 `%40`。
- `Session(proxy=...)` 是会话默认值。请求省略参数时继承；`proxy=None` 或 `proxy=""` 显式直连。不会读取 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY 或 NO_PROXY。
- 代理地址及凭据、浏览器配置和目标 CA 信任共同参与连接池隔离。不同代理或凭据不会借用已有隧道；cookie 仍按同一 Session 的目标域名规则管理。沿用每会话最多 16 组传输配置的限制。
- CONNECT/认证握手受连接阶段及总时间预算限制，取消关闭尚未建立的隧道。隧道建立后由 HTTP 传输层管理共享连接，取消单条 HTTP/2 流不会主动关闭其它流。会话关闭释放全部隧道。
- 代理认证只发送到代理握手，不发送到目标。目标请求禁止手动设置 Proxy-Authorization 和 Proxy-Connection，请通过代理 URL 配置凭据。参数与握手错误不回显代理用户名/密码或代理响应的任意原因短语。
- CONNECT 成功响应头最多读取约 64 KiB，并保留预读的隧道字节；此限制不限制后续响应正文。流式正文继续使用既有有界缓冲。

本阶段不开放 `https://` 代理、SOCKS4、PAC、代理规则或自动环境代理。HTTPS 代理需要独立的代理 CA/主机名验证契约及验收，不能使用旧核心分支的跳过证书校验行为作为 SDK 支持依据。这里的 HTTP/2 指目标隧道内的协议，不承诺 HTTP/2 CONNECT 代理。

## C ABI

`fastls_features` 包含 `proxy_tunnels`。`fastls_start` JSON 新增可选字符串 `proxy`，省略或空字符串表示直连。C ABI 没有隐式代理默认值；语言绑定负责把默认代理附加到请求。未知/不支持的 scheme、无效地址及凭据在请求登记前拒绝。现有函数签名和 ABI 版本不变。

## 验证

`tests/proxy_lab.py` 是独立、仅访问 loopback 的 HTTP CONNECT/RFC1928/RFC1929 靶场。`tests/test_proxy.py` 验证三种 scheme、认证成功/失败、请求覆盖、连接复用及凭据隔离、DNS 位置、HTTP/H1 TLS/H2 TLS、目标 CA 校验、握手停滞、连接/总超时、取消、64 MiB 同步/异步上传下载、慢消费者、SSE、H2 双工与共享隧道取消隔离。

```text
python tests/test_proxy.py
python scripts/test_wheel.py --proxy
```

六组原生 Actions 分别执行源码和 wheel 套件，保存 `stability-proxy-*.json` 及完整日志。核心额外验证握手取消关闭 socket、CONNECT 响应头上限、认证与预读数据、SOCKS 地址类型和凭据错误脱敏。结果以对应提交的 Actions 及产物为准。

## Node.js

Node 0.7使用相同的`proxy_tunnels`能力，无新增ABI函数。`new Session({proxy: url})`设置默认代理；`session.get(url, {proxy: null})`显式直连；省略或undefined继承默认值，空字符串也直连。请求覆盖同样适用于stream、sse和duplex。

源码及内置库安装包各执行10项对应代理用例，保留Python原测试ID；Node用同步/异步生产者和即时/慢速消费者覆盖Python的同步/异步组合。代理靶场由Node测试代码独立实现，安装验收继续清除工具搜索路径并禁用安装脚本。完整范围与类型示例见[node/README.md](node/README.md)及[node/PARITY.md](node/PARITY.md)。
