# Python 0.1.4 / Node 0.10.1 发布说明

发布日期：2026-10-27。Python 0.1.4 已发布至 PyPI，Node 0.10.1 已发布至 npm。Node 主包与五个平台包的 latest 均为 0.10.1，摘要已与认证归档核对；全新 Windows x64 安装复验通过。Python 发布源码为 `676c104`，Node 含文档修订的认证源码为 `19c6c57`，运行时代码一致。

## 用户可见改动

- Python 新增模块级 request/get/post/put/patch/delete/head/options，支持 stream=True、iter_content 和 IDE 关键字提示。HEAD 便捷方法默认不跟随重定向。
- 新增 aiohttp 风格 ClientSession：async with session.get(...)，以及 await response.read/text/json。原 AsyncSession 与高级接口保留。
- Node 新增 fetch / Session.fetch，接受标准 Request、URL、Headers 和正文，返回标准流式 Response；附加 profile、代理、证书和 match 信息。顶层 fetch 不保存 Cookie，Session.fetch 复用会话。补齐全部常用 HTTP 方法。
- Cookie、重定向和默认请求配置由 Python/JavaScript SDK 管理。原生 transport_context_v1 仅持有连接和请求资源，旧 C Cookie 接口保留兼容。新 SDK 需要同版或更新动态库。
- 保留浏览器指纹、连接复用、流式传输、分阶段超时、双工、SSE 和 WebSocket。兼容范围与不支持项见各 SDK README。

## 依赖与验证

Python 新增 publicsuffixlist 公共后缀数据依赖；Node 使用 tough-cookie。类型文件与 py.typed 随 Python 包交付，无需运行时 typing_extensions。原有 209 项共同行为测试全部保留，新增两项所有权/重定向测试，总共 211 项对应场景；另有 Python 便捷/异步入口与 Node fetch 专项。Node 源码及两种安装包各运行 231 项回归。

CI 将源码与安装包放到独立 runner 并行，移除完整套件前的重复快速检查。全部平台、隔离安装、包摘要、发布门禁与持续测试时长不变。

平台延续原要求：Python 3.10+、Node 22+；Windows x64、Linux glibc 2.39+ x64/ARM64、macOS 15+ Intel/Apple Silicon，不支持 Alpine/musl。正式包及跨平台验证结果以同提交成功 CI 的候选清单为准。
