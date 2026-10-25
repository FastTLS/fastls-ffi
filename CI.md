# GitHub Actions 持续测试

`.github/workflows/ci.yml` 在每次push、PR更新以及手动workflow_dispatch时执行。普通运行只读且不发布；只有 `v*` 标签在全部必需作业成功后获得 GitHub contents/attestation 权限并创建 GitHub Release，不上传外部软件包索引。
使用各系统原生runner构建并运行，而不是把交叉编译成功当成运行测试成功。
依赖入口先在关闭 `go.work` 的环境下载并校验 `go.sum`，列出远端模块图并拒绝遗留 `fhttp`；随后才检出固定提交执行本地源码矩阵。

## 测试矩阵

- Windows x64：windows-2022，Go1.26.10，Python3.11。
- Linux x64：ubuntu-24.04，Go1.26.10，Python3.11，附加桥接与核心race检查。
- Linux ARM64：ubuntu-24.04-arm，Go1.26.10，Python3.11。
- macOS Intel：macos-15-intel，Go1.26.10，Python3.11。
- macOS Apple Silicon：macos-15，Go1.26.10，Python3.11。
- 前瞻版本组合：Linux x64、Go1.27.3、Python3.10。

runner标签采用GitHub官方支持的标准runner。具体可用额度由仓库计划及账户设置决定。

## 每组实际执行

构建c-shared动态库及头文件；运行依赖版本门禁测试、核心与桥接Go测试、Python源码SDK集成测试、profile和生成器测试，以及ftransport的有序HTTP/1.1、HTTP/2显式空/零设置、流控、连接池和生命周期契约测试与vet检查。
wheel 内置动态库，安装到隔离临时目录后重复集成测试；编译并执行独立 C 调用程序。公网检测站点不作为CI必需依赖，网络请求测试使用本地靶场。
Linux x64当前Go组合附加race测试。其它组合不声称完成race覆盖。
所有作业完成后由固定检查名 `All native tests` 汇总；失败、取消、跳过都不会被计为通过。
可将这个检查设为分支保护的必需检查。

每组上传独立命名的artifact，保留14天：动态库、C头文件、wheel、C示例可执行文件、构建摘要和检查日志。
当前候选 wheel 内置动态库；使用范围与尚未完成的发布认证见 [PACKAGING.md](PACKAGING.md)。

## 上线前必须绑定当前依赖

F3 本地集成分支已生成不可变提交，`ci/dependencies.json` 固定以下候选；执行远端矩阵前须先推送这些提交，当前文档不把本地提交声称为已发布：

- fastls：661930e5f62f6d8abb3235da339467e5f33643da（`v0.1.0`）。
- ftransport：2e1d66a7983b4fe730d18c3b8b90831ae6e93db5（`v0.1.0`）。
- imitate：1ef806aec3fca9607c083e375a72e84ae542d6bd（`v0.1.0`）。

Fastls依赖包含连接生命周期、隔离的私有CA根配置、可复用Session、多值头/CookieStore及流式解码/分层预算；Brotli固定v1.2.0以拒绝截断流。ftransport负责标准库请求/响应、HTTP/1.1头顺序、HTTP/2帧与流控、可取消的共用建连及独立空闲超时。动态库须重新构建；核心包测试执行顺序请求、重定向、关闭后重连、活动响应体及并发回归，封装侧重新执行全部同步/异步与流式测试。

更新依赖时，先审阅并推送上游相关实现，再更新完整40位SHA。缺失或非法SHA会使门禁明确失败，不检出旧main冒充通过。
可以通过以下任一方式提供，优先级从高到低：

1. 手动运行工作流的fastls_ref、ftransport_ref、imitate_ref输入。
2. 仓库Actions变量FASTLS_REF、FTRANSPORT_REF、IMITATE_REF。
3. 提交到ci/dependencies.json的fastls_ref、ftransport_ref、imitate_ref。

不接受浮动分支名。上游依赖使用 `v0.1.0` 正式标签，可在关闭 `go.work`、没有 `replace` 的干净模块中解析；本地 `go.work` 仍显式绑定标签对应的同一批提交。
本仓库push会启动工作流；工作流不是本地文件写好后就已在GitHub执行。

## 本地复现

已有兄弟仓库及cgo编译器时：

```shell
python scripts/ci.py run
```

可通过CC指定编译器。设置FASTLS_CI_RACE=true启用额外race检查；Windows的本机Zig特殊race布局不用于GitHub Linux检查。
CI脚本核对Go目标与runner架构一致；跨平台二进制必须在相应runner上实际加载。
运行证据写入 `build/ci-*/<stage>/results.json`，包含整体结果、每项开始时间、耗时、退出码及检出的仓库提交；子进程日志实时输出，逐项持久化，失败保留先前记录。

