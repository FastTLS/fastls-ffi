# npm 分发与发布

主包是 `@fastls/fastls`，原生库通过同版本的可选依赖分发：

- `@fastls/fastls-win32-x64`
- `@fastls/fastls-linux-x64-glibc`
- `@fastls/fastls-linux-arm64-glibc`
- `@fastls/fastls-darwin-x64`
- `@fastls/fastls-darwin-arm64`

安装主包时保留 optional dependencies。安装没有 Fastls 编译或下载脚本；npm 按 os/cpu/libc 选择匹配平台的包。Linux 只支持 glibc，实际最低版本由原生 manifest 测量并在加载时检查。版本、平台和 SHA-256 均在加载前验证。

`FASTLS_LIBRARY` 或 Session 的 `library` 参数继续优先；源码开发使用仓库 dist，GitHub 内置库候选仍能独立安装。普通 `npm pack ./node` 是需要外部库的 thin 包，不能替代 npm 发布包组。

## 本地验收

先构建原生库和测试 fixture，再执行：

```sh
npm ci --prefix node
npm test --prefix node
npm run test:package --prefix node
npm run test:npm --prefix node
```

最后一步读取已通过完整安装测试的候选，不重建原生库；生成主包与本机平台包到 `dist/npm/`。临时 registry 只提供未发布的 @fastls 平台元数据，实际通过 npm 解析、过滤并安装可选依赖，其他依赖从正常 npm registry 获取。安装使用空编译工具路径并关闭生命周期脚本，随后运行 CJS/ESM、类型、Cookie 兼容和完整 225 项回归，检查版本错配及缺少可选依赖。拆分包的证据独立保存在 `dist/npm/`。

主分支 CI 对五个平台及额外 Node 22 组合运行这些步骤。发布使用五个 Node 24 候选。非本机平台只有对应 CI 验收通过后才能计为已验证。

## 依赖授权

`native/manifest.json` 的 `release.licenseReviewRequired` 保存缺少明确许可证的模块。构建和测试会保留原始材料；正式 npm 发布要求所有候选该列表为空。当前 imitate 的 NOTICE 不构成许可证授予，需要维护者确认授权、在依赖仓库补齐相应材料并重新构建验收。不能通过删字段或手工修改归档解除此项。

## 准备发布包组

将改动合并至 main 并等待完整 CI 成功。在 GitHub Actions 运行 `Prepare or publish npm packages`，填写该提交成功的主 CI run ID，首次保持 `publish=false`。

工作流要求自身与 CI 属于 main 的同一提交，从候选收集五个原生包和一个主包，不重新编译。它验证包摘要、完整回归证据、包名/版本/平台及原生内容，生成 `npm-release` artifact 和 `npm-release-manifest.json`。

预发布版本使用 next 标签，稳定版本使用 latest。版本发布后不能替换；发布脚本会先检查全部归档与 registry，遇到同版本不同内容立即停止。网络中断后的重试可以跳过已发布且 SHA-512 完全一致的包，依旧保证原生包先于主包。

## 账号操作与首次发布

维护者完成 npm 邮箱验证、2FA，确认对 `@fastls` 下六个包均有发布权限。先在本机登录并检查用户名，无需将密码、验证码或 Token 发到聊天中：

```sh
npm login --registry=https://registry.npmjs.org/ --auth-type=web
npm whoami --registry=https://registry.npmjs.org/
```

新包如果尚无 Trusted Publisher 设置入口，可在授权材料和完整 CI 均通过后，下载并解压上述六包 artifact，在本机执行以下首次发布操作（把路径换成包含 npm-release-manifest.json 的目录）：

```sh
npm run publish:npm --prefix node -- /absolute/path/to/npm-release
```

发布脚本会保留终端交互以完成 npm 验证。不要发布空占位包，也不要从源码目录直接发布 thin 包来替代此包组。首次本机发布不声称具有 GitHub OIDC provenance。

## 后续 GitHub Trusted Publisher 配置

在 npm 每个包的 Settings → Trusted Publisher 中选择 GitHub Actions，六个包均填写：

- Organization or user: `FastTLS`
- Repository: `fastls-ffi`
- Workflow filename: `npm-publish.yml`
- Environment name: `npm`
- Allowed actions: 启用直接 `npm publish`

文件名仅填写 `npm-publish.yml`。工作流使用 GitHub 托管 runner，授予 `id-token: write`，Node 24 的 npm CLI 必须至少 11.5.1；不需要配置 NPM_TOKEN。配置完成后，对同提交成功 CI 运行该工作流，设置 `publish=true`。它先检查依赖授权，再按顺序发布原生包和主包，并生成 npm provenance。

发布后在全新项目执行 `npm install @fastls/fastls@next`，验证加载与实际请求；正式版本去掉 `@next`。GitHub 包签发与 npm registry provenance 是独立结果。

官方参考：[npm 登录](https://docs.npmjs.com/cli/v11/commands/npm-login/)、[公开 scoped 包](https://docs.npmjs.com/creating-and-publishing-scoped-public-packages/)、[Trusted Publisher](https://docs.npmjs.com/trusted-publishers/)。
