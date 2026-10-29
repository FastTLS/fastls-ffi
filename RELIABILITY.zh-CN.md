# 接口、重试与离线配置

本批保持 ABI v1。新增业务重试默认关闭；已有 H3/策略限制和证书验证保持有效。
完整参数与责任边界见 [English contract](RELIABILITY.md)。

## Python 认证与 Cookie

```python
from fastls import Session, BasicAuth, BearerAuth, RetryPolicy

with Session(auth=BasicAuth("user", "password")) as session:
    response = session.get("https://example.com", auth=BearerAuth("token"),
                           request_cookies={"language": "zh"})
    session.cookies.update({"preference": "compact"}, url="https://example.com/")
```

`auth` 支持 BasicAuth、BearerAuth 或用户名/密码元组；Basic 使用 UTF-8。
显式 Authorization 头优先，逐请求 `auth=None` 关闭继承认证。
跨 origin 跳转后不会重新注入原凭据。认证默认值适用于普通 HTTP/SSE 入口；
WebSocket 与手动双工继续使用显式请求头。

`request_cookies` 只覆盖当前调用及其同 origin 跳转/重试，按初始主机和根路径匹配，
不会写入会话；跨 origin 后移除。显式 Cookie 头优先。服务端 Set-Cookie 仍按既有规则
更新会话。`session.cookies.update(mapping, url=...)` 会先验证整个映射再合并。
原有 Session 和顶层函数的 `cookies: bool` 开关不变；迁移逐请求 `cookies={...}`
时改为 `request_cookies={...}`。

## Python / Node 可选重试

```python
from fastls import Session, RetryPolicy

retry = RetryPolicy(max_retries=2, backoff=0.1, max_delay=2)
with Session(retry=retry, timeout=10) as session:
    response = session.get("https://example.com")
    # 每次尝试或 307/308 都重新创建正文；一次性迭代器不自动重放。
    response = session.put("https://example.com/upload",
                           body_factory=lambda: iter([b"first", b"second"]))
```

```typescript
import { Session, RetryPolicy } from '@fastls/fastls';

const retry = new RetryPolicy({ maxRetries: 2, backoffMs: 100, maxDelayMs: 2000 });
const session = new Session({ retry, timeoutMs: 10000 });
try {
  await session.get('https://example.com');
  await session.put('https://example.com/upload', {
    bodyFactory: () => [Buffer.from('first'), Buffer.from('second')],
  });
  await session.fetch('https://example.com', { retry });
} finally { await session.closeAsync(); }
```

Python 时长单位是秒，Node 是毫秒。默认额外尝试 2 次，仅 GET/HEAD/OPTIONS/PUT/DELETE；
默认状态为 429/502/503/504，另支持明确的网络错误及取得响应头前的 connect/headers 超时。
策略默认指数退避、完整抖动及有上限的 Retry-After；总超时覆盖全部尝试、等待和跳转。
`max_retries` / `maxRetries` 计 SDK 额外尝试次数，整个跳转链共用。底层已有协议恢复
不计入此数字，因此不承诺服务端只处理一次。

POST 等方法须在 methods 中列出并设置 `allow_non_idempotent=True` /
`allowNonIdempotent: true`。Python 请求 `retry=None`、Node 请求 `retry: null`
关闭会话继承的业务重试。认证/证书/策略拒绝、取消、解码、配置和未分类错误不重试。
旧原生库缺少结构化网络错误时保持保守，不依据错误字符串猜测。

bytes/字符串可重放，不受是否采用流式发送影响；可变字节源在请求完成前由调用者保持不变。
正文工厂不能与 data/body/json/form/files 混用，由调用者保证每次产生独立正文。
普通一次性文件、迭代器、multipart 不自动重放。已返回响应或读取正文失败不重新发送。
取消和会话关闭会中断退避；同步 Python 可通过其它线程关闭会话取消等待。
双工不支持本批重试和正文工厂；WS/SSE 自动恢复、Digest 保留后续。

## 离线配置导入、固定与回滚

```sh
python -m fastls profile export browser-spec.json browser-bundle.json --source local-capture
python -m fastls profile validate browser-bundle.json --sha256 <导出时打印的摘要>
python -m fastls profile import browser-bundle.json --sha256 <同一摘要> --store ./profiles
python -m fastls profile show --store ./profiles
python -m fastls profile rollback --store ./profiles
```

```python
from fastls import ProfileStore, Session
bundle = ProfileStore("profiles").load()
with Session(profile_spec=bundle.spec) as session:
    response = session.get("https://example.com")
```

```typescript
import { ProfileStore, Session } from '@fastls/fastls';
const bundle = new ProfileStore('profiles').load();
const session = new Session({ profileSpec: bundle.spec });
try { await session.get('https://example.com'); } finally { await session.closeAsync(); }
```

Python `ProfileBundle` 和 Node `ProfileBundle` 共用封装：schema_version、profile_id、
spec_json、sha256、source。SHA-256 固定精确 Spec UTF-8 字节，摘要只检查完整性，
不认证发布者。导入要求调用者提供摘要并由原生编译器验证 Spec，不会执行 DNS/拨号。
封装最多 512KiB、Spec 最多 256KiB、深度最多 64，便携数值须为安全整数；重复/未知封装
字段及后端不支持的 Spec 被拒绝。数据更新不能启用旧原生库不支持的新能力。

本地可信目录保存按摘要寻址的对象，以及原子替换的 current/previous 状态。失败导入不替换
当前状态；pin 可切换已有版本，rollback 返回上一次。调用者须串行化同一目录的写入。
工具不联网、不自动下载浏览器，不包含在线发现、采集、自动 PR 或定时更新。
