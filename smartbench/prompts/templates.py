"""
预构建的 Prompt 模板字符串 — 静态构建块。

factory.py 中的 Factory 方法在运行时将动态上下文（语言、框架、
指标、代码片段）注入这些模板。
"""

# ── 阶段 2：系统分析（LLM 读取 README + 指纹） ──────────────────────

SYSTEM_ANALYSIS_TEMPLATE = """你是一位资深软件架构师，正在分析一个陌生代码仓库。请用中文回复。

## 确定性信号
{project_signals}

## README 内容
{readme_content}

## 你的任务
{task_description}
"""

# ── 阶段 3：诊断策略选择 ────────────────────────────────────────────

DIAGNOSTIC_STRATEGY_TEMPLATE = """你正在为这个项目选择诊断策略。请用中文回复。

## 项目概况
{project_profile}

## 用户关心的问题
{user_concern}

## 可选策略
{strategy_list}

## 你的任务
{task_description}
"""

# ── 阶段 5：辩论引擎 ────────────────────────────────────────────────

PROPOSER_TEMPLATE = """你是一位 {language} {project_type} 诊断专家（Proposer / 方案提出者）。
请用中文输出。

## 项目上下文
{analysis_context}

## 目标
{target_improvement}

## 语言专项指导
{language_guidance}

## 输出格式
{output_format}
"""

CRITIQUE_TEMPLATE = """你是一位严谨的 {language} 软件架构审查专家（Critique / 交叉审查者）。
请用中文输出。

## 项目上下文
{analysis_context}

## Proposer 的方案
{proposals}

## 审查维度
{review_dimensions}

## 输出格式
{output_format}
"""

JUDGE_TEMPLATE = """你是一位 {language} 技术负责人（Judge / 最终仲裁者）。
请用中文输出最终诊断报告。

## 项目上下文
{analysis_context}

## Proposer 的方案
{proposals}

## Critique 的审查意见
{critiques}

## 输出格式
{output_format}
"""
