# 显式 H3 与可信地址策略

Python 0.6.0 / Node 0.15.0 增加原生 `http3_address_policy_v1` 组合能力。分别支持 H3 和地址策略，不代表支持组合。SDK 在创建 owner 或为已有策略 Session 逐请求选择 H3 前检查组合能力；旧库明确返回 `unsupported_capability`，不分配请求或静默忽略策略。C ABI、transport JSON、Spec 与 Bundle 仍为 v1。

```python
from fastls import Session

policy = {"allow_schemes": ["https"], "allow_hosts": ["api.example.com"],
          "allow_ports": [443], "deny_cidrs": ["127.0.0.0/8", "::1/128"]}
with Session(http_version="h3", address_policy=policy) as session:
    response = session.get("https://api.example.com/")
    response.raise_for_status()
```

```js
import { Session } from '@fastls/fastls';
const session = new Session({ httpVersion: 'h3', addressPolicy: {
  allow_schemes: ['https'], allow_hosts: ['api.example.com'], allow_ports: [443],
  deny_cidrs: ['127.0.0.0/8', '::1/128'],
} });
try { (await session.get('https://api.example.com/')).raiseForStatus(); }
finally { await session.closeAsync(); }
```

示例须替换为获准且实际支持 H3 的服务。空允许列表不限制该维度；示例拒绝列表不是完整 SSRF 防护。策略只由可信代码在 Session 创建时配置，复制后不可变，不能从普通请求 JSON 注入或放宽。Python 异步 Session、Node 独立 fetch 共用此契约。策略 Session 可逐请求选择 H3；强制 H3 的 Session 不能逐请求降级为 auto。

每次新建 QUIC 连接重新解析并检查全部 DNS 回答，任一地址禁止则在创建 socket 前拒绝；实际连接只接收该快照中的数字 IP/端口，不再次解析域名。解析和全部连接尝试共享最多 30 秒预算，调用方更短的请求/阶段时限优先。URL、HTTP authority、SNI 和证书名称保持原域名，SDK 默认校验证书。

connected UDP socket 在内核固定远端，PacketConn 同时拒绝其它写入目标（`peer_change_unsupported`）。策略路径不暴露原始/OOB/批量写入接口，不启用 ECN/GSO、连接迁移、服务端首选地址切换或 0-RTT。已批准的物理连接可复用，DNS 改变不会追溯撤销它；新连接必须验证新快照。请求、重定向及缓存分派前仍检查逻辑目标。握手失败、取消、连接结束和 Session 关闭释放所拥有的 socket，空闲关闭继续保护活动正文。

策略拒绝使用 `FastlsError.code == "address_denied"`；缺能力为 `unsupported_capability`，非法配置和请求策略注入明确拒绝。代理、H1/H2 Spec、q 指纹选择器、请求 Trailer、WebSocket、H2 双工仍不与此路径组合。没有 TCP 回退、自动 Alt-Svc、浏览器 H3 指纹、新增平台或服务协议扩展；无策略 H3 保留原路径。

更多见 [Go 地址策略](https://github.com/FastTLS/fastls/blob/main/ADDRESS_POLICY.md) 与 [English contract](H3_POLICY.md)。是否正式发布及平台验收范围以对应发行物与来源清单为准。
