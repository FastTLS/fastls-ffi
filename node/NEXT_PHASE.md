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

核对日期：2026-10-27。源码提交 `676c1040117a1da7ad64dbe9f0e8691a1a0c564c` 的 [main 完整 CI](https://github.com/FastTLS/fastls-ffi/actions/runs/40362480020) 已通过。六包组由 [npm 准备/发布工作流](https://github.com/FastTLS/fastls-ffi/actions/runs/40366698803) 从该运行的认证产物汇总，授权待审列表为空。

**0.10.1 尚未在 npm 发布。** Trusted Publisher 上传在第一个包前返回权限错误；本机恢复上传需要账号持有人完成 npm 浏览器验证。此前验证会话已超时，未发布任何 0.10.1 包。当前可安装版本仍为 `0.10.1-dev.0`，新增 API 不应按旧版本使用。

恢复时使用同一工作流保留的原始六包归档，不重建、不修改 manifest。脚本先检查全部文件和已发布版本，按五个平台包、主包的顺序上传，只跳过内容完全一致的已发布包。账号密码、验证码和 Token 不写入聊天、文档或仓库。

发布完成须核对六个精确版本、归档摘要及 `latest=0.10.1`，并在全新项目从官方 npm 安装，验证 ESM/CJS、类型、自带库、HTTPS、Session Cookie 和 fetch。实际成功后再更新 README、本站和版本记录，不把文档、CI 或 artifact 的存在写成 registry 已发布。

## 后续工作线

发布后的维护可并行处理，互不把历史结果当作新提交验收：

- 发布维护：核对 registry 摘要、包可见性、Trusted Publisher 与下一版本号。稳定版本进入 latest 前另做对应版本验收。
- 反馈速度：先运行相关类型和协议短检查；固定候选仍执行完整矩阵。重复的分支/PR CI 可保留一轮，不能取消唯一的必需验收。逐步测量构建、排队和下载耗时，资源检查继续使用独立 runner。
- 使用反馈：收集五平台安装、系统最低版本和真实调用问题；将明确的缺陷补为对应契约测试，再更新文档。

worker_threads、Electron、Bun、浏览器、Linux musl、额外架构、自动重连、WebSocket 压缩及 H2/H3 CONNECT 均未承诺实现。需要扩展时先确定需求及原生契约，不把它们写成当前功能。

## 工作区与证据约束

保留其它工作区的本地修改，使用独立 worktree、临时目录和固定依赖副本。不能覆盖共享 go.work、dist 或测试服务。源码或打包输入改变后，以新提交重新验收；不缓存“测试通过”。

资源阈值沿用 PARITY.md。短反馈不替代完整功能和资源检查；重复、缺失、失败、取消、意外跳过或摘要不一致，均不能签发发布包组。最终发布步骤及其产物应可由同提交 CI 和实际 registry 数据核对。
