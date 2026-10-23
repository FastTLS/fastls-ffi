# 第三阶段：网络异常与协议边界

本阶段延续现有SDK的测试，不新增连接池、Cookie jar、代理或流式上传。所有请求只访问回环地址，靶场由独立Go标准库服务和Python原始TCP服务组成，不依赖公网服务的可用性或修改系统网络设置。

## 必需覆盖

- TCP已接受连接但不返回HTTP响应，或TLS不返回ServerHello：请求超时后断开连接。
- asyncio在TLS握手停滞期间仍可调度；取消请求后连接关闭，同一Session继续完成正常请求。
- 六种异常HTTP/1.1输入：非法状态码、非法chunk长度、截断chunk、截断Content-Length、冲突Content-Length、缺少冒号的响应头。必须报错，不能作为成功EOF返回。
- 正向原始TCP对照：逐字节发送合法chunked响应，包含chunk扩展、二进制内容和trailer；正文必须逐字节一致。SDK当前不暴露trailer。
- 靶场在已发送正文后主动终止：HTTP/1.1连接关闭、HTTP/2 RST_STREAM。每种协议连续八次失败后均立即验证正常请求。
- HTTP/1.1与HTTP/2正文停滞：等待下一块时超时、消费者暂停后超时；asyncio等待下一块时取消。
- 301/302/303将POST改为GET并清除正文；307/308保留POST及二进制正文。
- 禁止重定向时返回307及Location；循环重定向在限制内报错。
- 从127.0.0.1重定向至localhost：同一临时CA校验成功，Authorization和Cookie被移除，普通自定义头保留。初始请求确认两个敏感头确实已发送。
- HEAD、204、304无正文；503作为正常HTTP响应返回状态码，不作为传输错误。

共11个测试方法，内部按协议、故障类型和状态码展开子场景。每个方法结束后最多等待10秒，原生open_requests及workers必须归零。超时场景使用短请求截止时间；不对网络错误的本地化文本作固定字符串断言。

## 复现及证据

```shell
go build -o build/ffi-fixture ./cmd/ffi-fixture
python tests/test_network.py
python scripts/test_wheel.py --network
```

Windows靶场文件名使用ffi-fixture.exe；先构建动态库和当前wheel。测试共享第二阶段的TLS靶场生命周期及JSON结果记录，源码与wheel分别输出build/stability-network-*.json。

每次GitHub Actions的六组原生组合都会运行源码和wheel两套网络测试，失败会直接使该作业失败。与原有7项基础测试、11项稳定性测试及Linux额外300秒持续测试一同执行，日志和JSON报告保留在同一artifact中。

这些测试验证可控故障下的行为，不代表公网指纹等价性、网络丢包模拟、代理兼容性或生产环境长期稳定性已通过。
