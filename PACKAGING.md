# Python 平台候选包

本阶段生成内置动态库的候选 wheel；不发布 PyPI 或 Release。验证结果以对应提交的 Actions 为准。

## 安装与加载

下载匹配平台的 wheel 后运行 `python -m pip install /path/to/fastls_ffi-....whl`。不需要 Go/C 编译器或源码目录，继续使用 `Session(profile="chrome/156/windows")`。

库加载顺序：显式 `library` → `FASTLS_LIBRARY` → 包内 `fastls/_lib/` → 源码开发目录的 `dist/`。显式选择失败直接报错，不静默切换。源码 SDK 安装没有包内库时须提供外置库，不在安装或导入时下载/编译 Go 代码。

`_lib/build-info.json` 记录原生 ABI、四仓库提交、工具链、源码摘要及库 SHA-256。`_lib/native-audit.json` 记录 ELF/Mach-O/PE 依赖信息。源码摘要目前包含绝对路径，不宣称不同目录或工具链的构建逐字节一致。

## 候选平台范围

- Windows amd64：`py3-none-win_amd64`，原生 Windows runner 验收。
- Linux amd64/arm64：原生 Ubuntu 24.04 runner，使用普通 `linux_*` 标签。附 ELF 所需 glibc 符号和动态依赖；尚未认证 manylinux 或 musllinux，不保证所有 Linux 发行版可用。
- macOS amd64/arm64：保守使用构建及验收主机的系统大版本作为 wheel 最低版本，不沿用可能早于 Go 运行时要求的 Python 最低部署标签。附 `otool` 依赖和加载命令。
- Python 最低版本仍为 3.10；当前六组矩阵没有穷举其后所有 Python 版本。

wheel 的 `py3-none` 表示通过 ctypes 调用 C ABI，没有 CPython 扩展 ABI 依赖；平台字段及非纯 Python 标记仍然必需。

## 构建与验收

有已检出的固定依赖和 cgo 工具链时，`python scripts/ci.py run --stage build` 构建 native、fixture、审计记录及 bundled wheel。`FASTLS_BUNDLE_LIBRARY` 是打包时的内部输入，必须指向与 `build-info.json` 一致的主机库；正常用户安装不用设置。

`python scripts/test_wheel.py --all` 将 wheel 用 pip 安装到源码目录之外的临时目录，在隔离 Python 子进程核对导入位置、加载路径、哈希和外置覆盖规则，再运行全部 SDK 套件。首先清除编译器搜索路径做 HTTPS/WS 安装探测；完整套件使用原有资源采样工具。fixture 与测试驱动属于测试端，库不会调用编译器。

`dist/ci-inputs.json` 绑定原生库、fixture、wheel、审计记录、workspace 与 SDK 输入。下游阶段拒绝不同提交、架构或哈希的产物。

## 后续发布门槛

候选包通过原生测试不等于完成公开发布认证。正式发布前仍需完成许可证/第三方随附材料、sdist 的独立安装说明和验收、版本升级/卸载验收，以及拟支持的 Linux 最低 libc/系统范围认证。本轮不自动扩展平台承诺。
