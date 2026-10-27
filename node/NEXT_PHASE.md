# Node 当前状态与下一阶段

本文件记录当前交付状态及剩余步骤。用户接口见 README.md，测试清单见 PARITY.md，发布操作见 NPM_RELEASE.md；不再以旧阶段的计划描述已经实现的功能。

## 已完成的实现

- HTTP/1.1、HTTP/2、流式上传下载、HTTP/2 双工、SSE、四种代理、Cookie、请求参数和 multipart。
- WebSocket 消息、关闭、超时、代理、通知与共享 Cookie；Cookie、默认参数和重定向由 JavaScript SDK 管理，动态库仅持有传输资源。
- 新增标准 fetch / Session.fetch、Request/Response/Web Stream 适配和全部常用 HTTP 方法，保留原高级接口。
- 请求 trailers、最终 URL、实际协议和响应 EOF trailers 刷新，与 main 的当前 Python 契约对应。
- 十六组 Python 套件的 211 个对应场景，加 2 项绑定和 18 项 Node 专有检查，共 231 项。源码、内置库候选和 npm 拆分包均运行同一完整套件。
- 固定原生构建输入，功能检查与各资源长测独立调度；五个平台的 Node 24 和 Linux x64 的 Node 22 组合并行验收。
- npm 主包 `@fastls/fastls` 与五个同版本原生包，通过可选依赖自动选择平台；版本、平台、摘要与 glibc 门禁在加载前验证。
- `imitate` 经维护者确认采用 Apache-2.0，依赖提交固定为 `780f88aaef8474f09035855f906ecc90dd0f4102`，实际许可证随原生包收集。
- `npm-publish.yml` 从同提交 main 成功 CI 收集六包组，支持仅准备、顺序发布和内容一致的中断恢复。

## 0.10.1 验证与实际发布状态

核对日期：2026-10-27。源码提交 `19c6c57bee4f5663757eda241081d469f62af456` 的 [main 完整 CI](https://github.com/FastTLS/fastls-ffi/actions/runs/40373864723) 已通过。六包组由 [npm 准备/发布工作流](https://github.com/FastTLS/fastls-ffi/actions/runs/40376125121) 从该运行的认证产物汇总，授权待审列表为空。

**0.10.1 已正式发布至 npm。** 主包与五个平台包的精确版本均为 0.10.1，latest 指向稳定版；registry 的 SHA-512 与认证六包组完全一致。历史开发版 0.10.1-dev.0 保留用于追溯。

首次 Trusted Publisher 上传曾返回权限错误；最终由维护者完成本机 npm 身份验证，上传同提交认证的原始归档。未重新打包、替换已发布版本或改变验证阈值；本次本机上传不声称具有 npm GitHub OIDC provenance。来源以成功 CI、候选清单及 registry 摘要共同核对。

Windows x64 全新项目从官方 npm 安装后，已验证默认原生包、库摘要、标准 Response 的 HTTPS/2 请求、Session.fetch Cookie 与 ESM/CJS 入口。其它平台的执行认证来自完整 CI，不把本机结果扩展为所有系统版本均已实测。

后续维护应保留相同的发布门禁、五个平台包先于主包的顺序和内容一致的中断恢复。账号密码、验证码和 Token 不写入聊天、文档或仓库。修改源码或打包输入必须重新认证，不能替换既有版本文件。

## 后续工作线

发布后的维护可并行处理，互不把历史结果当作新提交验收：

- 发布维护：核对 registry 摘要、包可见性、Trusted Publisher 与下一版本号。稳定版本进入 latest 前另做对应版本验收。
- 反馈速度：先运行相关类型和协议短检查；固定候选仍执行完整矩阵。重复的分支/PR CI 可保留一轮，不能取消唯一的必需验收。逐步测量构建、排队和下载耗时，资源检查继续使用独立 runner。
- 使用反馈：收集五平台安装、系统最低版本和真实调用问题；将明确的缺陷补为对应契约测试，再更新文档。

worker_threads、Electron、Bun、浏览器、Linux musl、额外架构、自动重连、WebSocket 压缩及 H2/H3 CONNECT 均未承诺实现。需要扩展时先确定需求及原生契约，不把它们写成当前功能。

## 工作区与证据约束

保留其它工作区的本地修改，使用独立 worktree、临时目录和固定依赖副本。不能覆盖共享 go.work、dist 或测试服务。源码或打包输入改变后，以新提交重新验收；不缓存“测试通过”。

资源阈值沿用 PARITY.md。短反馈不替代完整功能和资源检查；重复、缺失、失败、取消、意外跳过或摘要不一致，均不能签发发布包组。最终发布步骤及其产物应可由同提交 CI 和实际 registry 数据核对。
