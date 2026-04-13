#!/bin/bash
# SmartBench 一键启动脚本
#
# 用法:
#   ./run.sh                    # 使用默认配置（多模型并行分析）
#   ./run.sh 500                # 指定目标 QPS
#   ./run.sh 500 1              # 目标QPS 分析轮次

set -e

# 默认值
TARGET_QPS=${1:-400}
ANALYSIS_ROUNDS=${2:-1}

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  SmartBench v0.3 - Raft KV 专用版"
echo "=========================================="
echo "  目标 QPS:   $TARGET_QPS"
echo "  分析轮次:   $ANALYSIS_ROUNDS"
echo "  模型:       全部启用（多模型交叉验证）"
echo "=========================================="
echo ""

# 执行
python3 -m smartbench.cli run --target-qps $TARGET_QPS --analysis-rounds $ANALYSIS_ROUNDS
