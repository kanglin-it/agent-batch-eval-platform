# Agent Batch Eval Platform

一个用于**批量评测 AI Agent** 的平台。它帮助你在统一的框架下，针对大量测试用例并发运行一个或多个 Agent，收集运行结果，并按可配置的评分标准自动打分与汇总报告。

> ⚠️ 本仓库目前处于早期阶段。本文档描述了项目的目标与规划结构，具体实现会随代码一并更新。

## 目录

- [背景](#背景)
- [核心功能](#核心功能)
- [架构概览](#架构概览)
- [快速开始](#快速开始)
- [目录结构](#目录结构)
- [使用示例](#使用示例)
- [配置](#配置)
- [开发](#开发)
- [路线图](#路线图)
- [贡献](#贡献)
- [许可证](#许可证)

## 背景

在开发 AI Agent（智能体）时，单条对话的效果好坏往往不足以说明问题。要判断一次 prompt 调整、模型切换或工具改动是否真正带来提升，需要在**一整批**代表性任务上进行可复现、可对比的评测。

Agent Batch Eval Platform 的目标就是把这一过程标准化：

- 一次定义评测集，反复运行；
- 并发执行以缩短评测时间；
- 用一致的评分标准输出可对比的指标；
- 保留历史结果，方便版本间回归对比。

## 核心功能

- **批量执行**：从数据集读取任务，并发地对目标 Agent 逐条运行。
- **多 Agent / 多模型对比**：在同一批任务上并排比较不同配置。
- **可插拔评分器**：支持精确匹配、规则匹配、以及基于 LLM 的评审（LLM-as-a-judge）等多种打分方式。
- **结果汇总**：自动统计通过率、平均分等指标，并生成报告。
- **可复现**：固定数据集与配置，保证评测结果可重复、可追溯。

## 架构概览

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   数据集      │ ──> │   执行引擎    │ ──> │   评分器      │
│  (test set)  │     │  (runner)    │     │  (scorer)    │
└──────────────┘     └──────────────┘     └──────────────┘
                            │                     │
                            v                     v
                     ┌──────────────────────────────────┐
                     │           结果 & 报告              │
                     │        (results / reports)        │
                     └──────────────────────────────────┘
```

- **数据集（Dataset）**：一组评测任务，每条包含输入以及可选的期望输出 / 参考答案。
- **执行引擎（Runner）**：负责调度、并发控制与重试，把任务分发给被测 Agent。
- **评分器（Scorer）**：对每条运行结果打分。
- **报告（Report）**：汇总打分结果，输出指标与对比视图。

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/kanglin-it/agent-batch-eval-platform.git
cd agent-batch-eval-platform

# 安装依赖（示例，具体以实际实现为准）
# npm install   或   pip install -r requirements.txt

# 运行一次批量评测（示例命令）
# ./run-eval --dataset ./datasets/example.jsonl --agent my-agent --scorer llm-judge
```

## 目录结构

```
agent-batch-eval-platform/
├── datasets/      # 评测数据集
├── agents/        # 被测 Agent 的定义与适配器
├── scorers/       # 评分器实现
├── runner/        # 批量执行引擎
├── reports/       # 生成的评测报告
└── README.md
```

> 目录结构为规划参考，会随实现调整。

## 使用示例

一条典型的评测任务（JSONL 格式，每行一个对象）：

```json
{"id": "case-001", "input": "把这段文字翻译成英文：你好世界", "expected": "Hello, world"}
```

评测流程：

1. 准备数据集文件。
2. 配置目标 Agent 与评分器。
3. 运行批量评测。
4. 查看生成的报告，对比不同版本的指标。

## 配置

评测通过配置文件（如 `config.yaml`）来描述，示例：

```yaml
dataset: ./datasets/example.jsonl
concurrency: 8          # 并发数
agent:
  name: my-agent
  model: claude-opus-4-8
scorer:
  type: llm-judge       # exact | rule | llm-judge
output:
  report: ./reports/latest.html
```

## 开发

```bash
# 运行测试（示例）
# npm test   或   pytest

# 代码检查（示例）
# npm run lint
```

## 路线图

- [ ] 定义数据集格式规范
- [ ] 实现批量执行引擎与并发控制
- [ ] 提供多种内置评分器
- [ ] 生成可视化对比报告
- [ ] 支持评测结果的历史存储与回归对比

## 贡献

欢迎提交 Issue 与 Pull Request。提交前请确保：

1. 变更有清晰的描述；
2. 相关测试通过；
3. 遵循项目的代码风格。

## 许可证

待定（TBD）。
