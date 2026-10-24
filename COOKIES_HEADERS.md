# 多值响应头与会话Cookie

Session和AsyncSession现在默认使用原生内存Cookie存储。它由同一Session内的所有profile/CA连接池共享，不同Session相互隔离；关闭Session会清空并禁用其存储。Cookie不会保存到磁盘。

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

Set-Cookie、Link等字段不会按逗号或旧的/,/分隔符拆分；每个解析后的字段值分别保留。映射不承诺原始字段名大小写、跨字段的线上顺序或原始报文字节；trailer与重定向历史不在此接口中。这里只展示最终响应头，重定向中间响应的Cookie仍会进入存储。

与此前开发版本的dict相比，Python的头访问现在大小写不敏感且只读；Set-Cookie单值访问返回第一项，获取全部Cookie应使用get_list。

## Cookie规则与操作

- 自动接收每一项Set-Cookie，并按RFC 6265的域名、host-only、路径、Secure、Max-Age和Expires规则选择后续请求的Cookie。Go 1.26标准库按浏览器安全源语义将localhost和回环IP视为安全源，因此Secure Cookie可在这些主机的HTTP请求中发送；其他HTTP主机仍不会发送。使用内置Public Suffix List拒绝跨公共后缀的Cookie。
- 重定向响应在下一跳发送前更新存储；禁止跟随重定向时仍接收该响应的Cookie。跨主机重定向只会使用目标主机有权接收的存储内容。
- 显式Cookie请求头（包括空字符串）控制整个请求及重定向链的手动Cookie，不再自动追加存储中的Cookie；响应仍更新存储。手动头在跨主机重定向中的转发遵守核心客户端的敏感头规则。
- cookies.get_list(url)返回该URL可发送的{name,value}列表，保留不同路径下的同名Cookie；get_dict(url)及get(name,url=...)优先选择匹配路径更长的一项。这是发送视图，不是包含完整属性的存储导出。
- cookies.set(name,value,url=...,path=...,domain=...,secure=...,http_only=...,max_age=...,expires=...)手动写入。省略path使用URL目录；省略domain为host-only。expires使用RFC3339字符串，例如2100-01-01T00:00:00Z。max_age正值覆盖expires，0或负值删除，省略则由expires或会话寿命决定。
- cookies.delete必须给出与目标Cookie一致的URL、path和domain范围；clear删除整个Session的Cookie，但保留连接池。close_idle_connections释放空闲连接，不清空Cookie。
- Session(cookies=False)或AsyncSession(cookies=False)禁用自动接收和发送，仍可使用显式Cookie头；手动set会报禁用错误。Cookie访问是短同步操作，AsyncSession可直接使用相同方法。

手动写入校验名称、值、域名、路径和过期格式；name/value/domain/path文本总计最多4096字节，max_age正值最大2147483647秒。存储沿用HTTP客户端Cookie jar，不提供浏览器的SameSite上下文、分区Cookie、前缀策略或浏览器式配额淘汰；HttpOnly不限制调用方读取，当前也不提供完整requests兼容接口。

## 原生边界

核心Response新增HeaderValues；旧Headers单值映射继续保留，包括历史Set-Cookie拼接形式。C ABI仍为版本1，fastls_info新增header_values字段（字段名到字符串数组），不改变原有headers字段。

新增fastls_session_cookies(handle,data,length)，输入是最多16384字节的UTF-8 JSON命令，返回仍为JSON信封并由fastls_free释放。支持action=get/set/clear/configure；get/set要求HTTP(S)绝对URL且不含凭据，set使用cookie对象，configure要求enabled布尔值。旧C客户端可继续使用原接口；新版Python包需同时更新动态库。

## 验收

tests/test_cookies.py包含13项真实动态库测试，覆盖H1/H2多值头、发送范围、服务端覆盖删除、Max-Age及Expires、重定向、手动头优先、会话/profile边界、禁用、32路异步更新、流式首块前可用的Cookie及非法输入。核心还检查公共后缀拒绝与并发Clear，并纳入race；Cookie实现已迁移到标准库cookiejar。

六组原生CI分别执行源码和wheel套件，并继续运行原有基础、连接池、网络异常、64MiB下载和Linux300秒持续测试；JSON证据随artifact保存到build/stability-cookies-*.json。
