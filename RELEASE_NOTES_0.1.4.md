# fastls 0.1.4

## 新增

- Python 模块级同步请求：`fastls.request()`、`get()`、`post()`、`put()`、`patch()`、`delete()`、`head()` 和 `options()`。
- 便捷方法每次创建临时会话，完整缓存响应后关闭会话；返回后仍可读取正文和响应头。连续请求、登录态和流式操作继续使用 Session / AsyncSession。
- 便捷方法的完整关键字参数类型提示，以及 Response、Headers 常用属性与方法的类型注解。wheel 和 sdist 包含 api.pyi 与 py.typed，支持 Python 3.10+，无新增 Python 运行依赖。

## 文档与验证

- 完善 Python 使用与类型提示说明，整理 README 的使用入口、限制和维护文档。
- 新的 Python 专有 API 测试接入源码和安装包验收；保留原有 209 项 Python/Node 跨语言测试基线，未宣称 Node 提供相同模块级入口。
- 发布门禁检查类型提示文件完整性，并执行 Python 3.10 静态类型检查。

## 安装

```sh
python -m pip install --only-binary=:all: fastls==0.1.4
```

平台要求延续上一版：Python 3.10+；Windows x64、Linux glibc 2.39+ x64/ARM64、macOS 15+ Intel/Apple Silicon。不支持 Alpine/musl。Go 核心、C ABI 与 Node npm 版本不因本次 Python 版本发布而升级。
