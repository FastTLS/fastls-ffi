# 浏览器配置候选与 JA3/H2 参数

Python 0.4.0 / Node 0.13.0 提供同一个离线转换器。详细格式及 ABI 契约见 [English contract](FINGERPRINTS.md)。

```python
import json
from fastls import ProfileSpec, Session

with open("base-spec.json", encoding="utf-8") as file:
    base = ProfileSpec(json.load(file))
spec = ProfileSpec.from_fingerprints(base,
    h2="2:0;4:131072;1:4096|100000|3:1:0:201|p,a,m,s")
with Session(profile_spec=spec) as session:
    response = session.get("https://example.com")
```

Node 使用 `ProfileSpec.fromFingerprints(base, { ja3, h2, library })`；Go 使用 `fastls.AdaptProfile(base, fastls.FingerprintParameters{JA3: text, H2: text})`。结果是标准完整 Spec，继续使用已有会话、逐请求、异步和 fetch 的 Spec 参数。保留 selector/Spec 互斥与显式 H3 的能力限制。

JA3 格式为 `771,密码套件,扩展,曲线,点格式`，各有序列表用 `-` 分隔。必须提供完整基础配置；JA3 未编码的签名算法、ALPN、TLS 版本、密钥份额及 HTTP 身份继续使用基础值。仅能选择基础配置已经明确参数的扩展；重复、越界、显式 GREASE、未知扩展和冲突密钥份额均被拒绝。转换固定扩展顺序、取消可选扩展省略；不会凭空补齐未知行为。

H2 格式为 `settings|连接窗口增量|优先级帧|伪头顺序`。例如 `2:0;4:131072|100000|3:1:0:201|p,a,m,s`。设置保持输入顺序；优先级帧字段为 `stream:exclusive:dependency:weight`，多个用逗号分隔，`0` 表示没有。weight 是 **1–256** 的语义值。伪头必须是 `m,a,s,p` 的完整排列。未编码的 HEADERS 优先级保留基础值；不同采集器方言不能默认互认。

结果使用 `custom` 身份变体，并清除继承的实测验证证据。JA3/H2 相同不代表整个浏览器行为相同。每个紧凑参数限制 16 KiB，基础 Spec 限制 256 KiB。转换不创建会话、不执行 DNS 或请求，失败不修改输入或现有配置。旧原生库缺少 `fingerprint_adapter_v1` 能力或符号时返回 `unsupported_capability`。

```sh
python -m fastls profile adapt base-spec.json candidate.json --h2 '2:0;4:131072|100000|0|m,a,s,p' --source 'reviewed local parameters'
```

浏览器更新通过 [imitate 手动工具](https://github.com/FastTLS/imitate/blob/v2/tools/firefox/README.md) 单独执行。当前覆盖 Firefox 普通稳定版、Windows x64、zh-CN、无头冷连接，每次三个场景各五个新进程，共 15 个样本；官方 HTTPS 下载及 SHA512SUMS 校验后，由独立服务端采集并执行在线/离线回放。

工作流只有读取权限，生成来源、语义差异、兼容性限制与 ProfileBundle v1 候选；不会修改活动配置、创建 PR 或自动发布。调用方审核来源与摘要后使用已有 ProfileStore 导入、固定、回滚。导入、会话创建和请求均不隐式联网。已知 H2 初始流编号及流级窗口等差异保留，不能写成完整等价。其它浏览器/平台、ESR、H3 指纹和定时更新继续保留后续。

Go 用户需固定含 `AdaptProfile` 的核心版本；旧稳定版 v0.1.2 没有该接口。Firefox 实测数据也使用固定生成器版本：

```sh
go get github.com/FastTLS/fastls@v0.1.3-0.20261030065220-4c07fe9e4059
go get github.com/FastTLS/imitate@v0.1.2-0.20261030065723-c75c3bf7679f
```

Python/Node 正式包已经包含原生库和固定配置，普通安装无需 Go。
