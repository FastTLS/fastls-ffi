# PyPI 发布：fastls

Python 新发行名为 `fastls`，导入名仍为 `fastls`；GitHub 仓库和 C ABI 不改名。历史 v0.1.2 使用 `fastls-ffi`，已有附件和版本标签不重写。

## Trusted Publisher

维护者在 PyPI 的 GitHub Publisher 表单填写：

- Project Name：`fastls`
- Owner：`FastTLS`
- Repository：`fastls-ffi`
- Workflow：`ci.yml`
- Environment：`pypi`

对应仓库文件为 `.github/workflows/ci.yml`，上传 job 声明 `environment: pypi` 和 `id-token: write`。GitHub 仓库可在 Settings → Environments 配置 `pypi` 的保护规则。无需提供密码或长期 API Token。

添加 Pending Publisher 本身不上传文件、不创建项目，也不保留包名。首次成功上传后才会创建项目；包名是否可用仍以 PyPI 服务端为准。[官方说明](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)

## 构建和上传条件

1. 所有 push/PR 运行正常原生矩阵。发行元数据、安装升级卸载测试和 fixture 均使用新包名。
2. Linux 使用固定 `auditwheel==6.8.2`、`patchelf==0.19.1.0`，审计并生成 `manylinux_2_39_x86_64`/`manylinux_2_39_aarch64`，之后才绑定 CI 输入并执行消费者测试。若修复改变原生库内容，拒绝沿用旧构建摘要。
3. 完整门禁 `required` 通过后，`pypi-candidate` 选择 Go 1.26.10 的五个正式平台组合，拒绝缺失、修改、混合源码或错误包名/版本/平台的候选；运行 `twine check --strict`，保存 `pypi-validated` artifact。
4. 普通分支/PR 只生成带 `.devN` 的已验证候选，`publish-pypi` 跳过。标签由 `workflow_dispatch` 手动运行也不会触发该上传 job。
5. 只有 `push` 的 `v*` 标签，在候选验收和 GitHub Release 都成功后，才能使用 `pypi` Environment、官方 PyPA action 和 OIDC 上传同一组五个 wheel 与一个 SDK-only sdist，并生成 PyPI 发布证明。

使用的 [PyPI 官方 Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/) 不需要在仓库保存 Token。工作流里出现 `id-token: write` 本身不代表已获得发布权限，PyPI 仍会校验你配置的仓库、工作流和 Environment。

## 首次发布与失败处理

先把改动合入主线，并等待该候选的完整 Actions 通过，再选择一个新的正式版本标签。不要重写 v0.1.2。实际推送正式标签会同时触发 GitHub 与 PyPI 发布，必须由维护者决定版本和发布时间。

若上传拒绝授权，核对 Publisher 的四个 GitHub 字段和 PyPI 项目名；若只有部分文件上传成功，先核对服务端已接受文件的摘要，再制定恢复方案，不移动标签或重新构建同名版本来覆盖。默认没有 `skip-existing`，避免隐藏不一致。

若上传工具在连接 PyPI 前失败，可以从 main 手动运行 `ci.yml`，填写原正式标签 CI 的 `pypi_run_id` 和 `pypi_version`（如 `0.1.3`）。该模式只执行 `Resume certified PyPI upload`：验证原运行是同仓库的已完成标签 push、标签提交未移动、原生/包集合/GitHub 发布门禁均已通过，再下载该运行的 `pypi-validated`，逐文件与正式 GitHub Release 的 SHA256SUMS、包元数据和原生来源摘要比较。不会重新编译、创建 GitHub Release 或跳过已有 PyPI 文件。普通完整 CI 不受该模式影响。

发布 Action 固定到 PyPA v1.14.3 的提交 `7245906fb61fa33a13554f9d5fb856fe61758086`；更新时还须确认对应 GHCR 容器镜像存在，不能只确认 Git tag 存在。

每次上传完成后，还需要在支持的系统安装 `python -m pip install --only-binary=:all: fastls==实际版本`，创建 Session、列配置并运行真实请求，以索引返回的包验证分发链路。

源码包仅含 SDK，不编译或下载原生库。为避免不受支持的平台静默装入 SDK-only 包，普通用户推荐 `--only-binary=:all:`。安装及从旧包迁移见 [INSTALL.md](INSTALL.md)。

## v0.1.3 发布记录

2026-10-26，[fastls 0.1.3](https://pypi.org/project/fastls/0.1.3/) 首次发布成功，包含五个平台 wheel 和一个 SDK-only sdist。[原正式 CI](https://github.com/FastTLS/fastls-ffi/actions/runs/40309964336) 完成运行验收与 GitHub Release，但旧版上传 Action 的容器镜像不存在，上传前失败。更新上传工具后，[恢复上传](https://github.com/FastTLS/fastls-ffi/actions/runs/40314270279) 使用同一批通过认证的文件成功发布。六个文件均已与正式 GitHub Release 的 SHA256SUMS 比较通过；原标签和运行时源码提交 `f87f44148b79c7f60d92d0d3bf98ab7a29800f90` 保持不变。

原 CI 的 macOS ARM64 Node 大响应测试曾超时；同源码和同产物完整重跑该任务后，源码与安装包各 225 项通过，未重现超时。该次重跑没有放宽超时、跳过测试或修改运行时代码；原始失败记录保留在正式 CI 中。

发布后在全新 Windows x64 虚拟环境中从官方 PyPI 安装 `fastls==0.1.3`，确认下载来源为 files.pythonhosted.org，包摘要同时匹配 PyPI 与 GitHub，原生库位于虚拟环境且 ABI 为 1。使用 `chrome/156/windows` 通过独立本地 CA 靶场验证 HTTPS HTTP/1.1、HTTP/2、64 MiB 流式下载及正文摘要、WebSocket 回显和 asyncio 请求。该安装复验仅在 Windows 本机运行；其它平台的认证来自原正式 CI。
