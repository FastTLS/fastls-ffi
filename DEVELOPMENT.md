# 源码开发与文档导航

普通使用者从 [README](README.md) 的平台安装入口开始。源码构建需要 Go 1.26+、Python 3.10+ 和目标平台的 cgo C 编译器；Node 源码还需要 Node.js 22+。

```sh
python scripts/build.py
python -m pip install -e ./python
npm ci --prefix node
npm run build --prefix node
```

动态库产物在 dist/。跨仓开发可在同级放置 fastls、ftransport、imitate，运行 `python scripts/build.py --local`，只生成被忽略的本仓库 go.work。编译器用 CC 或 --cc 指定。不带 --local 使用固定模块版本。源码安装不会自动下载或编译动态库；显式库路径和 FASTLS_LIBRARY 的优先级高于内置库。

## 使用者文档

[安装](INSTALL.md) · [Python](python/README.md) · [Node](node/README.md) · [请求参数](REQUEST_OPTIONS.md) · [Cookie 与头](COOKIES_HEADERS.md) · [连接池](POOLING.md) · [流式与超时](STREAMING_TIMEOUTS.md) · [上传](STREAMING_UPLOAD.md) · [双工](DUPLEX.md) · [SSE](SSE.md) · [代理](PROXY.md) · [WebSocket](WEBSOCKET.md)

## 接口与维护

[C ABI](ABI.md) · [原生通知](NOTIFICATIONS.md) · [CI](CI.md) · [打包](PACKAGING.md) · [PyPI 发布](PYPI.md) · [npm 发布](node/NPM_RELEASE.md) · [Node 行为映射](node/PARITY.md)

## 检查

```sh
go test ./internal/bridge
python tests/test_python.py
python scripts/check.py
npm run test:quick --prefix node
```

完整平台矩阵、源码及安装包测试以 CI.md 为准。历史阶段记录 [VALIDATION.md](VALIDATION.md)、[TESTING_STAGE2.md](TESTING_STAGE2.md)、[TESTING_STAGE3.md](TESTING_STAGE3.md)、[PLAN.md](PLAN.md)、[NEXT_PHASE.md](NEXT_PHASE.md) 是维护资料，不是当前发版承诺。


## Python 便捷 API 与类型提示

模块级同步入口位于 python/fastls/api.py；api.pyi 提供各 HTTP 方法的完整关键字参数签名，py.typed 使安装包可被类型检查器识别。修改参数时同步实现、提示文件及 python/README.md。返回数据为已缓存 Response，每次便捷调用关闭临时会话。

面向 Python 3.10 的消费代码检查：

```sh
npm exec --yes --package=pyright@1.1.414 -- pyright --project tests/pyrightconfig.json
```

这只用于开发验证，不是 Python SDK 的运行依赖。回归测试沿用 `python tests/test_python.py`，需要已构建且匹配的动态库；检查普通 wheel 与 sdist 都包含 api.py、api.pyi 和 py.typed。
