# Node SDK 维护与源码开发

普通用户请先读 [使用指南](README.md)。以下是构建、候选包和历史验收材料，不代表当前默认安装所需步骤。

从源码开发时，在仓库根目录构建动态库和测试服务，再安装 Node 开发依赖：

```sh
python scripts/build.py
go build -o build/ffi-fixture ./cmd/ffi-fixture
npm ci --prefix node
npm run build --prefix node
```

Windows 测试服务文件名使用 `build/ffi-fixture.exe`，原生构建需要可用的 C 编译器，可通过 `--cc` 指定。`scripts/build.py --local` 会生成忽略跟踪的 `go.work`，使用同级源码仓库；也可用 `FASTLS_LOCAL_FASTLS`、`FASTLS_LOCAL_FTRANSPORT`、`FASTLS_LOCAL_IMITATE` 指向固定副本。当前脚本没有 `--workspace` 参数。正式发布消费同提交 CI 的固定依赖构建，不能用任意本地构建替代。

源码默认加载仓库 `dist/`；普通 `npm pack ./node` 生成需要外部动态库的 thin 包。thin 包可通过 `FASTLS_LIBRARY` 或 `new Session({ library: '/absolute/path/to/library' })` 指定库。GitHub 内置库候选和 npm 拆分包的区别见下文。


## 候选包与原生库加载

构建本平台Go动态库后，运行 `npm run pack:native`，产物位于上级 `dist/`。
例如Windows x64：`fastls-fastls-0.10.1-dev.0-win32-x64.tgz`；Linux包名另带`-glibc`。
在目标项目执行 `npm install <候选包绝对路径> --ignore-scripts`，随后即可 `new Session()`，无需Go、Python、编译器或FASTLS_LIBRARY。
安装时保留npm可选依赖，Koffi使用对应平台的预编译组件。`npm run test:package`在空工具搜索路径、清除FASTLS_LIBRARY且关闭安装脚本的临时项目中重复完整231项测试。靶场是仓库中已构建的独立测试服务，SDK和动态库均从安装目录加载。

当前候选覆盖Windows x64、Linux glibc x64/arm64、macOS x64/arm64，Node22+。每个tarball只含一个系统/架构，不能将同名版本的这些变体当成可直接发布到npm的通用包。npm的os/cpu/libc字段限制安装目标；首次使用还检查平台、ABI声明、库SHA256及Linux的glibc版本。Linux最低glibc从ELF符号版本提取，macOS最低版本从Mach-O提取，具体要求及系统动态依赖见包内`native/manifest.json`，不将CI宿主机上的通过扩大为所有旧系统均已测试。musl不在当前范围。

加载顺序：显式 `SessionOptions.library` → `FASTLS_LIBRARY` → 包内 `native/manifest.json` 对应的库 → 已安装主包的本平台可选依赖；源码目录没有内置 manifest 时使用仓库 `dist/`。已安装的 thin 包没有仓库回退。可选原生包必须与主包同版本；缺包、错误平台、glibc 不兼容或摘要不一致均报错。显式路径覆盖仍检查 ABI 与必需能力。SHA-256 只验证内容完整性，不能替代来源签名。

清单还保存Go构建来源摘要、模块版本/校验和、FastTLS仓库提交和收集到的许可证文本。`native/THIRD_PARTY_NOTICES.txt`仅是可用材料清单。依赖许可证按实际构建输入收集；imitate已补齐Apache-2.0许可证，npm发布要求所有平台的licenseReviewRequired均为空。GitHub内置库候选保持private且没有安装/构建脚本；npm主包与平台包的发布流程见NPM_RELEASE.md。

npm 拆分包通过 `npm run test:package` 后再执行 `npm run test:npm` 生成，主包与本机原生包位于 `dist/npm/`。五个同名同版本的 GitHub 内置库候选不能作为五个 npm 版本发布。首次 registry 发布必须使用同提交 main CI 汇总得到的六包组，详见 NPM_RELEASE.md。


## 验证

所有命令从仓库根目录执行，先完成动态库和测试服务构建。

```sh
npm run test:quick --prefix node
npm test --prefix node
npm run test:package --prefix node
npm run test:npm --prefix node
python node/scripts/test-npm-release.py
```

`test:quick` 提供类型、映射、原生绑定、WebSocket 和 Cookie 兼容的短反馈。`npm test`、`test:package`、`test:npm` 分别运行源码、内置库候选、npm 拆分包的完整 231 项套件：十六组 Python 的 211 项行为映射，加 2 项绑定和 18 项 Node 专有测试。类型、Cookie 真实库兼容、安装元数据和发布边界检查另计；当前发布边界测试为 9 项。

两类安装测试都在独立消费项目运行，关闭安装脚本、清除外部库环境变量并移除编译工具搜索路径。拆分包测试由临时 registry 提供尚未发布的本项目平台包，npm 自行按平台筛选；其它依赖仍使用正常 registry。

主 CI 覆盖五个平台的 Node 24，以及 Linux x64 的 Node 22 组合，复用固定的原生构建输入。源码证据位于 `build/node-*.json`；内置候选和摘要在 `dist/`；拆分包、`npm-package-info.json` 和独立安装证据在 `dist/npm/`。具体映射、资源阈值及长测命令见 PARITY.md。功能检查和资源长测分开执行，不把清单存在或历史绿色结果当作新提交已通过。


## 发布与授权

维护者在 main 的同提交完整 CI 成功后，运行 `npm-publish.yml` 准备或发布 npm 六包组；首次发布、账号权限、2FA、Trusted Publisher 和重试步骤见 NPM_RELEASE.md。

`node-release.yml` 单独签发 GitHub 内置库候选和 thin 包，生成 SHA256SUMS、Sigstore 来源证明及清单，不会发布 npm。既有 `node-v0.10.1-dev.0` GitHub 预发布使用历史包名 `@fasttls/fastls`；本次 npm 包名为 `@fastls/fastls`，两套归档不能互换。后续 GitHub 签发使用新版本号，不能覆盖既有 tag 的来源记录。

每次构建收集原生依赖许可证，npm 发布要求五个平台的 `licenseReviewRequired` 均为空。`imitate` 已固定到补齐 Apache-2.0 的提交；是否满足要求以本次候选清单为准。发布状态以 npm registry 和已完成的工作流为准。
