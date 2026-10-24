# GitHub Actions 持续测试

`.github/workflows/ci.yml` 在每次push、PR更新以及手动workflow_dispatch时执行。只读仓库权限，不发布模块或Release。
使用各系统原生runner构建并运行，而不是把交叉编译成功当成运行测试成功。

## 测试矩阵

- Windows x64：windows-2022，Go1.26.10，Python3.11。
- Linux x64：ubuntu-24.04，Go1.26.10，Python3.11，附加桥接与核心race检查。
- Linux ARM64：ubuntu-24.04-arm，Go1.26.10，Python3.11。
- macOS Intel：macos-15-intel，Go1.26.10，Python3.11。
- macOS Apple Silicon：macos-15，Go1.26.10，Python3.11。
- 最低版本组合：Linux x64、Go1.24.11、Python3.10。

runner标签采用GitHub官方支持的标准runner。具体可用额度由仓库计划及账户设置决定。

## 每组实际执行

Node.js SDK 新增同一原生矩阵的检查：前五组使用 Node 24，最低版本组使用 Node 22。
Node 0.7 将十一组Python SDK的146个场景映射为对应Node用例，包含13项双工、23项SSE、21项通知及10项代理验收，连同原有16项共162项；源码与隔离安装包均执行。
每组各运行30秒Node资源回收测试；Linux当前Go组合另跑300秒，检查RSS、句柄/描述符、Go堆及goroutine。
该Linux组合还分别运行60秒双工与SSE混合资源回收检查；SSE轮换成功、取消、非法头、解析超限和解码失败，保存逐批资源快照。
Linux当前Go组合另跑60秒、32路并发代理混合长测，验证三种scheme下认证拒绝、握手取消及共享H2流取消的回收。
Linux当前Go组合另跑60秒双工混合长测，覆盖同连接连续收发、取消、手动交换和提前拒绝，防止取消后流控记账漂移。
`npm run test:parity`检查Python新增/删除用例是否仍有精确映射；语言差异见node/PARITY.md。
在已有原生/Python/C 检查通过后执行 `npm ci --prefix node`、`npm test --prefix node` 和
`npm run test:package --prefix node`。安装包检查在临时项目安装 tarball，重复真实动态库测试并验证 ESM/CJS 包名入口。
Node 证据保存在 `build/node-*.json`，编译后的 SDK 同原生证据一起上传；失败会使现有汇总检查失败。
隔离安装检查还会保留实际测试过的 npm tarball、SHA256 文件和 `dist/node-package-info.json`，
清单记录 npm 包与配套动态库校验值、平台、Node版本和运行提交；候选npm包内置本平台Go动态库，独立动态库也继续提供。安装阶段禁用生命周期脚本、清除FASTLS_LIBRARY并使用空工具搜索路径；检查错误架构、库摘要、缺失文件和显式外部覆盖。包内native/manifest.json记录实际原生依赖及最低glibc/macOS要求；许可证缺失作为发布前待处理项记录。
各平台结果以同一提交的Actions退出状态和artifact中的逐项记录为准，映射清单及配置加入不代表已通过。

构建c-shared动态库及头文件；运行依赖版本门禁测试、核心与桥接Go测试、Python源码SDK集成测试、profile和生成器测试，以及fhttp HTTP2的显式空/零设置与连接设置契约测试、vet检查。
随后构建wheel、从wheel加载SDK重复集成测试、编译并执行独立C调用程序。公网检测站点不作为CI必需依赖，网络请求测试使用本地靶场。
Linux x64当前Go组合附加race测试。其它组合不声称完成race覆盖。
所有作业完成后由固定检查名 `All native tests` 汇总；失败、取消、跳过都不会被计为通过。
可将这个检查设为分支保护的必需检查。

每组上传独立命名的artifact，保留14天：动态库、C头文件、wheel、C示例可执行文件、构建摘要和检查日志。
当前wheel不内置动态库，尚未完成manylinux或macOS最低系统版本的发布兼容认证。

## 上线前必须绑定当前依赖

2026-10-23经用户授权，已将相关实现推送到三个上游的v2开发分支，原main和旧标签不变。ci/dependencies.json固定以下审阅提交：

- fastls：4c8b6239292dacbfc39e5477a7a6361320622638。
- fhttp：e027ce786984d3e7e831b0d10c54875dec746b35（修复已取消流DATA导致的连接接收额度膨胀）。
- imitate：28b32d452256e3acbdb2e904869e5c4331bf0177。

Fastls依赖包含连接生命周期、隔离的私有CA根配置、可复用Session、多值头/CookieStore及流式解码/分层预算；Brotli固定v1.2.0以拒绝截断流。fhttp包含HTTP2接收窗口修复、可取消的共用建连及独立空闲超时。动态库须重新构建；核心包测试执行顺序请求、重定向、关闭后重连、活动响应体及并发回归，封装侧重新执行全部同步/异步与流式测试。

更新依赖时，先审阅并推送上游相关实现，再更新完整40位SHA。缺失或非法SHA会使门禁明确失败，不检出旧main冒充通过。
可以通过以下任一方式提供，优先级从高到低：

