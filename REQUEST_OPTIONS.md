# Python 请求参数契约

同步 `Session` 和异步 `AsyncSession` 的 `request/get/post/stream` 支持相同参数，继续使用 `profile="chrome/156/windows"`。编码由 Python 标准库完成，无新增运行依赖。

```python
from fastls import Session

with Session(profile="chrome/156/windows") as session:
    response = session.get("https://example.com/search", params={"q": "中文", "tag": ["a", "b"]})
    response.raise_for_status()
    response = session.post("https://example.com/api", json={"enabled": True})
    response = session.post("https://example.com/form", data=[("tag", "a"), ("tag", "b")])
    with open("archive.bin", "rb") as upload:
        response = session.post("https://example.com/upload", data={"description": "附件"},
                                files={"file": ("archive.bin", upload, "application/octet-stream")})
```

## 查询和正文

- `params` 接受映射、键值对列表/元组，或已编码的 ASCII 查询字符串（不能含 `#`）。追加到原查询之后，保留原查询的编码及 URL fragment；fragment 不发送给服务器。列表值展开为重复键，`None` 值省略，空字符串保留，布尔值编码成 `True`/`False`。键为字符串/bytes，标量值为字符串/bytes/数字；Unicode 使用 UTF-8，空格为 `+`。不接受生成器形式的键值对。
- `json` 使用严格 JSON、UTF-8，无 ASCII 转义，不允许 NaN/Infinity。显式 `json=None` 发送 `null`；省略 `json` 不生成正文。与非 `None` 的 `data` 或 `files` 同用立即报错，与 Requests 静默选择正文来源的行为不同。
- 映射或键值对列表/元组形式的 `data` 使用 `application/x-www-form-urlencoded`，重复键、Unicode 和空值与 `params` 相同。空列表是空表单。原有字符串、bytes、二进制文件及字节块迭代器继续是原始正文；字节块列表不会变成表单。
- JSON、表单在 Python 内存中编码；它们不是大型数据的增量编码器。超过原生内联阈值后仍分块传输。大型原始数据继续使用文件或迭代器，响应缓冲仍限制为 32 MiB。
- JSON/表单只在没有会话/请求级 `Content-Type` 时设置默认类型，保留显式类型。编码正文由库管理长度和传输方式，传入 `Content-Length` 或 `Transfer-Encoding` 报错；原始 `data` 保持既有规则。

## multipart 文件

- `files` 接受映射或 `(字段名, 文件描述)` 列表，后者可重复字段。文件描述可为 bytes、二进制文件，或 `(filename, body[, content_type])`。未指定类型时由文件名推断，否则使用 `application/octet-stream`。`filename=None` 不生成 filename 参数。
- 可同时用 `data` 提供普通表单字段。文件体逐块读取，每次最多 64 KiB；不读取全部文件、不 seek、不计算文件长度、不创建临时文件。异步请求同样分块，但同步文件的 `read()` 在事件循环内执行，慢磁盘应由调用方预先处理。
- 编码器生成随机 boundary，独占 `Content-Type`；会话或请求中显式设置该头会报错，避免边界不一致。字段名、文件名、类型不允许控制字符。不支持四元素自定义分部头、异步文件、文件体生成器或 multipart 与 duplex 组合。
- 文件由调用方拥有，成功、失败、取消后均不自动关闭；请求从文件当前位置开始，后续重发需调用方显式重新打开/定位。编码器只使用一次，发生读取错误立即传播原异常并回收请求。
- multipart 沿用已有流式上传的重定向契约：不会自动重放一次性正文。307/308 不重放；301/302/303 的正文丢弃和 GET 转换沿用核心规则。需要重新上传时由调用方检查响应并显式开启新请求。缓冲 JSON/表单沿用既有缓冲正文的重定向规则；超过内联阈值后视为流式正文。
- 同步手动 `duplex()` 只新增 `params`；正文继续使用 `send()`。异步 duplex 允许已有 `data`，也可用 JSON/表单，但禁用自动重定向；不支持 multipart。

## 响应

`response.encoding` 初值来自 `Content-Type` 的 charset，缺省 UTF-8，可在读取 `.text` 前修改。文本解码替换坏字节；未知编码名抛出 `LookupError`，不自动猜测编码。`.json()` 仍直接解析原始字节，独立于 `.encoding`，保留 JSON 的 UTF/BOM 识别。

`response.ok` 表示状态码小于 400；`raise_for_status()` 对 400–599 抛出 `FastlsHTTPError`，其它状态返回 `None`。异常提供 `.response`、`.status_code`，消息只含状态码。异步响应缓冲读取后共享这些方法；流式场景可在消费正文前检查状态，仍需通过上下文或 `aclose()` 回收请求。

这是明确限定的便利 API，不承诺完整 Requests 兼容；不增加环境代理、自动重试、一次性正文重放或编码探测。
