# Case 4: etcd/Kubernetes Context Cancellation Goroutine Leak

## 源信息
- **项目**: Kubernetes (etcd client)
- **PR**: [#25331](https://github.com/kubernetes/kubernetes/pull/25331) by @hongchaodeng
- **Bug 类型**: Goroutine 泄漏
- **严重性**: High (生产环境内存泄漏)

## Bug 描述

在 Kubernetes 使用 etcd 客户端监听配置变更时，当 context 被取消，但内部的 watcher goroutine 没有正确退出。

### 根本原因
1. Watcher goroutine 在循环中向 channel 发送事件
2. 当调用方取消 context 并停止读取 channel 时
3. Goroutine 在 `resultChan <- resp` 处永久阻塞
4. Context 取消不会自动终止 goroutine

### 触发条件
- 创建 etcd watcher
- 消费方取消 context
- Watcher goroutine 仍在尝试发送数据

## 影响
- **内存泄漏**: 每个泄漏的 goroutine 占用内存
- **Goroutine 积累**: 长期运行导致成千上万的泄漏 goroutine
- **性能下降**: Go scheduler 开销增加

## 修复方案

### before.go (有 bug)
```go
go func() {
    for {
        resp := WatchResponse{...}
        w.resultChan <- resp  // 永久阻塞
        time.Sleep(100 * time.Millisecond)
    }
}()
```

### after.go (已修复)
```go
go func() {
    for {
        select {
        case <-ctx.Done():
            close(w.resultChan)
            return  // 正确退出
        default:
            resp := WatchResponse{...}
            select {
            case w.resultChan <- resp:
            case <-ctx.Done():
                close(w.resultChan)
                return  // 发送时也检查取消
            }
        }
    }
}()
```

## SmartBench 应检测的模式

1. **Goroutine 启动**但没有终止机制
2. **Channel 发送**不检查 context.Done()
3. **循环**中没有退出条件
4. **Context 传入**但未在 goroutine 中使用

## 相关案例
- etcd clientv3 的类似问题
- 其他长期运行的 watcher/subscriber 模式
