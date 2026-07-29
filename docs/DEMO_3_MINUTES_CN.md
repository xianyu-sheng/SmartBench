# SmartBench 2 分 39 秒无声实战演示

完整视频：[直接播放或下载 H.264 MP4](https://raw.githubusercontent.com/xianyu-sheng/SmartBench/main/docs/assets/smartbench-demo.mp4)。

这不是测试结果回放。视频使用 Ubuntu GNOME 原生录屏，从一个新终端开始，对 Requests 的完整公开历史仓库执行一次真实 SmartBench 分析。录制中没有旁白、音轨或可见 API Key。

## 时间线

| 时间 | 画面内容 |
| --- | --- |
| 0:00–0:05 | 干净 Ubuntu 桌面，打开新终端并输入 `smartbench` |
| 0:05–0:10 | 输入完整 Requests 项目路径，安全读取环境中的模型配置 |
| 0:10–0:20 | 零 LLM 项目指纹：Python、35 个文件、约 8,650 行、pip、Git 与 README |
| 0:20–0:35 | LLM 概括仓库用途，输入跨 `sessions.py` / `adapters.py` 的代理认证关注点 |
| 0:35–1:25 | 现场构建 815 个 RAG 代码块、991 个图节点、1,607 条边，并执行 correctness audit |
| 1:25–1:45 | Proposer 提出候选问题，Verifier 核验两个文件中的源码位置和引用 |
| 1:45–2:25 | Critique 检查 CONNECT、SOCKS、NO_PROXY 与测试边界，保留缺失证据为 unknown |
| 2:25–2:39 | Judge 和最终 findings：给出优先级、共识、修复约束、源码位置与图统计 |

## 演示目标

演示展示的是产品使用闭环，而不是准确率宣传：

```text
输入仓库
  → 项目理解
  → 代码图 / RAG EvidencePack
  → Proposer 候选
  → Verifier 源码核验
  → Critique 反证与 unknown
  → Judge / 最终 finding
```

目标仓库固定在 Requests 安全修复前的公开提交 `302225334678490ec66b3614a9dddb8a02c5f4fe`。这让分析对象可复现，但视频本身仍是现场运行：SmartBench 重新扫描仓库、重新建图，并重新调用多 Agent，而不是读取预制 JSON。

## 如何自行复现

准备任意本地仓库和至少一个支持的模型环境变量后运行：

```bash
smartbench
```

然后依次输入项目路径和关注点。视频中的关注点为：

```text
Analyze rebuild_proxies, proxy_headers, and HTTPS CONNECT across requests/sessions.py and requests/adapters.py
```

SmartBench 默认只读；它不会自动修改目标仓库、创建 Issue/PR 或联系上游项目。模型结论仍须结合 evidence score、unknown 边界和人工复核，不应把演示视为通用缺陷检测准确率证明。
