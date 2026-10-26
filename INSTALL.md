# Python SDK 安装指南

本指南以 GitHub Release [v0.1.3](https://github.com/FastTLS/fastls-ffi/releases/tag/v0.1.3) 为例。平台 wheel 已包含原生动态库，普通安装不需要 Go、C 编译器、克隆源码或单独下载 DLL/dylib/so 文件。

发行包名和 Python 导入名都是 `fastls`。推荐从 [PyPI](https://pypi.org/project/fastls/0.1.3/) 安装：

```shell
python -m pip install --only-binary=:all: fastls==0.1.3
```

`--only-binary=:all:` 防止没有匹配平台时安装缺少原生库的 SDK-only sdist。

历史 v0.1.2 的发行包名为 `fastls-ffi`，已有 GitHub 附件保持原名。从旧包迁移时，先执行 `python -m pip uninstall fastls-ffi`，再安装 `fastls`。两者都提供 `fastls` 导入目录，不应在同一个环境并存。重新安装历史包前，同样先卸载新包 `fastls`。

## 1. 环境与平台选择

需要 Python 3.10 或更新版本。请按 **Python 解释器实际运行的平台和架构**选择 wheel，而不是按希望模拟的浏览器配置选择。例如，在 Linux 安装 Linux wheel 后，仍可使用 `chrome/156/windows`。

查看解释器信息：

```shell
python -c "import platform, struct, sys; print(sys.version); print(platform.system(), platform.machine(), struct.calcsize('P') * 8)"
```

下面命令统一使用 `python`。Linux/macOS 若只有 `python3`，请相应替换；Windows 也可使用已选择正确版本的 `py -3`。

v0.1.3 的平台范围：

- Windows：64 位 x86 Python，使用 `win_amd64`；本版没有 Windows ARM64 原生 wheel。
- Linux x86_64：使用 `manylinux_2_39_x86_64`；ARM64/aarch64 使用 `manylinux_2_39_aarch64`。
- Linux wheel 当前仅认证 Ubuntu 24.04 兼容系统、glibc 2.39 或更新版本，不承诺所有 Linux 发行版可用，也不支持 Alpine/musl。可用 `ldd --version` 辅助确认 glibc 版本。
- macOS Intel：使用 `macosx_15_0_x86_64`；Apple Silicon 原生 Python 使用 `macosx_15_0_arm64`。本版两者均要求 macOS 15 或更新版本。Apple Silicon 上运行 Rosetta/x86_64 Python 时，应选择 x86_64 wheel。

`py3-none` 表示通过 ctypes 调用 C ABI，没有 CPython 扩展 ABI 绑定；文件仍含有指定平台的原生库，不能跨平台通用。完整分发边界见 [PACKAGING.md](PACKAGING.md)。

## 2. 建议使用虚拟环境

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Linux/macOS：

```shell
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

若 PowerShell 禁止运行激活脚本，可直接用 `.venv\Scripts\python.exe` 替代后续命令中的 `python`，无需修改系统执行策略。

## 3. 直接从 GitHub 安装

只运行与当前解释器匹配的一条命令。

### Windows x64

```shell
python -m pip install "https://github.com/FastTLS/fastls-ffi/releases/download/v0.1.3/fastls-0.1.3-py3-none-win_amd64.whl"
```

### Linux x64

```shell
python -m pip install "https://github.com/FastTLS/fastls-ffi/releases/download/v0.1.3/fastls-0.1.3-py3-none-manylinux_2_39_x86_64.whl"
```

### Linux ARM64

```shell
python -m pip install "https://github.com/FastTLS/fastls-ffi/releases/download/v0.1.3/fastls-0.1.3-py3-none-manylinux_2_39_aarch64.whl"
```

### macOS Intel，macOS 15+

```shell
python -m pip install "https://github.com/FastTLS/fastls-ffi/releases/download/v0.1.3/fastls-0.1.3-py3-none-macosx_15_0_x86_64.whl"
```

### macOS Apple Silicon，macOS 15+

```shell
python -m pip install "https://github.com/FastTLS/fastls-ffi/releases/download/v0.1.3/fastls-0.1.3-py3-none-macosx_15_0_arm64.whl"
```

也可从发布页下载 wheel，再安装本地文件。例如 Windows x64：

```shell
python -m pip install ./fastls-0.1.3-py3-none-win_amd64.whl
```

不要重命名 wheel 来绕过平台检查；改名不会改变动态库的架构或系统依赖。在线安装需要能够访问 GitHub 的发布附件下载服务。

## 4. 验证安装与动态库加载

先确认安装版本：

```shell
python -c "from importlib.metadata import version; print(version('fastls'))"
```

预期输出 `0.1.3`。然后运行以下 Python 代码，它会实际创建会话、加载原生库并列出配置，不需要访问公网：

```python
from fastls import Session

with Session(profile="chrome/156/windows") as session:
    print(session.list_profiles())
```

仅执行 `import fastls` 不足以验证动态库加载。公网请求、流式与异步示例见 [README.md](README.md)。

## 5. 升级与卸载

从 PyPI 升级可执行 `python -m pip install --upgrade --only-binary=:all: fastls`。从 GitHub 升级时，从 [Releases](https://github.com/FastTLS/fastls-ffi/releases) 选择目标版本及平台，将对应完整下载地址交给 `python -m pip install --upgrade "下载地址"`；地址中的版本目录与文件名版本必须一致。重新安装本版可使用：

```shell
python -m pip install --force-reinstall "https://github.com/FastTLS/fastls-ffi/releases/download/v0.1.3/fastls-0.1.3-py3-none-win_amd64.whl"
```

上例适用于 Windows x64；其它平台替换为上面的匹配地址。更新后重启使用该库的 Python 进程或 Notebook 内核，避免继续使用已加载的旧动态库。

卸载：

```shell
python -m pip uninstall fastls
```

## 6. 常见问题

- **`not a supported wheel on this platform`**：核对 Python 的系统、位数和架构，以及 macOS 最低版本；确认使用了当前环境的 `python -m pip`。可执行 `python -m pip debug --verbose` 查看接受的兼容标签。
- **`ModuleNotFoundError: fastls`**：通常是安装和运行用了不同的 Python。用 `python -m pip show fastls` 和 `python -c "import sys; print(sys.executable)"` 核对环境。
- **`native library is missing` / `Cannot load Fastls native library`**：确认安装的是平台 wheel，而不是仅含 SDK 的源码包；核对系统边界。若以前设置过 `FASTLS_LIBRARY`，它会覆盖包内库；不再需要时清除该变量，并移除代码中旧的 `library=...`。
- **Linux 报 `GLIBC_... not found`**：系统低于该产物依赖要求。使用符合要求的系统/容器，或另行构建并验证适合目标系统的库；当前不能仅通过改 wheel 标签获得旧系统兼容性。
- **下载失败**：可在能访问 GitHub 的机器下载正确 wheel，传到目标机器后本地安装。

加载优先级是 `Session(library=...)` → `FASTLS_LIBRARY` → wheel 包内动态库 → 源码开发目录的 `dist/`。正常 wheel 用户无需设置库路径。

## 7. 发布附件与源码安装

普通用户只需匹配的 `.whl`。其它附件用于追溯与校验：`SHA256SUMS` 记录发布文件摘要，`build-info-*.json` 记录源码提交、工具链、ABI 和库摘要，`native-audit-*.json` 记录原生依赖，来源证明见发布页。构建环境字段不是所有兼容系统的完整清单。

SDK-only `.tar.gz` 不包含原生库，也不会在安装或导入时下载/编译它；使用者必须另行提供兼容的 ABI v1 库。开发构建参见 [README 本地构建](README.md#本地构建)。

## 8. PyPI 发布状态

[fastls 0.1.3](https://pypi.org/project/fastls/0.1.3/) 已发布，包含五个平台 wheel 和一个 SDK-only sdist。正式 `v*` 标签通过完整门禁后，由 `ci.yml` 使用 Trusted Publishing 上传；普通分支、PR 和开发快照不上传 PyPI。Linux wheel 的 `manylinux_2_39` 标签要求 glibc 2.39+，不支持 Alpine/musl。维护说明见 [PYPI.md](PYPI.md)。

PyPI 官方说明：[通过 Trusted Publisher 创建项目](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)、[GitHub Actions 发布](https://docs.pypi.org/trusted-publishers/using-a-publisher/)。
