# npm 分发与发布

所有命令从仓库根目录执行。当前开发包版本为 `0.10.1-dev.0`，使用 `next` 渠道；版本或发布状态以 `node/package.json`、npm registry 和成功工作流为准。用户 API 见 [README.md](README.md)，测试范围见 [PARITY.md](PARITY.md)。

主包是 `@fastls/fastls`，原生库通过同版本的可选依赖分发：

- `@fastls/fastls-win32-x64`
- `@fastls/fastls-linux-x64-glibc`
- `@fastls/fastls-linux-arm64-glibc`
- `@fastls/fastls-darwin-x64`
- `@fastls/fastls-darwin-arm64`

安装主包时保留 optional dependencies。安装没有 Fastls 编译或下载脚本；npm 按 os/cpu/libc 选择匹配平台的包。Linux 只支持 glibc，实际最低版本由原生 manifest 测量并在加载时检查。版本、平台和 SHA-256 均在加载前验证。

`FASTLS_LIBRARY` 或 Session 的 `library` 参数继续优先；源码开发使用仓库 dist，GitHub 内置库候选仍能独立安装。普通 `npm pack ./node` 是需要外部库的 thin 包，不能替代 npm 发布包组。

## 本地验收

先按 README 构建原生库和 `build/ffi-fixture[.exe]`，再执行：

```sh
npm ci --prefix node
npm test --prefix node
npm run test:package --prefix node
npm run test:npm --prefix node
python node/scripts/test-npm-release.py
```

最后一步读取已通过完整安装测试的候选，不重建原生库；生成主包与本机平台包到 `dist/npm/`。临时 registry 只提供未发布的 @fastls 平台元数据，实际通过 npm 解析、过滤并安装可选依赖，其他依赖从正常 npm registry 获取。安装使用空编译工具路径并关闭生命周期脚本，随后运行 CJS/ESM、类型、Cookie 兼容和完整 225 项回归，检查版本错配及缺少可选依赖。拆分包的证据独立保存在 `dist/npm/`。

发布边界的 9 项检查验证成功汇总、授权待审拒绝、缺少平台、错误提交、失败 CI、损坏归档、失败行为证据和元数据错配。主分支 CI 对五个平台及额外 Node 22 组合运行这些步骤。发布使用五个 Node 24 候选。非本机平台只有对应 CI 验收通过后才能计为已验证。

## 依赖授权

`native/manifest.json` 的 `release.licenseReviewRequired` 保存缺少明确许可证的模块。构建和测试会保留原始材料；正式 npm 发布要求所有候选该列表为空。imitate 已由维护者确认采用 Apache-2.0，源仓库提交 780f88aaef8474f09035855f906ecc90dd0f4102 补齐 LICENSE 并保留来源 NOTICE；ci/dependencies.json 固定到该提交。后续仍以每次构建实际收集到的材料为准，不能通过删字段或手工修改归档解除待审项。

## 准备发布包组

将改动合并至 main 并等待完整 CI 成功。在 GitHub Actions 运行 `Prepare or publish npm packages`，填写该提交成功的主 CI run ID，首次保持 `publish=false`。

`run_id` 必须是 `.github/workflows/ci.yml` 的 main push 运行，状态为 completed/success，且 head SHA 等于当前工作流的源码 SHA；不能使用 PR 运行或其它提交的历史绿色结果。工作流要求自身与 CI 属于 main 的同一提交，从候选收集五个原生包和一个主包，不重新编译。它验证包摘要、完整回归证据、包名/版本/平台及原生内容，生成 `npm-release` artifact 和 `npm-release-manifest.json`。

普通 `npm pack ./node` 产出的 thin 包，以及 `node-release.yml` 的 GitHub 内置候选，都不是此六包组。既有 GitHub `node-v0.10.1-dev.0` 使用历史包名 `@fasttls/fastls`；本次 registry 包使用 `@fastls/fastls`，不要混用。

