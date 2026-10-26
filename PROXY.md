# HTTP/HTTPS CONNECT 与 SOCKS5 代理

Python 源码与 wheel 共用 C ABI 的 `proxy_tunnels` 和 `https_proxy_tls` 能力。代理与目标证书分别验证，浏览器配置保持原来的选择方式。

```python
from fastls import Session, AsyncSession, Timeout

with Session(profile="chrome/156/windows",
             proxy="https://user:password@proxy.example:8443",
             proxy_ca_file="/path/to/private-proxy-ca.pem") as session:
    response = session.get("https://example.com/")
    direct = session.get("https://example.com/", proxy=None)
    other = session.get("https://example.com/",
                        proxy="socks5h://127.0.0.1:1080",
                        timeout=Timeout(total=30, connect=5))

# AsyncSession、stream、sse 和 duplex 同样接受 proxy 参数。
```

## 明确的行为

- `http://` 使用 HTTP/1.1 CONNECT 隧道，默认代理端口 80。HTTP 与 HTTPS 目标均使用 CONNECT；代理必须允许目标端口，不使用普通正向代理的 absolute-form 请求，也不在拒绝后降级或重试。
- `https://` 先验证代理的 TLS 证书与代理主机名，再发送 HTTP/1.1 CONNECT，默认端口 443，最低 TLS 1.2。没有跳过验证选项，也不会在 TLS 失败后回退到明文。外层代理使用标准 Go TLS 与 HTTP/1.1 ALPN，内层目标连接仍使用所选 Chrome 等配置，允许目标 HTTP/2 多路复用与双工。
- `proxy_ca_file` 只控制代理 TLS 信任，`ca_file` 只控制目标信任；两者互不代替。代理 CA 未设置时使用系统根；设置后使用文件中的证书替代系统根。会话构造时读取默认文件一次；请求显式传文件时重新读取，省略参数继承默认，显式 `proxy_ca_file=None` 恢复系统根。直连、HTTP 或 SOCKS 代理忽略代理 CA 默认值。
- `socks5://` 使用 SOCKS5 CONNECT，本机解析目标域名，优先选择返回的 IPv4 地址，其次使用 IPv6；首版不遍历多个解析结果重试。`socks5h://` 把目标域名交给代理解析。IP 字面量直接传 IP。两者默认代理端口 1080。
- HTTP 代理支持 URL 用户名/密码对应的 Basic 认证；SOCKS5 支持无认证及用户名/密码认证。配置了 SOCKS5 凭据时要求用户名/密码方法，不降级为无认证。用户名和密码按 URL 规则转义，例如密码中的 `@` 写为 `%40`。
- `Session(proxy=...)` 是会话默认值。请求省略参数时继承；`proxy=None` 或 `proxy=""` 显式直连。不会读取 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY 或 NO_PROXY。
- 代理地址及凭据、代理 CA、浏览器配置和目标 CA 信任共同参与连接池隔离。改变 CA 不会借用旧信任配置建立的隧道；cookie 仍按同一 Session 的目标域名规则管理。沿用每会话最多 16 组传输配置的限制。
- CONNECT/认证握手受连接阶段及总时间预算限制，取消关闭尚未建立的隧道。隧道建立后由 HTTP 传输层管理共享连接，取消单条 HTTP/2 流不会主动关闭其它流。会话关闭释放全部隧道。
- 代理认证只发送到代理握手，不发送到目标。目标请求禁止手动设置 Proxy-Authorization 和 Proxy-Connection，请通过代理 URL 配置凭据。参数与握手错误不回显代理用户名/密码或代理响应的任意原因短语。
- CONNECT 成功响应头最多读取约 64 KiB，并保留预读的隧道字节；此限制不限制后续响应正文。流式正文继续使用既有有界缓冲。

本阶段不开放 SOCKS4、PAC、代理规则或自动环境代理。这里的 HTTP/2 指目标隧道内的协议，HTTP/HTTPS 代理本身使用 HTTP/1.1 CONNECT，不支持仅接受 HTTP/2 CONNECT 的代理。

## C ABI

`fastls_features` 包含 `proxy_tunnels`。`fastls_start` JSON 新增可选字符串 `proxy`，省略或空字符串表示直连。C ABI 没有隐式代理默认值；语言绑定负责把默认代理附加到请求。未知/不支持的 scheme、无效地址及凭据在请求登记前拒绝。现有函数签名和 ABI 版本不变。

HTTPS 代理另需 `https_proxy_tls` 能力。可选 `proxy_ca_pem` 字符串用于代理根证书；空字符串使用系统根。该字段只有在 `proxy` 为 HTTPS 时才允许非空，格式无效时在请求登记前拒绝；不会复用 `ca_pem` 作为代理根。Go 对应配置为 `Options.ProxyTLSRootCAs`，`NewSession` 克隆该证书池。

## 验证

`tests/proxy_lab.py` 是独立、仅访问 loopback 的 TLS/HTTP CONNECT/RFC1928/RFC1929 靶场。`tests/test_proxy.py` 的14项测试覆盖四种 scheme、认证成功/失败、请求覆盖、连接复用及凭据隔离、DNS 位置、HTTP/H1 TLS/H2 TLS、握手停滞、连接/总超时、取消、64 MiB 同步/异步上传下载、慢消费者、SSE、H2 双工与共享隧道取消隔离。代理使用独立于目标的临时 CA，并验证未知 CA、错误代理主机名、目标与代理根不可互换、请求级 CA 覆盖、信任配置池隔离、空/非法 CA 与禁止明文回退。

```text
python tests/test_proxy.py
python scripts/test_wheel.py --proxy
```

六组原生 Actions 分别执行源码和 wheel 套件，保存 `stability-proxy-*.json` 及完整日志。核心额外验证握手取消关闭 socket、CONNECT 响应头上限、认证与预读数据、SOCKS 地址类型和凭据错误脱敏。结果以对应提交的 Actions 及产物为准。

## Node.js

Node 0.7使用相同的`proxy_tunnels`及`https_proxy_tls`能力，无新增ABI函数。`new Session({proxy: url})`设置默认代理；`session.get(url, {proxy: null})`显式直连；省略或undefined继承默认值，空字符串也直连。请求覆盖同样适用于stream、sse和duplex。

源码及内置库安装包各执行14项对应代理用例，保留Python原测试ID；Node用同步/异步生产者和即时/慢速消费者覆盖Python的同步/异步组合。代理靶场由Node测试代码独立实现，安装验收继续清除工具搜索路径并禁用安装脚本。完整范围与类型示例见[node/README.md](node/README.md)及[node/PARITY.md](node/PARITY.md)。

Node的`proxyCaFile`与Python的`proxy_ca_file`对应：Session默认值在构造时读取；请求undefined继承，路径重新读取，null使用系统根。该信任仅用于HTTPS代理，独立于目标`caFile`；HTTP/SOCKS/直连不发送proxy_ca_pem。Node验收同样包含错误代理CA、错误目标CA、错误代理主机名、禁止明文回退、TLS与CONNECT握手超时取消及无效CA不登记请求。
