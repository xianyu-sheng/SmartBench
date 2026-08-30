# Case 6: Blocking Channel Send Goroutine Leak

## 源信息
- **模式**: 通用并发缺陷模式
- **参考**: 
  - [Why goroutine leaks](https://stackoverflow.com/questions/29892950/why-goroutine-leaks)
  - [goleak-example](https://github.com/0xvbetsun/goleak-example)
- **Bug 类型**: Goroutine 泄漏
- **严重性**: High

## Bug 描述

生产者 goroutine 尝试向无缓冲或已满的 channel 发送数据，但消费者已经退出或停止读取，导致生产者 goroutine 永久阻塞。

### 根本原因
1. Worker goroutine 向 channel 发送结果
2. 主 goroutine 提前退出（超时/错误/取消）
3. Channel 没有接收者，发送操作永久阻塞
4. Worker goroutine 泄漏

### 常见场景
- HTTP 请求处理带超时
- 并行任务执行，"first wins" 模式
- 数据处理 pipeline 中断

## 影响
- **Goroutine 泄漏**: 每个未完成的操作一个泄漏
- **内存泄漏**: Goroutine 持有的资源无法释放
- **累积效应**: 长期运行服务中累积成千上万个泄漏

## 修复方案

### before.go (有 bug)
```go
func ProcessWithTimeout(data string) (string, error) {
    resultChan := make(chan string)  // 无缓冲 channel
    
    go func() {
        result := processData(data)
        resultChan <- result  // 可能永久阻塞
    }()
    
    select {
    case result := <-resultChan:
        return result, nil
    case <-time.After(1 * time.Second):
        return "", errors.New("timeout")
        // BUG: goroutine 仍在等待发送，永久泄漏
    }
}
```

### after.go (已修复)
```go
// 方案 1: 带缓冲的 channel
func ProcessWithTimeout(data string) (string, error) {
    resultChan := make(chan string, 1)  // 缓冲大小 1
    
    go func() {
        result := processData(data)
        resultChan <- result  // 不会阻塞，即使没人读取
    }()
    
    select {
    case result := <-resultChan:
        return result, nil
    case <-time.After(1 * time.Second):
        return "", errors.New("timeout")
    }
}

// 方案 2: 使用 context 取消
func ProcessWithTimeout(ctx context.Context, data string) (string, error) {
    resultChan := make(chan string)
    
    go func() {
        result := processData(data)
        select {
        case resultChan <- result:
        case <-ctx.Done():
            return  // Context 取消时退出
        }
    }()
    
    select {
    case result := <-resultChan:
        return result, nil
    case <-ctx.Done():
        return "", ctx.Err()
    }
}
```

## SmartBench 应检测的模式

1. **无缓冲 channel** 的发送操作在 goroutine 中
2. **主函数**可能在 goroutine 完成前退出
3. **Select with timeout** 但 goroutine 不感知超时
4. **没有 context** 传递给长期运行的 goroutine

## 相关模式
- "First wins" 并发模式的陷阱
- Fan-out 模式中的部分失败
- 请求处理超时但后台工作继续