预发布版本使用 next 标签，稳定版本使用 latest。版本发布后不能替换；发布脚本会先检查全部归档与 registry，遇到同版本不同内容立即停止。网络中断后的重试可以跳过已发布且 SHA-512 完全一致的包，依旧保证原生包先于主包。

## 账号操作与首次发布

维护者完成 npm 邮箱验证、2FA，确认对 `@fastls` 下六个包均有发布权限。用户名为 `fastls` 的个人账号可以发布自己的 `@fastls/*` 包，无需额外创建同名组织；GitHub 组织权限不自动授予 npm 权限。先在本机登录并检查用户名，无需将密码、验证码或 Token 发到聊天中：

```sh
npm login --registry=https://registry.npmjs.org/ --auth-type=web
npm whoami --registry=https://registry.npmjs.org/
```

新包如果尚无 Trusted Publisher 设置入口，可在授权材料和完整 CI 均通过后，下载并解压上述六包 artifact，在本机执行以下首次发布操作（把路径换成包含 npm-release-manifest.json 的目录）：

```sh
npm run publish:npm --prefix node -- /absolute/path/to/npm-release
```

Windows PowerShell 的路径示例：

```powershell
npm run publish:npm --prefix node -- "D:/release/npm-release"
```

首次执行前检查 manifest 的 revision、version、六个 publish_order 项、授权待审列表和各文件摘要。正式发布只使用上述工作流保留的原始归档，不重新打包或编辑 manifest。发布脚本会保留终端交互以完成 npm 验证。不要发布空占位包，也不要从源码目录直接发布 thin 包来替代此包组。首次本机发布不声称具有 GitHub OIDC provenance。

## 后续 GitHub Trusted Publisher 配置

在 npm 每个包的 Settings → Trusted Publisher 中选择 GitHub Actions，六个包均填写：

- Organization or user: `FastTLS`
- Repository: `fastls-ffi`
- Workflow filename: `npm-publish.yml`
- Environment name: `npm`
- Allowed actions: 启用直接 `npm publish`

文件名仅填写 `npm-publish.yml`。工作流使用 GitHub 托管 runner，授予 `id-token: write`，Node 24 的 npm CLI 必须至少 11.5.1；不需要配置 NPM_TOKEN。配置完成后，对同提交成功 CI 运行该工作流，设置 `publish=true`。它先检查依赖授权，再按顺序发布原生包和主包，并生成 npm provenance。

## 发布后验收与恢复

查询六个包的精确版本、`dist-tags.next` 和 `dist.integrity`，核对本次归档。使用全新消费项目安装公开版本，保留可选依赖：

```sh
npm install @fastls/fastls@next --include=optional --ignore-scripts
node --input-type=module -e "import {Session} from '@fastls/fastls'; const s=new Session(); console.log(s.listProfiles().length); await s.closeAsync();"
```

再验证 CJS、TypeScript 类型和一次本地测试服务的真实请求，确认入口与原生库均来自消费项目的 node_modules；不设置 FASTLS_LIBRARY，也不回退到源码。跨平台的安装结果按实际执行的平台分别记录。正式稳定版本使用 latest，安装时去掉 @next。

如果部分包已发布，保留原始六包组并重新执行同一发布命令。脚本仅跳过 SHA-512 完全一致的已发布版本；出现同版本不同内容必须停止，确认来源并选择新版本，不能覆盖已有版本。新的源码或重新构建产物不可冒充原归档恢复上传。

GitHub 候选签发与 npm registry provenance 是独立结果。初次本机上传不声明 GitHub OIDC provenance；后续通过已配置的 Trusted Publisher 工作流发布时由 npm 生成来源证明。

官方参考：[npm 登录](https://docs.npmjs.com/cli/v11/commands/npm-login/)、[公开 scoped 包](https://docs.npmjs.com/creating-and-publishing-scoped-public-packages/)、[Trusted Publisher](https://docs.npmjs.com/trusted-publishers/)。
