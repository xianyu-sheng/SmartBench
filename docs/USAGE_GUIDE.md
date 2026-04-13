# SmartBench 智能诊断功能使用指南

**版本**: v0.4
**更新日期**: 2026-04-13

---

## 目录

1. [快速入门](#快速入门)
2. [环境准备](#环境准备)
3. [命令详解](#命令详解)
4. [诊断场景案例](#诊断场景案例)
5. [诊断类型说明](#诊断类型说明)
6. [输出报告解读](#输出报告解读)
7. [常见问题](#常见问题)
8. [最佳实践](#最佳实践)

---

## 快速入门

### 3 分钟快速体验

```bash
# 1. 进入项目目录
cd /home/xianyu-sheng/SmartBench

# 2. 查看帮助
python3 -m smartbench.cli --help

# 3. 健康检查（检查诊断工具是否就绪）
python3 -m smartbench.cli health-check

# 4. 性能分析（生成火焰图）
python3 -m smartbench.cli diagnose --performance --duration 10

# 5. 完整压测+分析
python3 -m smartbench.cli run --target-qps 400
```

### 一图看懂 SmartBench

```
                    SmartBench 能做什么？
                    ┌─────────────────────────────────────────────┐
                    │                                              │
                    │   🏃 压测运行中？  →  性能分析与优化建议     │
                    │         ↓                                    │
                    │   💥 程序崩溃了？  →  智能诊断 + GDB 分析     │
                    │         ↓                                    │
                    │   🐢 运行很慢？   →  火焰图 + 热点分析       │
                    │         ↓                                    │
                    │   ❓ 不知道啥问题？ →  自动检测 + 全面诊断     │
                    │                                              │
                    └─────────────────────────────────────────────┘
```

---

## 环境准备

### 必需环境

| 环境 | 版本要求 | 检查命令 |
|------|----------|----------|
| Python | 3.10+ | `python3 --version` |
| Linux/macOS | 任意 | `uname -a` |

### 推荐安装的工具

SmartBench 的诊断功能依赖以下工具。安装越多，诊断能力越强。

```bash
# ===== GDB (崩溃分析必需) =====
sudo apt update
sudo apt install gdb

# 验证安装
gdb --version

# ===== perf (性能分析必需) =====
# Ubuntu/Debian
sudo apt install linux-tools-common linux-tools-generic

# CentOS/RHEL
sudo yum install perf

# 验证安装
perf --version

# ===== Valgrind (内存检测必需) =====
sudo apt install valgrind

# 验证安装
valgrind --version

# ===== FlameGraph (火焰图必需) =====
git clone https://github.com/brendangregg/FlameGraph.git ~/FlameGraph

# 验证安装
ls ~/FlameGraph/*.pl

# ===== pstack (线程分析) =====
sudo apt install pstack

# ===== 其他常用工具 =====
sudo apt install strace lsof net-tools iotop sysstat
```

### 一键安装所有工具

```bash
# 复制以下命令，一次性安装所有推荐工具
sudo apt update && sudo apt install -y \
    gdb \
    valgrind \
    linux-tools-common \
    linux-tools-generic \
    pstack \
    strace \
    lsof \
    net-tools \
    iotop \
    sysstat

# 安装 FlameGraph
git clone https://github.com/brendangregg/FlameGraph.git ~/FlameGraph

echo "✅ 所有工具安装完成！"
```

### 检查工具是否就绪

```bash
# 使用 SmartBench 健康检查
python3 -m smartbench.cli health-check
```

**期望输出**:
```
╭────────────────────────╮
│ 🏥 SmartBench 健康检查 │
╰────────────────────────╯

检查项
┏━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ 项目       ┃ 状态 ┃ 详情               ┃
┡━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ 二进制文件 │  ✅  │ 已找到              │
│ GDB        │  ✅  │ 8.0.1             │
│ perf       │  ✅  │ 可用               │
│ Valgrind   │  ✅  │ 3.15.0            │
│ FlameGraph │  ✅  │ 已安装             │
│ 可用内存   │  ✅  │ 8.0Gi             │
└────────────┴──────┴────────────────────┘

✅ 健康检查通过 (6/6)
```

---

## 命令详解

### 1. `diagnose` - 智能诊断（最常用）

这是最核心的诊断命令，用于自动检测和分析各种问题。

#### 基本语法

```bash
python3 -m smartbench.cli diagnose [OPTIONS]
```

#### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--symptoms` | 字符串 | None | 问题症状描述 |
| `--error-logs` | 文件路径 | None | 错误日志文件路径 |
| `--core-dump` | 文件路径 | None | core dump 文件路径 |
| `--performance` | 开关 | False | 执行性能分析 |
| `--duration` | 整数 | 30 | 性能采样时长（秒） |
| `--output` | 文件路径 | None | 输出报告文件路径 |
| `--project-path` | 目录路径 | 自动 | 项目路径 |

#### 使用场景

**场景 1: 程序崩溃了**

```bash
# 方法 1: 描述症状
python3 -m smartbench.cli diagnose --symptoms "程序段错误崩溃"

# 方法 2: 指定错误日志
python3 -m smartbench.cli diagnose --error-logs ./error.log

# 方法 3: 分析 core dump
python3 -m smartbench.cli diagnose --core-dump ./core.12345
```

**场景 2: 程序运行不起来**

```bash
python3 -m smartbench.cli diagnose --symptoms "程序启动失败"

# 附加错误日志
python3 -m smartbench.cli diagnose \
    --symptoms "程序启动失败" \
    --error-logs /path/to/startup.log
```

**场景 3: 程序运行很慢**

```bash
# 性能分析 + 生成火焰图
python3 -m smartbench.cli diagnose --performance --duration 60

# 保存报告
python3 -m smartbench.cli diagnose \
    --performance \
    --duration 60 \
    --output report.txt
```

**场景 4: 不确定是什么问题**

```bash
# 自动检测所有问题
python3 -m smartbench.cli diagnose --symptoms "程序不正常"
```

#### 完整示例

```bash
# 一个完整的诊断流程
cd /home/xianyu-sheng/SmartBench

# 1. 先健康检查
python3 -m smartbench.cli health-check

# 2. 执行诊断
python3 -m smartbench.cli diagnose \
    --symptoms "压测时 QPS 不达标" \
    --error-logs ./build/error.log \
    --output ./diagnosis_report.txt

# 3. 查看报告
cat ./diagnosis_report.txt
```

---

### 2. `health-check` - 健康检查

快速检查系统状态和诊断工具是否就绪。

#### 基本语法

```bash
python3 -m smartbench.cli health-check [OPTIONS]
```

#### 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `--project-path` | 目录 | 项目路径（默认自动检测） |
| `--verbose` | 开关 | 显示详细信息 |

#### 使用示例

```bash
# 基础检查
python3 -m smartbench.cli health-check

# 详细输出
python3 -m smartbench.cli health-check --verbose
```

**输出解读**:

| 检查项 | ✅ | ❌ | 说明 |
|--------|----|----|------|
| 二进制文件 | 找到了可执行文件 | 未找到 | 检查项目是否编译 |
| GDB | 已安装 | 未安装 | 崩溃分析必需 |
| perf | 可用 | 不可用 | 性能分析必需 |
| Valgrind | 已安装 | 未安装 | 内存检测必需 |
| FlameGraph | 已安装 | 未安装 | 火焰图必需 |
| 可用内存 | 足够 | 不足 | 系统资源检查 |

---

### 3. `run` - 完整压测+分析

执行完整的压测流程，并生成优化建议。

#### 基本语法

```bash
python3 -m smartbench.cli run [OPTIONS]
```

#### 常用参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--target-qps` | 400 | 目标 QPS |
| `--rounds` | 1 | 压测轮次 |
| `--analysis-rounds` | 2 | 分析轮次 |
| `--models` | 全部 | 使用的模型 |
| `--verbose` | False | 详细输出 |

#### 使用示例

```bash
# 标准压测
python3 -m smartbench.cli run --target-qps 400

# 多轮压测 + 详细输出
python3 -m smartbench.cli run \
    --target-qps 500 \
    --rounds 3 \
    --verbose

# 指定模型
python3 -m smartbench.cli run \
    --target-qps 400 \
    --models deepseek,glm-4.7
```

---

### 4. `regression` - 性能回归分析

查看历史性能数据和趋势。

```bash
# 查看 7 天趋势
python3 -m smartbench.cli regression

# 查看特定指标
python3 -m smartbench.cli regression --metric qps

# 查看 30 天趋势
python3 -m smartbench.cli regression --days 30
```

---

## 诊断场景案例

### 案例 1: 程序崩溃（段错误）

**问题**: 程序运行时突然崩溃，提示 "Segmentation fault"

**诊断步骤**:

```bash
# 1. 检查是否生成了 core dump
ls -la ./build/core*

# 2. 执行诊断
python3 -m smartbench.cli diagnose \
    --core-dump ./build/core.12345 \
    --output crash_report.txt

# 3. 查看报告
cat crash_report.txt
```

**期望输出**:

```
🔍 SmartBench 智能诊断报告
============================================================

⏰ 诊断时间: 2026-04-13T21:00:00
📋 问题类型: crash
⚠️  严重程度: CRITICAL

🔍 根本原因:
  - 程序收到 SIGSEGV (段错误) 信号
  - 崩溃位置: KvServer::Get() at KvServer.cpp:85
  - 可能原因: 空指针解引用

💡 修复建议:
  1. 空指针检查
     说明: 在访问指针前检查是否为 nullptr

  2. 使用 GDB 详细分析
     命令: gdb ./build/kvserver -c ./build/core.12345
     说明: 进入 GDB 后执行 'bt full' 查看完整堆栈

============================================================
```

**下一步操作**:

```bash
# 使用 GDB 详细分析
gdb ./build/kvserver -c ./build/core.12345 -batch -ex "bt full" -ex "quit"
```

---

### 案例 2: 内存泄漏

**问题**: 程序运行一段时间后内存占用持续增长，最终 OOM

**诊断步骤**:

```bash
# 1. 先检查系统内存
free -h

# 2. 使用 Valgrind 检测
python3 -m smartbench.cli diagnose \
    --symptoms "内存持续增长，疑似内存泄漏" \
    --output memory_report.txt
```

**Valgrind 手动检测**:

```bash
# 运行 Valgrind（需要重新编译带调试信息的程序）
valgrind --leak-check=full \
         --show-leak-kinds=all \
         --track-origins=yes \
         --verbose \
         ./build/kvserver &

# 或者检测已运行的进程
# 找到进程 PID
ps aux | grep kvserver

# 附加到进程
valgrind --attach-to-pid=<PID> --tool=memcheck
```

---

### 案例 3: 性能瓶颈

**问题**: 程序运行正常，但 QPS 远低于预期

**诊断步骤**:

```bash
# 1. 快速性能分析（30秒）
python3 -m smartbench.cli diagnose \
    --performance \
    --duration 30 \
    --output perf_report.txt

# 2. 查看报告
cat perf_report.txt
```

**期望输出**:

```
🔍 SmartBench 智能诊断报告
============================================================

⏰ 诊断时间: 2026-04-13T21:00:00
📋 问题类型: performance
⚠️  严重程度: MEDIUM

🔥 CPU 热点分析:

  35.2%  Raft::sendAppendEntries
  22.1%  KvServer::Get
  15.8%  Log::serialize
   8.3%  Network::send

💡 优化建议:

  1. 优化 sendAppendEntries
     问题: 该函数占用 35% CPU 时间
     建议: 实现批量发送，减少 RPC 调用次数

  2. 优化日志序列化
     问题: 序列化效率低
     建议: 使用二进制序列化替代文本格式

📁 生成文件:
  - data/flamegraphs/cpu_flamegraph_20260413.svg

提示: 使用浏览器打开 SVG 文件查看火焰图
============================================================
```

**查看火焰图**:

```bash
# 使用浏览器打开
firefox ./data/flamegraphs/cpu_flamegraph_*.svg

# 或者复制到桌面查看
cp ./data/flamegraphs/cpu_flamegraph_*.svg ~/Desktop/
```

---

### 案例 4: 程序启动失败

**问题**: 程序无法启动，提示各种错误

**诊断步骤**:

```bash
# 1. 诊断启动问题
python3 -m smartbench.cli diagnose \
    --symptoms "程序无法启动" \
    --output startup_report.txt

# 2. 查看报告
cat startup_report.txt
```

**可能的问题和解决方案**:

| 错误信息 | 可能原因 | 解决方案 |
|----------|----------|----------|
| `cannot find -lxxx` | 缺少动态库 | `sudo apt install libxxx-dev` |
| `Permission denied` | 无执行权限 | `chmod +x ./binary` |
| `No such file or directory` | 文件不存在 | 检查路径 |
| `Address already in use` | 端口被占用 | `lsof -i :8000` 然后 kill |

**手动诊断**:

```bash
# 检查依赖
ldd ./build/kvserver

# 典型输出:
#     linux-vdso.so.1 (0x00007fff...)
#     libpthread.so.0 => /lib/x86_64/.../libpthread.so.0
#     libstdc++.so.6 => /lib/x86_64/.../libstdc++.so.6
#     libm.so.6 => /lib/x86_64/.../libm.so.6
#     libgcc_s.so.1 => /lib/x86_64/.../libgcc_s.so.1
#     libc.so.6 => /lib/x86_64/.../libc.so.6
#     libzookeeper.so.2 => not found  ← 问题在这里！

# 安装缺失的库
sudo apt install libzookeeper-mt-dev
```

---

### 案例 5: 死锁/无响应

**问题**: 程序突然卡住，无响应

**诊断步骤**:

```bash
# 1. 诊断死锁问题
python3 -m smartbench.cli diagnose \
    --symptoms "程序无响应，卡死" \
    --output deadlock_report.txt

# 2. 手动获取线程堆栈
pstack $(pgrep -f kvserver) > thread_stack.txt

# 3. 查看所有线程状态
ps -T -p $(pgrep -f kvserver)
```

**死锁特征识别**:

```
线程堆栈中多个线程都处于:
- pthread_mutex_lock
- pthread_cond_wait
- futex_wait

且堆栈顶部都是类似的等待函数
```

---

## 诊断类型说明

### 问题类型自动检测

SmartBench 会根据你提供的症状自动检测问题类型：

| 关键词 | 自动识别类型 | 说明 |
|--------|--------------|------|
| `崩溃` `段错误` `segfault` `SIGSEGV` | crash | 程序崩溃 |
| `死锁` `卡死` `hang` `无响应` | deadlock | 线程卡住 |
| `内存` `leak` `泄漏` `oom` | memory_leak | 内存问题 |
| `缺页` `page fault` | page_fault | 虚拟内存问题 |
| `慢` `性能` `瓶颈` `qps低` | performance | 性能问题 |
| `启动失败` `cannot start` | startup_failure | 启动问题 |

### 不同类型使用不同工具

```
crash ──────────────→ GDB, dmesg
     │
deadlock ───────────→ pstack, /proc
     │
memory_leak ────────→ Valgrind, pmap
     │
page_fault ─────────→ vmstat, dmesg
     │
performance ────────→ perf, 火焰图
     │
startup_failure ───→ ldd, file, strace
```

---

## 输出报告解读

### 诊断报告结构

```
🔍 SmartBench 智能诊断报告
============================================================

⏰ 诊断时间:        ← 诊断执行时间
📋 问题类型:        ← 自动识别的问题类型
⚠️  严重程度:        ← CRITICAL / HIGH / MEDIUM / LOW
📊 置信度:          ← 诊断的可信程度 (0-100%)

────────────────────────────────────────────────────────────

📋 发现的症状:      ← 观察到的问题表现
  - 症状1
  - 症状2

🔍 根本原因:        ← 分析出的深层原因
  - 原因1
  - 原因2

💡 修复建议:        ← 具体的解决方案
  1. 标题
     命令: xxx      ← 可执行的命令
     说明: xxx      ← 详细解释

🔧 使用的诊断命令:  ← 诊断过程执行的命令
  - command 1
  - command 2

📁 生成的文件:      ← 产生的分析文件
  - file1
  - file2

============================================================
```

### 严重程度说明

| 严重程度 | 含义 | 建议 |
|----------|------|------|
| CRITICAL | 致命错误，程序无法运行 | 立即修复 |
| HIGH | 严重问题，影响功能 | 尽快修复 |
| MEDIUM | 中等问题，性能下降 | 计划修复 |
| LOW | 轻微问题，可忽略 | 可选修复 |

---

## 常见问题

### Q1: 诊断报告说 "未安装 XXX"

**问题**: health-check 显示工具未安装

**解决**:
```bash
# 安装对应工具
sudo apt install <工具名>

# GDB
sudo apt install gdb

# Valgrind
sudo apt install valgrind

# perf
sudo apt install linux-tools-common linux-tools-generic
```

### Q2: core dump 没有生成

**问题**: 程序崩溃了但没有 core 文件

**解决**:

```bash
# 1. 检查 core 文件大小限制
ulimit -c

# 2. 如果是 0，设置为无限
ulimit -c unlimited

# 3. 永久设置
echo "* soft core unlimited" | sudo tee -a /etc/security/limits.conf

# 4. 设置 core 文件路径和格式
echo "/tmp/core.%e.%p.%t" | sudo tee /proc/sys/kernel/core_pattern
```

### Q3: perf 权限不足

**问题**: `perf: permission denied`

**解决**:

```bash
# 临时方案（需要 root）
sudo sysctl -w kernel.perf_event_paranoid=-1

# 永久方案
echo "kernel.perf_event_paranoid=-1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### Q4: 火焰图是空的

**问题**: perf 采样成功但火焰图没有内容

**解决**:

```bash
# 1. 确保程序编译时带了调试信息
#    CMakeLists.txt 中添加: set(CMAKE_BUILD_TYPE Debug)

# 2. 或者使用符号化
perf record -F 99 -a -g -- ./program
perf script | ~/FlameGraph/stackcollapse-perf.pl | ~/FlameGraph/flamegraph.pl > result.svg
```

### Q5: 诊断结果不准确

**问题**: 诊断报告没有找到真正的问题

**解决**:

```bash
# 1. 提供更多症状信息
python3 -m smartbench.cli diagnose \
    --symptoms "详细描述问题..." \
    --error-logs ./error.log

# 2. 使用 --verbose 查看详细信息
python3 -m smartbench.cli diagnose \
    --performance --verbose

# 3. 结合多个诊断命令
python3 -m smartbench.cli diagnose --symptoms "问题" --error-logs ./log
python3 -m smartbench.cli diagnose --performance
```

### Q6: 如何分析多轮压测结果

```bash
# 查看历史数据
python3 -m smartbench.cli regression --days 30

# 导出数据
python3 -m smartbench.cli regression --metric qps > qps_trend.txt

# 生成趋势图（需要 gnuplot）
gnuplot -e "set terminal png; set output 'trend.png'; plot 'qps_trend.txt'"
```

---

## 最佳实践

### 1. 日常使用流程

```bash
#!/bin/bash
# smartbench_daily.sh - 每日健康检查脚本

echo "=== SmartBench 每日检查 ==="
echo ""

# 1. 健康检查
echo "1. 执行健康检查..."
python3 -m smartbench.cli health-check
echo ""

# 2. 性能分析（可选，每周一次）
if [ "$1" = "--perf" ]; then
    echo "2. 执行性能分析..."
    python3 -m smartbench.cli diagnose --performance --duration 60
fi

# 3. 查看火焰图
if [ -f "./data/flamegraphs/cpu_flamegraph_*.svg" ]; then
    echo "3. 最新火焰图:"
    ls -lt ./data/flamegraphs/*.svg | head -1
fi

echo ""
echo "=== 检查完成 ==="
```

### 2. 崩溃复现流程

```bash
#!/bin/bash
# reproduce_crash.sh - 崩溃复现脚本

echo "=== 崩溃复现诊断 ==="

# 1. 确保 core dump 开启
ulimit -c unlimited

# 2. 运行程序
./build/kvserver &

# 3. 等待崩溃
wait

# 4. 检查是否生成 core
if [ -f "./core" ]; then
    echo "检测到 core dump，开始分析..."
    python3 -m smartbench.cli diagnose \
        --core-dump ./core \
        --output crash_report.txt

    # 使用 GDB 详细分析
    gdb ./build/kvserver -c ./core -batch \
        -ex "thread apply all bt" \
        -ex "info registers" \
        -ex "quit" > gdb_report.txt

    echo "报告已生成:"
    echo "  - crash_report.txt"
    echo "  - gdb_report.txt"
fi
```

### 3. 性能优化流程

```bash
#!/bin/bash
# optimize_perf.sh - 性能优化脚本

TARGET_QPS=400
ITERATIONS=3

echo "=== 性能优化流程 ==="

for i in $(seq 1 $ITERATIONS); do
    echo ""
    echo "=== 第 $i 轮测试 ==="

    # 1. 运行压测
    python3 -m smartbench.cli run --target-qps $TARGET_QPS

    # 2. 生成火焰图
    python3 -m smartbench.cli diagnose \
        --performance \
        --duration 30 \
        --output perf_report_$i.txt

    # 3. 等待用户确认优化
    echo "按回车继续下一轮测试..."
    read
done

# 4. 查看趋势
python3 -m smartbench.cli regression --days 1
```

### 4. 自动化集成

```python
# integrate.py - 集成到 CI/CD

import subprocess
import sys

def run_diagnosis():
    """在 CI/CD 中集成诊断"""

    # 1. 健康检查
    result = subprocess.run(
        ["python3", "-m", "smartbench.cli", "health-check"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("❌ 健康检查失败")
        print(result.stdout)
        return False

    # 2. 性能测试
    result = subprocess.run(
        ["python3", "-m", "smartbench.cli", "run", "--target-qps", "400"],
        capture_output=True,
        text=True
    )

    # 3. 检查结果
    if "QPS" in result.stdout:
        # 提取 QPS
        import re
        match = re.search(r'当前 QPS.*?(\d+\.?\d*)', result.stdout)
        if match:
            qps = float(match.group(1))
            print(f"当前 QPS: {qps}")

            if qps < 350:
                print("⚠️  QPS 低于预期，执行诊断...")
                subprocess.run([
                    "python3", "-m", "smartbench.cli",
                    "diagnose", "--performance", "--output", "diagnosis.txt"
                ])
                return False

    return True

if __name__ == "__main__":
    success = run_diagnosis()
    sys.exit(0 if success else 1)
```

---

## 参考资料

### 官方文档

- **GDB 文档**: https://www.gnu.org/software/gdb/documentation/
- **FlameGraph**: https://github.com/brendangregg/FlameGraph
- **perf 文档**: https://perf.wiki.kernel.org/

### 推荐阅读

- **Brendan Gregg 的 Linux 性能**: http://www.brendangregg.com/linuxperf.html
- **火焰图理论**: http://queue.acm.org/detail.cfm?id=2927301

---

## 反馈与支持

遇到问题？

1. 查看本文档的 [常见问题](#常见问题) 部分
2. 使用 `health-check` 检查环境
3. 使用 `--verbose` 获取详细信息
4. 提交 Issue 到 GitHub

---

**祝您使用愉快！SmartBench 让诊断变得简单。**
