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

构建c-shared动态库及头文件；运行依赖版本门禁测试、核心与桥接Go测试、Python源码SDK集成测试、profile和生成器测试，以及fhttp HTTP2的显式空/零设置与连接设置契约测试、vet检查。
随后构建wheel、从wheel加载SDK重复集成测试、编译并执行独立C调用程序。公网检测站点不作为CI必需依赖，网络请求测试使用本地靶场。
Linux x64当前Go组合附加race测试。其它组合不声称完成race覆盖。
所有作业完成后由固定检查名 `All native tests` 汇总；失败、取消、跳过都不会被计为通过。
可将这个检查设为分支保护的必需检查。

每组上传独立命名的artifact，保留14天：动态库、C头文件、wheel、C示例可执行文件、构建摘要和检查日志。
当前wheel不内置动态库，尚未完成manylinux或macOS最低系统版本的发布兼容认证。

## 上线前必须绑定当前依赖

2026-10-23经用户授权，已将相关实现推送到三个上游的v2开发分支，原main和旧标签不变。ci/dependencies.json固定以下审阅提交：

- fastls：f0fe1707d6c9131f0ed7aaaab5861596801982fd。
- fhttp：f86a33d13ece257abeabf62ccaec88a2779777ce。
- imitate：28b32d452256e3acbdb2e904869e5c4331bf0177。

Fastls依赖已更新到连接生命周期修复：TLS协议发现连接只交接一次，传输缓存访问加锁，空闲连接清理委托给协议传输层。Python SDK公开接口不变，动态库须重新构建；核心包测试会执行新增的顺序请求、重定向、关闭后重连、活动响应体及并发回归，封装侧重新执行全部同步/异步与流式测试。

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

## 上游完整测试的范围

本地试跑完整fhttp/http2测试已记录失败，包括依赖用户目录charles_cert.pem的用例、原版Accept-Encoding假设、TLS1.0/1.1默认版本变化及PushPromise的Host断言。
原始失败日志保留于build/ci-20261023T061821035135Z/fhttp-h2.log。
默认必需检查采用现有Fastls验收入口相同的可移植HTTP2契约范围，不宣称fhttp完整测试已通过。
需要诊断完整上游测试时，可在workflow_dispatch勾选full_fhttp_tests；它会在各组完成封装测试后附加完整HTTP2测试，任何失败都会使该次运行失败。也可本地设置FASTLS_FULL_FHTTP_TESTS=true运行。
