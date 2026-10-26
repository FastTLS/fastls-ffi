> 历史维护/阶段记录：下文按当时阶段保存，不代表当前发布状态。当前安装、能力与限制以 [README](README.md)、[统一文档](https://fasttls.github.io/) 和实际发布标签为准。

# 第一阶段本地验收

日期：2026-10-23（Asia/Shanghai）。平台：Windows x64，Go1.26.10、Python3.11、已有Zig0.14.1工具链。

## 实际通过

- Windows x64 c-shared DLL及C头文件构建完成。构建前后对Go选中的110个本地源文件/模块文件校验，输入摘要一致。
- Go桥接4项测试通过：二进制完整性与稳定EOF、慢消费者背压与取消、输入校验/未知ID/超时、未受信TLS证书/截断响应。
- Fastls核心3个包测试通过，包含新增DoContext取消与流式首块测试。
- Python7项集成测试通过：同步流、异步流、响应体读取期间取消、响应头前取消、事件循环响应、8请求并发、二进制上传/下载、用户头覆盖、重定向、超时、关闭及非法C参数/句柄。
- 打包生成Python wheel，并直接从wheel导入SDK重新运行上述7项测试通过。
- 独立C可执行程序调用DLL，枚举152条指纹并找到chrome/156/windows。
- Python→DLL→Fastls实际请求 https://tls.peet.ws/api/all，返回200、HTTP/2，自定义UA为fastls-python-ffi/0.1；Match为chrome/156/windows，协议基线为Chrome150。
- 桥接包race测试通过。Windows使用已有scripts/race-exec.py调整临时race测试PE布局，并使用-lapi-ms-win-core-synch-l1-2-0链接；生产DLL未应用此调整。

完整浏览器指纹一致性不属于此次结论。152条目录是生成器当前目录，不代表152条都已通过实机采集验证。

## 本地产物

- dist/fastls.dll：Windows x64动态库，约20MB。
- dist/fastls.h：由cgo生成的接口声明。
- dist/fastls_ffi-0.1.0.dev0-py3-none-any.whl：纯Python SDK，不包含DLL，需FASTLS_LIBRARY或显式library路径。
- dist/build-info.json：工具链、输入摘要及DLL SHA256。
- dist/peet-check.json：公网HTTPS/HTTP2观测与Match摘要。
- build/checks-20261023T054248Z/：最后一次统一本地检查的日志与results.json，3组检查全通过。目录时间戳使用UTC。

以上构建产物、工作区文件与运行日志均被gitignore排除。正式版本发布前需要独立发布流程。

## 复现

已有cgo兼容编译器的终端内运行：

```shell
python scripts/build.py --local
python scripts/check.py
python -m pip wheel ./python --no-deps -w dist
```

Windows上可用CC或--cc选择已有工具链。构建脚本不安装编译器、不修改全局Go配置。
Go核心改动位于兄弟仓库的index.go（DoContext、VerifyTLS选项）、client.go和roundtripper.go（TCP TLS证书校验传递），回归测试为context_test.go。

## 尚未验收或实现

Linux/macOS实际运行、其它架构、连接池、自动Cookie jar、完整重复响应头、流式内容解压、流式上传、代理、自定义完整Spec、WebSocket、其它语言SDK以及稳定发布。
asyncio当前使用定时轮询，高并发性能尚未专项测量。上游仍是未发布的开发候选，需要兄弟仓库联调。
本节记录第一阶段本地验收，当时未创建提交、推送、标签或远端Release。后续用户已授权推送开发分支并接入GitHub Actions，当前依赖与执行配置见CI.md；仍未创建发布标签或Release。
