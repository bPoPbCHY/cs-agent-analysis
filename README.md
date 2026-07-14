# 🎧 客服分析 Agent — AI 驱动的客服消息结构化分析

> 一个面向 PM 的 AI Agent Demo：输入客户消息 → 结构化分析（意图/情绪/紧急度/回复建议/路由动作）→ 流式展示。支持多轮对话记忆和多模型切换。

**线上试不了**（本地 Ollama 跑的），但 30 秒演示视频能说明一切：[📹 演示视频](#)（待上传）

---

## 🤔 这个项目解决了什么问题？

客服系统里每天进来成千上万条消息，人工看完再分类、再分配、再回复——慢，而且标准不一。

这个 Demo 做的事情：

```
"我刚收到的衣服有质量问题，我要退款！"
            │
            ▼
    ┌─────────────────────────────┐
    │  🎧 客服分析 Agent           │
    │                             │
    │  意图：退款 (refund)          │
    │  情绪：愤怒 (angry)           │
    │  紧急度：█████ 5/5           │
    │  动作：转人工 (escalate_human) │
    │  回复："非常抱歉给您带来不好   │
    │   的体验！我已为您优先转接…"    │
    └─────────────────────────────┘
            │
            ▼
    自动路由到人工客服（带上下文）
```

**一句话**：把非结构化的客户消息变成结构化的分析结果 + 可执行的动作，减少人工判断成本。

---

## 🧱 产品设计决策（PM 视角）

这部分才是这个项目跟"一个 Flask Demo"的区别——**每个设计选择背后都有 PM 的判断**。

### 1. 为什么用 Pydantic `output_type` 而不是 Prompt 里写"请返回 JSON"？

| 方案 | 做法 | 问题 |
|------|------|------|
| Prompt 要求 JSON | system prompt 写"返回 JSON 格式" | LLM 可能返回纯文本、漏字段、加废话 |
| JSON Mode | API 参数强制 JSON | 只是保证格式是 JSON，不保证字段对 |
| **Pydantic `output_type`** ✅ | 定义 `CustomerAnalysis` 类，框架自动校验 | 类型安全、字段必填、自动重试 |

**产品判断**：客服场景需要 **100% 的结构可靠性**。如果"紧急度"字段偶尔丢失，下游路由就挂了。Pydantic 的 `output_type` 在框架层面保证了这件事——retry 3 次，不行就报错，不会出现"半成品 JSON"。

### 2. 为什么 `conversation_store` 里要同时存 `message_history` 和 `rounds`？

```python
conversation_store = {
    "a1b2c3d4": {
        "message_history": [...],  # Pydantic AI 原生格式，给 Agent 下一轮用
        "rounds": [...]            # 简化格式，给前端展示用
    }
}
```

这是有意为之的**关注点分离**：

- **`message_history`**：Pydantic AI 的 `result.all_messages()` 返回值，包含 system/user/assistant/tool 四个角色的完整消息。这个结构 Agent 下一轮能直接消费，不需要我做任何转换。
- **`rounds`**：前端只需要 `{user_msg, analysis}` 这个简单结构来渲染时间线。不需要知道 tool role 是什么、system prompt 长什么样。

**如果不分开会怎样？** 要么让前端去解析 Pydantic AI 的内部消息格式（耦合），要么每次从 `message_history` 重新提取前端需要的数据（浪费）。分开之后两边各取所需，互不干扰。

### 3. 为什么做流式（SSE）而不是一次性返回？

**产品直觉**：Agent 分析需要 2-5 秒，白屏等 5 秒 vs 逐步看到"意图识别 → 情绪判断 → 紧急度评估 → 生成回复"，体验完全不同。

SSE（Server-Sent Events）在这里不是炫技——它让用户**感知到 Agent 在工作**，而不是怀疑"是不是卡了"。这在客服场景尤其重要：操作员需要知道系统每一步的判断依据，而不是一个黑盒结果。

### 4. 为什么支持模型切换（Ollama ↔ Anthropic）？

这是一个 **PM 自己验证成本-效果的实验工具**：

- **Ollama (qwen3:8b)**：本地免费跑，结构化输出效果够用，适合小团队 POC
- **Anthropic (Claude Haiku 4.5)**：云端、付费、更快更准，适合生产环境

切换按钮让非技术人员也能直观对比两个模型的效果差异——这在跟业务方沟通"为什么需要用更好的模型"时非常有用。

---

## 🏗️ 技术架构

```
┌──────────────┐     SSE 流式      ┌──────────────┐     Structured Output    ┌──────────┐
│  index.html  │ ◄──────────────► │  Flask 后端   │ ◄─────────────────────► │  LLM     │
│  (前端 UI)    │   /analyze-stream │  (app.py)    │   Pydantic AI Agent      │  Ollama  │
│              │                  │              │                          │  Claude  │
│  - 时间线展示 │                  │  - 会话管理   │  CustomerAnalysis schema │          │
│  - 模型切换  │                  │  - 路由分发   │  intent/sentiment/...    │          │
│  - 流式渲染  │                  │  - 记忆存储   │                          │          │
└──────────────┘                  └──────────────┘                          └──────────┘
```

- **Pydantic AI**：结构化输出框架，用 `output_type=CustomerAnalysis` 保证 Agent 一定返回合法结构
- **Flask + SSE**：轻量后端 + 流式推送，不需要 WebSocket
- **会话管理**：内存 dict + `localStorage`，刷新页面不丢会话
- **多轮记忆**：`result.all_messages()` 保持 Pydantic AI 原生格式，下一轮直接传入

---

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 确保 Ollama 在运行（默认用本地 Ollama）
ollama pull qwen3:8b

# 3. （可选）配置 Anthropic API Key，解锁模型切换
export ANTHROPIC_API_KEY=sk-ant-...

# 4. 启动
python app.py

# 5. 打开浏览器 → http://localhost:5002
```

---

## 📂 项目结构

```
cs-agent-analysis/
├── app.py                 # Flask 后端 + Agent 定义 + SSE 流式
├── templates/
│   └── index.html         # 前端 UI（暗色主题, 纯原生 JS, 零依赖）
├── requirements.txt       # pydantic-ai + pydantic
└── .gitignore             # 防 .env 泄露
```

**故意不用的东西**：前端没引入任何框架（React/Vue），就是一个 HTML 文件。因为这个 Demo 的重点是 Agent 的设计逻辑，不是前端工程化。

---

## 💡 后续可以做的事（产品迭代方向）

- **RAG 接入**：把公司退款政策/FAQ 塞进 Agent 的 system prompt，让回复更准确
- **多 Agent 协作**：一个 Agent 分析意图 → 路由给不同专业 Agent（退款专员/投诉专员/咨询专员）
- **人机协作**：priority ≥ 4 自动转人工，priority ≤ 2 自动回复，priority = 3 推荐回复但人工确认
- **评测体系**：怎么量化这个 Agent 好不好？准确率？人工采纳率？平均处理时间？
- **持久化**：当前会话存内存，重启就没了 → 接 SQLite/Redis

---

*Built by 曹宏宇 — AI Agent PM 求职中。这个 Demo 是我"能写代码但不是工程师"的证明：技术足够搭原型验证想法，但真正的价值在产品判断。*
