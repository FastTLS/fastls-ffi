# 多值响应头与会话Cookie

从 Python 0.1.4 / Node 0.10.1 起，应用 Cookie 由各语言 SDK 保存和管理。Python 使用 http.cookiejar 并以 publicsuffixlist 校验公共后缀，Node 使用 tough-cookie。动态库的 transport_context_v1 只持有连接池和请求资源，不保存 Cookie；旧 C Cookie 接口仅为旧客户端保留。

同一 SDK Session 的各 profile 共用其 Cookie 罐，不同 Session 及不同语言进程不共享。SDK 逐跳处理重定向，先接收当前响应 Set-Cookie，再为目标 URL 选择 Cookie；总超时跨跳转累计。关闭 Session 清空 Cookie 并释放底层资源。Cookie 不会保存到磁盘。

```python
from fastls import Session

with Session(profile="chrome/156/windows") as session:
    response = session.get("https://example.com/login")
    print(response.headers.get_list("set-cookie"))
    print(session.cookies.get_dict("https://example.com/account"))

    session.cookies.set("token", "value", url="https://example.com", path="/", secure=True)
    session.cookies.delete("token", url="https://example.com", path="/")
    session.cookies.clear()
```

## 响应头

response.headers是大小写不敏感的只读Mapping。headers[name]和headers.get(name)返回该字段的第一个值；get_list(name)返回所有值的独立列表，multi_items()返回包含重复字段的(name,value)列表。不存在的字段使用get_list会返回空列表。

Set-Cookie、Link等字段不会按逗号或旧的/,/分隔符拆分；每个解析后的字段值分别保留。映射不承诺原始字段名大小写、跨字段的线上顺序或原始报文字节。`response.trailers` 在正文成功读到 EOF 后提供相同的多值只读映射；这里只展示最终响应头和 Trailer，重定向中间响应的 Cookie 仍会进入存储。

与此前开发版本的dict相比，Python的头访问现在大小写不敏感且只读；Set-Cookie单值访问返回第一项，获取全部Cookie应使用get_list。

## Cookie规则与操作

- 自动接收每一项Set-Cookie，并按RFC 6265的域名、host-only、路径、Secure、Max-Age和Expires规则选择后续请求的Cookie。SDK 将 localhost 和回环 IP 视为可信本机源，Secure Cookie 可在这些主机的 HTTP 请求中发送；其它 HTTP 主机不会发送。Python 使用 publicsuffixlist，Node 使用 tough-cookie 的公共后缀规则，拒绝跨公共后缀的 Cookie。
- 重定向响应在下一跳发送前更新存储；禁止跟随重定向时仍接收该响应的Cookie。跨主机重定向只会使用目标主机有权接收的存储内容。
- 显式Cookie请求头（包括空字符串）控制整个请求及重定向链的手动Cookie，不再自动追加存储中的Cookie；响应仍更新存储。手动头在跨来源重定向中按 SDK 敏感头规则剥离。
- cookies.get_list(url)返回该URL可发送的{name,value}列表，保留不同路径下的同名Cookie；get_dict(url)及get(name,url=...)优先选择匹配路径更长的一项。这是发送视图，不是包含完整属性的存储导出。
- cookies.set(name,value,url=...,path=...,domain=...,secure=...,http_only=...,max_age=...,expires=...)手动写入。省略path使用URL目录；省略domain为host-only。expires使用RFC3339字符串，例如2100-01-01T00:00:00Z。max_age正值覆盖expires，0或负值删除，省略则由expires或会话寿命决定。
- cookies.delete必须给出与目标Cookie一致的URL、path和domain范围；clear删除整个Session的Cookie，但保留连接池。close_idle_connections释放空闲连接，不清空Cookie。
- Session(cookies=False)或AsyncSession(cookies=False)禁用自动接收和发送，仍可使用显式Cookie头；手动set会报禁用错误。Cookie访问是短同步操作，AsyncSession可直接使用相同方法。

手动写入校验名称、值、域名、路径和过期格式；name/value/domain/path文本总计最多4096字节，max_age正值最大2147483647秒。存储沿用HTTP客户端Cookie jar，不实现浏览器的 SameSite 请求上下文、分区 Cookie 或浏览器式配额淘汰，也不承诺两个语言 jar 的每项宽容解析和前缀校验完全一致；HttpOnly不限制调用方读取，当前也不提供完整requests兼容接口。

## 原生边界

核心 Response 提供 HeaderValues、Trailer、Protocol 与 FinalURL；旧 Headers 单值映射继续保留，包括历史 Set-Cookie 拼接形式。C ABI 仍为版本 1，`fastls_info` 增加 `header_values`、`trailer_values`、`protocol` 与 `final_url`，不改变原有 headers 字段。

新增fastls_session_cookies(handle,data,length)，输入是最多16384字节的UTF-8 JSON命令，返回仍为JSON信封并由fastls_free释放。支持action=get/set/clear/configure；get/set要求HTTP(S)绝对URL且不含凭据，set使用cookie对象，configure要求enabled布尔值。旧C客户端可继续使用原接口；新版Python包需同时更新动态库。

## 验收

tests/test_cookies.py包含13项真实动态库测试，覆盖H1/H2多值头、发送范围、服务端覆盖删除、Max-Age及Expires、重定向、手动头优先、会话/profile边界、禁用、32路异步更新、流式首块前可用的Cookie及非法输入。SDK 检查公共后缀拒绝、并发更新与 Clear；新增 test_sdk_sessions.py 验证原生上下文没有 Cookie 存储，且跳转失败不会泄漏请求。

六组原生CI分别执行源码和wheel套件，并继续运行原有基础、连接池、网络异常、64MiB下载和Linux300秒持续测试；JSON证据随artifact保存到build/stability-cookies-*.json。
