# 第二阶段：现有能力的稳定性验收

本阶段验证首版SDK，不代表连接池、Cookie jar、流式上传或WebSocket已实现。持续运行是有明确时间范围的回归检查，不是生产环境长期无泄漏证明。

## 必需覆盖

- 真实动态库到独立Go标准库靶场，HTTPS HTTP/1.1和HTTP/2均校验证书、确认实际协商协议。
- 靶场启动时生成临时CA与叶证书；Session指定CA后可访问，缺省/错误CA必须失败，系统信任不受影响。
- 加密流式首块、读取中关闭、关闭一个响应不破坏另一个、响应头前取消、截断响应报错。
- 64MiB HTTP2流式下载校验完整SHA256，暂停读取时服务器不能完成传输，桥接已读未消费数据不超过3×64KiB。
- 普通缓存响应超过32MiB后报错并关闭；512KiB二进制上传边界；分片UTF-8 SSE字节不丢失。
- 256个未关闭句柄限制及释放后的恢复；128路异步混合成功/取消；关闭Session终止32个挂起请求；12线程共享Session执行96次请求。
- 源码SDK及wheel分别连续运行30秒，32路并发，记录完成请求数、Go堆、goroutine、进程RSS、文件描述符/句柄。
- Linux x64当前Go组合额外连续运行300秒；不将30秒/5分钟扩大描述为小时或天级稳定性结论。

## 回收与阈值

每个用例结束等待最多10秒，必须达到open_requests=0、workers=0。
持续测试先预热96个TLS请求；每批32请求结束后采样。结束goroutine最多比预热基线多16，Go堆in-use峰值增加不得超过96MiB。
进程RSS峰值增长不得超过128MiB，结束增长不超过96MiB；描述符/Windows句柄增长不超过64。
这些是包含Go运行时缓存与平台差异的回归阈值，不承诺绝对内存占用，也不能检测小于阈值的泄漏。
具体样本与各用例通过/失败状态写入build/stability-*.json，随Actions artifact保存。

## 测试发现与修复

新增HTTP2大响应测试复现了约4MiB处连接被关闭：协议声明的INITIAL_WINDOW_SIZE与本地固定4MiB计数不一致。
fhttp现按实际发送的SETTINGS记录流窗口；连接窗口从协议默认65535加实际WINDOW_UPDATE计算，两者独立；读取时按各自窗口返还额度，小窗口也能继续传输。
已有显式空/零设置的线上帧保持不变；AutoWriteFrames返回的接收连接额度修正为真实连接额度。
桥接另修复了已收到EOF但尚有未消费字节时取消被忽略的问题；已经向调用方返回的EOF仍保持稳定。

## 运行

```shell
go build -o build/ffi-fixture ./cmd/ffi-fixture
python tests/test_stability.py
python scripts/test_wheel.py --stability
```

Windows fixture可执行文件名为ffi-fixture.exe。FASTLS_STABILITY_SECONDS可在本地设为1..300秒用于诊断；GitHub正常回归使用30秒，Linux额外300秒不缩短。
私有CA是请求级TLSRootCAs配置的映射，始终VerifyTLS=true；没有测试专用的跳过校验路径或生产ABI强制GC接口。
