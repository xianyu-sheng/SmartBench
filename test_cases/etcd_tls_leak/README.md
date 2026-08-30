# Case 5: etcd TLS Listener Unbounded Goroutine Creation

## 源信息
- **项目**: etcd
- **CVE**: CVE-2026-73500
- **Advisory**: [GHSA-6vch-q96h-7gc3](https://github.com/advisories/GHSA-6vch-q96h-7gc3)
- **Bug 类型**: DoS - 无限 Goroutine 创建
- **严重性**: Critical

## Bug 描述

etcd 的 TLS 监听器在接受连接时没有设置握手超时，攻击者可以打开大量 TCP 连接但不发送 ClientHello，导致无限制创建阻塞的 goroutine，最终耗尽系统资源。

### 根本原因
1. `acceptLoop` 为每个连接创建新 goroutine
2. `tls.Conn.Handshake()` 调用时没有 deadline
3. 攻击者不发送 TLS ClientHello，握手永久阻塞
4. 每个阻塞的 goroutine 消耗文件描述符和内存

### 攻击场景
```bash
# 攻击者打开大量连接但不发送数据
for i in {1..10000}; do
  nc etcd-server 2379 &  # 连接但不发送任何数据
done
```

### 影响范围
- etcd 3.4.x - 3.4.34
- etcd 3.5.x - 3.5.17
- etcd 3.6.x - 3.6.0-alpha.0

## 影响
- **Goroutine 泄漏**: 每个连接一个永久阻塞的 goroutine
- **文件描述符耗尽**: 最终导致 `accept4: too many open files`
- **内存耗尽**: 每个 goroutine ~10KB + 连接结构
- **DoS**: etcd 节点崩溃，集群不可用

## 修复方案

### before.go (有 bug)
```go
func (l *tlsListener) acceptLoop() {
    for {
        conn, err := l.listener.Accept()
        if err != nil {
            return
        }
        
        // BUG: 没有设置握手超时
        go func() {
            // 这里会永久阻塞如果客户端不发送 ClientHello
            err := conn.Handshake()
            if err != nil {
                conn.Close()
                return
            }
            l.connChan <- conn
        }()
    }
}
```

### after.go (已修复)
```go
func (l *tlsListener) acceptLoop() {
    for {
        conn, err := l.listener.Accept()
        if err != nil {
            return
        }
        
        go func() {
            // FIX: 设置握手超时 (10秒)
            conn.SetDeadline(time.Now().Add(10 * time.Second))
            
            err := conn.Handshake()
            if err != nil {
                conn.Close()
                return
            }
            
            // 握手成功后移除 deadline
            conn.SetDeadline(time.Time{})
            l.connChan <- conn
        }()
    }
}
```

## SmartBench 应检测的模式

1. **Accept 循环**为每个连接创建 goroutine
2. **网络操作**（Handshake/Read/Write）没有超时
3. **无限制 goroutine 创建**没有并发限制
4. **阻塞操作**在 goroutine 中没有退出机制

## 相关漏洞
- Gogs SSH 握手 DoS ([CVE-2026-52814](https://github.com/advisories/GHSA-xp79-5mx3-jx52))
- TLS Slow Loris 攻击模式
- golang.org/x/crypto/ssh 类似问题