1. 手动运行工作流的fastls_ref、fhttp_ref、imitate_ref输入。
2. 仓库Actions变量FASTLS_REF、FHTTP_REF、IMITATE_REF。
3. 提交到ci/dependencies.json的fastls_ref、fhttp_ref、imitate_ref。

不接受浮动分支名。上游各自的模块候选版本仍为v0.1.0-dev.20261023.8；本地go.work显式绑定检出的提交，无需先发布Go模块标签。
本仓库push会启动工作流；工作流不是本地文件写好后就已在GitHub执行。

## 本地复现

已有兄弟仓库及cgo编译器时：

```shell
python scripts/ci.py run
```

可通过CC指定编译器。设置FASTLS_CI_RACE=true启用额外race检查；Windows的本机Zig特殊race布局不用于GitHub Linux检查。
CI脚本核对Go目标与runner架构一致；跨平台二进制必须在相应runner上实际加载。
运行证据写入build/ci-*/results.json，包含整体结果、每项退出码及检出的仓库提交。

当前状态：工作流已配置，上游提交已推送并绑定。远端运行结果应以GitHub Actions实际作业及artifact为准。

## 稳定性扩展

每组额外构建独立TLS靶场，源码SDK和wheel各执行11项稳定性用例，包含HTTPS H1/H2、128路混合成功/取消、256句柄限制、64MiB下载和各30秒持续运行。Linux当前Go组合另执行300秒连续运行，以及桥接、核心和HTTP2窗口回归的race检查。
所有内存、goroutine、句柄样本和用例结果随artifact保存；阈值和结论边界见TESTING_STAGE2.md。

网络异常扩展在每组源码和wheel各运行11项测试，覆盖TCP/TLS停滞、异常HTTP报文、HTTP2流重置、正文截止时间、重定向方法及敏感头处理。范围与复现步骤见[TESTING_STAGE3.md](TESTING_STAGE3.md)，结果写入build/stability-network-*.json；任何失败都会阻止CI通过。

会话复用阶段在源码和wheel各增加6项真实连接ID验收；核心Session回归及fhttp共用建连取消回归也为必需检查，并在Linux额外执行race。见[POOLING.md](POOLING.md)。

多值头和Cookie阶段在源码与wheel各增加13项真实库测试，额外执行fhttp/cookiejar完整单元测试，并将原生CookieStore并发清空回归纳入核心race。见[COOKIES_HEADERS.md](COOKIES_HEADERS.md)。

流式解码与分层超时在源码与wheel各增加15项验收，包含独立压缩向量、64MiB解码及SHA256、截断/校验和、原始视图、大小边界、每跳超时及HTTP2流隔离。核心解码与定时器回归纳入race；见[STREAMING_TIMEOUTS.md](STREAMING_TIMEOUTS.md)。

流式上传另增加源码/wheel各16项验收，覆盖64MiB H1/H2、异步生产者、背压、取消、提前响应及write停顿预算；见[STREAMING_UPLOAD.md](STREAMING_UPLOAD.md)。

HTTP/2全双工另增加源码/wheel各13项验收，覆盖上传前首条响应、64MiB双向校验、半关闭、独立读写预算、慢消费者、生产者异常及取消清理；核心生命周期回归纳入race，见[DUPLEX.md](DUPLEX.md)。

SSE另增加源码/wheel各23项验收，覆盖逐字节与所有分割位置、H1/H2、id/retry、压缩异常、40MiB事件流、背压、心跳/超时及全双工组合，见[SSE.md](SSE.md)。

状态通知另增加源码/wheel各20项验收，覆盖空闲无周期查询、并发初始化、取消竞态、通道失败、兼容模式和资源回收；Go通知回归纳入race，新增独立C等待程序的编译及64KiB正文校验。见[NOTIFICATIONS.md](NOTIFICATIONS.md)。

## 上游完整测试的范围

本地试跑完整fhttp/http2测试已记录失败，包括依赖用户目录charles_cert.pem的用例、原版Accept-Encoding假设、TLS1.0/1.1默认版本变化及PushPromise的Host断言。
原始失败日志保留于build/ci-20261023T061821035135Z/fhttp-h2.log。
默认必需检查采用现有Fastls验收入口相同的可移植HTTP2契约范围，不宣称fhttp完整测试已通过。
需要诊断完整上游测试时，可在workflow_dispatch勾选full_fhttp_tests；它会在各组完成封装测试后附加完整HTTP2测试，任何失败都会使该次运行失败。也可本地设置FASTLS_FULL_FHTTP_TESTS=true运行。

## Python基础靶场并发队列

Python 3.10/3.11基础测试的ThreadingHTTPServer默认backlog为5，而事件循环验收同时建立8路HTTP/1连接。macOS Intel出现该项2秒总超时失败后，靶场改用显式32连接等待队列，保持原有8路请求、2秒预算、正文和取消断言。Node对应项同时验证8路HTTP/1、每路512KiB正文及原有32路H2检查；未通过放宽SDK超时消除失败。
