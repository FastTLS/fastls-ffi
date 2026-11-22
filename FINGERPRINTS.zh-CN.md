# 浏览器配置候选与 JA3/H2 参数

## H2 流级配置

Python 0.5.0 / Node 0.14.0 支持完整 Spec 内的 `http2.first_stream_id`、`http2.stream_window_increment`。省略或零值保持流 1、不发送初始流更新。非零流编号须为不超过 2147483647 的奇数；每个物理连接重新起号，后续请求递增 2，不发送占位请求。窗口增量在每个请求初始 HEADERS（含全部 CONTINUATION）之后发送一次，Trailer 不重复触发；增量与 SETTINGS_INITIAL_WINDOW_SIZE 之和不得超过 31 位上限。增加额度也增加单流潜在缓冲，应结合并发预算配置。

任一字段非零须把 `http2.stream.v1` 加入 `capabilities.required_features`，保留其它必要能力。SDK 在创建 owner/转换前检查原生 `http2_stream_v1`；旧解析器和后端明确拒绝不支持的配置。Spec、Bundle 和 C ABI 版本仍为 1。JA3/H2 紧凑参数没有编码这两项，转换保留 base 值；Bundle 摘要、pin/回滚、同步/异步及 fetch 继续共用契约。

候选须显式导入与固定，不静默替换内置 selector。Firefox Windows 冷请求还需原始 TLS、头序和 SETTINGS；两个流级值不足以重现浏览器。回放独立检查首流编号，不依赖 HEADERS 是否携带优先级，并比较原始初始帧；匹配不代表整个浏览器语义等价。示例与协议来源见 [English contract](FINGERPRINTS.md#optional-h2-stream-configuration)。

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

## 每日维护与已发布数据（2026-11-22）

`imitate v0.1.3` 包含 Chrome **158.0.8127.37**、Edge **158.0.4370.33** 和 Firefox **160.0.1** 的 Windows 配置，数据修订为 `browser-8ee4c22c6b779cbb`。使用规范 ID `chrome/158/windows`、`edge/158/windows`、`firefox/160/windows`；既有默认 selector 保持兼容，不保证默认别名选中这批实测版本。三个服务也支持显式的 `chrome158`、`edge158`、`firefox160` 名称。

[每日维护工作流](https://github.com/FastTLS/imitate/blob/v2/.github/workflows/browser-maintenance.yml)于北京时间 **10:17**（02:17 UTC）检查官方稳定版。每产品采集三个 TLS/H1/H2 场景、各五个独立冷进程；验证原始观察、完整 Spec 和六项在线/离线回放后，独立采用任务正常快进提交至默认分支。采集任务只读；采用任务具有受限的内容写权限。失败保留证据，不移动标签、不强制覆盖分支；可从已完成的可信运行恢复采用。版本与输入未变时复用已验证证据，仍导出快照。

验证范围仅为 Windows x64 冷导航：Chrome 为 en-US 有界面最小化；Edge 为 en-US 窗口移至屏幕外并使用软件图形模式；Firefox 为 zh-CN 无头。Chromium 扩展成员和参数的比较不证明随机顺序分布等价；恢复连接、ECH、其它系统、H3 和整个浏览器行为未获认证，ML-DSA 仍仅为声明能力。浏览器签名、完整版本及适用的官方摘要均在采集阶段核验；Chrome 本地 SHA256 不冒充厂商摘要。

**源库采用与用户升级是两步。** 每日任务不会自动发布新的 Go 模块、Python／npm 包或服务二进制，也不会在导入、创建会话或请求时下载更新。Python **0.7.3**、Node **0.16.3**、Fetch **v0.3.2**、RPC **v0.3.3**、MITM **v0.1.4** 和 fcurl **v0.2.3** 固定本批模块；旧安装须升级或使用已有 `ProfileStore` 显式导入、核对摘要、固定及回滚 Bundle。原始采集报告和离线快照见 [Actions 记录](https://github.com/FastTLS/imitate/actions/workflows/browser-maintenance.yml)；固定模块来源见 [v0.1.3](https://github.com/FastTLS/imitate/releases/tag/v0.1.3)。Bundle 对应固定版本，不代表未来数据自动更新。

```sh
go get github.com/FastTLS/fastls@v0.2.1
go get github.com/FastTLS/imitate@v0.1.3
go run github.com/FastTLS/imitate/cmd/imitate-export@v0.1.3 -output fingerprints.json
```

Go 导出的完整目录由 Go 的离线 Provider 加载；Python／Node 的 ProfileStore 使用 Actions 单产品候选中的 ProfileBundle v1，不直接导入完整目录快照。

Python／Node 的正式包自带原生库和固定数据，普通安装无需 Go。手动采集入口仍保留：[Firefox](https://github.com/FastTLS/imitate/blob/v2/tools/firefox/README.md)、[Chrome／Edge](https://github.com/FastTLS/imitate/blob/v2/tools/chromium/README.md)、[维护及恢复说明](https://github.com/FastTLS/imitate/blob/v2/tools/maintenance/README.md)。
