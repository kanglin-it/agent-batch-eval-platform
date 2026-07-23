# Agent 批量评测平台 (agent-batch-eval-platform)

面向法研团队的批量评测平台：从历史用例库选取真实用户提问 → 用新 Agent 重跑 →
与历史基线对比评测 → 产出质量指标（胜率、耗时、幻觉数）。

## 目录结构

| 目录 | 技术栈 | 说明 |
|------|--------|------|
| [`backend/`](./backend) | FastAPI + SQLAlchemy(async) | API + 评测流水线；复用 Django 运营后台用户体系登录 |
| [`frontend/`](./frontend) | Vue 3 + Vite + Element Plus | 用例管理页 / 任务管理页 |

## 登录：复用 Django 运营后台账号

- 后端**只读**映射 Django 的用户表，用 `passlib` 按 Django 的哈希算法
  （默认 `pbkdf2_sha256`）校验密码——Django 哈希自带算法/迭代/盐，**无需 Django
  运行时、也无需 `SECRET_KEY`**。
- 校验通过后由本平台签发自己的 JWT，前端用 axios 拦截器统一携带；不复用 Django session。
- 改密/建号仍走 Django 后台，避免双写。

详见各子目录的 README。

## 本期范围

- ✅ 用例管理页（筛选 / 列表，选中上限 500 / 创建评测任务弹窗）
- ✅ 任务管理页（状态机 + 进度 + 胜率/耗时/幻觉数 + 重试 + 下载）
- ✅ 后台两阶段流水线骨架（Agent 执行 → 对比评测）
- ⏳ 标准 Skill 管理页 —— 本期先不做（P1）

## 待确认（影响落地）

1. FastAPI 与 Django 是否同库；Django 用户表名 / 是否自定义 User 模型。
2. 胜率 / 幻觉 / 耗时 的计算口径（由 Coze 评测工作流产出，还是平台侧加工）。
3. SaaS 用户明细、合同审查"持方页"信息、"旧版文件解析结果" 三处数据来源。
4. Agent / Coze 调用的并发、超时、限流与重试约束。