当前状态：工作流已配置，上游提交已推送并绑定。远端运行结果应以GitHub Actions实际作业及artifact为准。

## 稳定性扩展

每组额外构建独立TLS靶场，源码SDK和wheel各执行11项稳定性用例，包含HTTPS H1/H2、128路混合成功/取消、256句柄限制、64MiB下载和各30秒持续运行。Linux当前Go组合另执行300秒连续运行，以及桥接、核心和HTTP2窗口回归的race检查。
所有内存、goroutine、句柄样本和用例结果随artifact保存；阈值和结论边界见TESTING_STAGE2.md。

网络异常扩展在每组源码和wheel各运行11项测试，覆盖TCP/TLS停滞、异常HTTP报文、HTTP2流重置、正文截止时间、重定向方法及敏感头处理。范围与复现步骤见[TESTING_STAGE3.md](TESTING_STAGE3.md)，结果写入build/stability-network-*.json；任何失败都会阻止CI通过。

会话复用阶段在源码和wheel各增加6项真实连接ID验收；核心Session回归及ftransport共用建连取消回归也为必需检查，并在Linux额外执行race。见[POOLING.md](POOLING.md)。

多值头和Cookie阶段在源码与wheel各增加13项真实库测试，并将标准库CookieStore并发清空回归纳入核心race。见[COOKIES_HEADERS.md](COOKIES_HEADERS.md)。

流式解码与分层超时在源码与wheel各增加15项验收，包含独立压缩向量、64MiB解码及SHA256、截断/校验和、原始视图、大小边界、每跳超时及HTTP2流隔离。核心解码与定时器回归纳入race；见[STREAMING_TIMEOUTS.md](STREAMING_TIMEOUTS.md)。

流式上传另增加源码/wheel各16项验收，覆盖64MiB H1/H2、异步生产者、背压、取消、提前响应及write停顿预算；见[STREAMING_UPLOAD.md](STREAMING_UPLOAD.md)。

HTTP/2全双工另增加源码/wheel各13项验收，覆盖上传前首条响应、64MiB双向校验、半关闭、独立读写预算、慢消费者、生产者异常及取消清理；核心生命周期回归纳入race，见[DUPLEX.md](DUPLEX.md)。

SSE另增加源码/wheel各23项验收，覆盖逐字节与所有分割位置、H1/H2、id/retry、压缩异常、40MiB事件流、背压、心跳/超时及全双工组合，见[SSE.md](SSE.md)。

状态通知另增加源码/wheel各20项验收，覆盖空闲无周期查询、并发初始化、取消竞态、通道失败、兼容模式和资源回收；Go通知回归纳入race，新增独立C等待程序的编译及64KiB正文校验。见[NOTIFICATIONS.md](NOTIFICATIONS.md)。

## 上游完整测试的范围

ftransport 的完整测试与 vet 现在是 contracts 阶段的必需检查；Linux race 阶段同时覆盖 fastls、ftransport 与桥接层。旧 fhttp 诊断入口已移除。

## 并行阶段

`run` 默认仍完整串行执行，便于本地复现；`run --stage build|contracts|source|wheel|release|race|soak` 提供独立入口。release阶段核对许可证材料、sdist、从旧版本升级到平台wheel、卸载清理及Linux glibc边界。

Actions 先执行六组原生构建，每组只构建一次 library、fixture 和 wheel，上传带哈希的输入产物。构建矩阵完成后，六组 contracts/source/wheel 分别作为独立 job 并行运行；当前 Linux x64 的 race 与 300 秒 soak 也使用独立 runner，不争用功能测试或性能测量的机器。代价是更多 runner 启动、checkout 和产物下载；实际总耗时取决于额度与队列，不预设提速倍率。

所有消费者核对同一次 workflow 的提交、SDK 输入、依赖 SHA、目标架构和原生/wheel 哈希。下载的 Unix fixture 恢复执行权限；跨 runner 的 workspace 使用相同相对布局。`All native tests` 同时要求 dependencies、native、tests、extended 成功；短版检查不替代完整验收。工作流仍在每次代码 push/PR 执行，纯文档分类暂未实现。

源码与 wheel 套件各自在一个 job 内顺序执行，避免其资源统计互相干扰。竞态与稳定性也不在同一 runner 上竞争。未来进一步拆分套件，须先验证端口、文件和全局统计隔离。
