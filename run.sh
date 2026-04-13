#!/bin/bash
# SmartBench 一键启动脚本
#
# 用法:
#   ./run.sh                    # 使用默认配置（多模型并行分析）
#   ./run.sh 500                # 指定目标 QPS
#   ./run.sh 500 1              # 目标QPS 分析轮次
#   ./run.sh --verbose          # 启用详细输出
#   ./run.sh 500 --verbose      # 组合使用

set -e

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 解析参数
EXTRA_ARGS=""
TARGET_QPS="400"
ANALYSIS_ROUNDS="1"

for arg in "$@"; do
    case $arg in
        --verbose|-v)
            EXTRA_ARGS="$EXTRA_ARGS --verbose"
            ;;
        --*)
            # 其他以 -- 开头的参数
            EXTRA_ARGS="$EXTRA_ARGS $arg"
            ;;
        *)
            # 数字参数
            if [[ "$arg" =~ ^[0-9]+$ ]]; then
                if [ "$TARGET_QPS" = "400" ]; then
                    TARGET_QPS="$arg"
                else
                    ANALYSIS_ROUNDS="$arg"
                fi
            else
                EXTRA_ARGS="$EXTRA_ARGS $arg"
            fi
            ;;
    esac
done

echo "=========================================="
echo "  SmartBench v0.3 - Raft KV 专用版"
echo "=========================================="
echo "  目标 QPS:   $TARGET_QPS"
echo "  分析轮次:   $ANALYSIS_ROUNDS"
echo "  模型:       全部启用（多模型交叉验证）"
echo "  额外参数:   ${EXTRA_ARGS:-无}"
echo "=========================================="
echo ""

# 执行
python3 -m smartbench.cli run --target-qps $TARGET_QPS --analysis-rounds $ANALYSIS_ROUNDS $EXTRA_ARGS
