"""客服分析 Agent — Pydantic AI Structured Output Web Demo（多轮记忆版）。

跟单轮版的区别：每个会话保持对话历史，Agent 能记住上一轮说了什么。
就像淘宝 AI 客服——"我刚才说的那个订单，退了吧"→ Agent 知道"那个订单"指什么。

跑法：
    pip install -r requirements.txt
    python app.py
    浏览器打开 http://localhost:5002
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env（在项目根目录）
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

# Anthropic 可选：有 API key 才 import
try:
    from pydantic_ai.models.anthropic import AnthropicModel
    HAS_ANTHROPIC = bool(os.environ.get("ANTHROPIC_API_KEY"))
except Exception:
    HAS_ANTHROPIC = False

app = Flask(__name__)

# ── 配置 ──────────────────────────────────────────────
OLLAMA_BASE = os.environ.get("OLLAMA_API_BASE", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("MODEL", "qwen3:8b")
ANTHROPIC_MODEL = "claude-haiku-4-5"
PORT = 5002

# ── 会话记忆存储 ──────────────────────────────────────
# 结构：{session_id: {"message_history": [...], "rounds": [...]}}
# - message_history: Pydantic AI 格式，传给下一轮 agent.run_sync()
# - rounds: 简化格式 [{user_msg, analysis: {intent, sentiment, ...}}]，给前端展示
conversation_store: dict[str, dict] = {}


# ── Schema：Agent 必须返回这个结构 ────────────────────

class CustomerAnalysis(BaseModel):
    """客服消息的结构化分析结果。"""
    intent: str = Field(
        description="用户意图：退款(refund)、咨询(inquiry)、投诉(complaint)、购买(purchase)、其他(other)"
    )
    sentiment: str = Field(
        description="用户情绪：positive(正面)、neutral(中性)、negative(负面)、angry(愤怒)"
    )
    priority: int = Field(
        ge=1, le=5,
        description="紧急度 1-5。5=非常紧急，1=不紧急"
    )
    reply: str = Field(
        description="建议的客服回复文案，语气匹配用户情绪。如有对话历史，请参考之前的内容保持连贯"
    )
    action: str = Field(
        description="建议的系统动作：create_ticket(创建工单)、escalate_human(转人工)、"
                    "auto_reply(自动回复)、refund(触发退款流程)"
    )


# ── Agent ─────────────────────────────────────────────

def build_analyzer(use_anthropic: bool = False) -> Agent:
    """创建客服分析 Agent。use_anthropic=True 时用 Claude。"""
    if use_anthropic and HAS_ANTHROPIC:
        model = AnthropicModel(ANTHROPIC_MODEL)
        model_label = f"Anthropic {ANTHROPIC_MODEL}"
    else:
        model = OpenAIModel(
            OLLAMA_MODEL,
            provider=OpenAIProvider(base_url=OLLAMA_BASE, api_key="ollama"),
        )
        model_label = f"Ollama {OLLAMA_MODEL}"

    analyzer = Agent(
        model=model,
        output_type=CustomerAnalysis,
        retries=3,
        system_prompt=(
            "你是一个专业的客服分析系统。分析用户消息，判断意图、情绪、紧急度，"
            "并给出建议的回复和系统动作。"
            "如果用户很愤怒(angry)，priority 至少设为 4，action 建议 escalate_human。"
            "如果用户要求退款，action 设为 refund。"
            "reply 要匹配用户的情绪和问题，用中文回复。"
            "如果有对话历史，reply 要参考之前的上下文保持连贯——"
            "比如用户说'那个订单'，你要结合历史知道是哪个订单。"
        ),
    )
    analyzer._model_label = model_label  # type: ignore[attr-defined]
    return analyzer


def analyze_message(
    user_message: str,
    use_anthropic: bool = False,
    chat_history: list | None = None,
) -> tuple[dict, list]:
    """分析一条客户消息。chat_history 为之前的对话历史（Pydantic AI 格式）。
    返回 (analysis_dict, new_history)。"""
    bot = build_analyzer(use_anthropic)
    result = bot.run_sync(user_message, message_history=chat_history)

    analysis_dict = {
        "intent": result.output.intent,
        "sentiment": result.output.sentiment,
        "priority": result.output.priority,
        "reply": result.output.reply,
        "action": result.output.action,
        "model": getattr(bot, "_model_label", "unknown"),
    }

    # all_messages() 返回包含本轮在内的全部消息，下一轮直接传进去
    new_history = result.all_messages()
    return analysis_dict, new_history


# ── Flask 路由 ────────────────────────────────────────

@app.route("/")
def index():
    """前端页面。"""
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    """一次性分析（无流式），支持多轮记忆。"""
    data = request.get_json()
    user_message = data.get("message", "").strip()
    use_anthropic = data.get("use_anthropic", False)
    session_id = data.get("session_id", "").strip()

    if not user_message:
        return jsonify({"error": "消息不能为空"}), 400

    # 获取或创建会话
    if not session_id or session_id not in conversation_store:
        session_id = str(uuid.uuid4())[:8] #  生成新 ID，比如 "a1b2c3d4"
        conversation_store[session_id]  = { #conversation_store["a1b2c3d4"]
          "message_history": [], 
          "rounds": []
        } 
    # 现在 conversation_store 变成：
    # {"a1b2c3d4": {"message_history": [], "rounds": []}}

    session = conversation_store[session_id] #session = conversation_store["a1b2c3d4"] 
    history = session["message_history"] if session["message_history"] else None

    try:
        analysis_dict, new_history = analyze_message(
            user_message, use_anthropic, chat_history=history,
        )

        # 更新会话记忆
        session["message_history"] = new_history
        session["rounds"].append({
            "user_msg": user_message,
            "analysis": analysis_dict,
        })

        return jsonify({
            "ok": True,
            "session_id": session_id,
            "round": len(session["rounds"]),
            **analysis_dict,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/analyze-stream", methods=["POST"])
def analyze_stream():
    """流式分析（SSE），支持多轮记忆。"""
    data = request.get_json()
    user_message = data.get("message", "").strip()
    use_anthropic = data.get("use_anthropic", False)
    session_id = data.get("session_id", "").strip()

    if not user_message:
        return jsonify({"error": "消息不能为空"}), 400

    # 获取或创建会话
    if not session_id or session_id not in conversation_store:
        session_id = str(uuid.uuid4())[:8]
        conversation_store[session_id] = {"message_history": [], "rounds": []}

    session = conversation_store[session_id]
    history = session["message_history"] if session["message_history"] else None
    round_num = len(session["rounds"]) + 1

    def generate():
        yield f"event: phase\ndata: {json.dumps({'phase': 'thinking', 'label': f'第 {round_num} 轮 · Agent 正在分析…'})}\n\n"

        try:
            analysis_dict, new_history = analyze_message(
                user_message, use_anthropic, chat_history=history,
            )

            # 更新会话记忆
            session["message_history"] = new_history
            session["rounds"].append({
                "user_msg": user_message,
                "analysis": analysis_dict,
            })

            fields = [
                ("intent", analysis_dict["intent"], "意图识别"),
                ("sentiment", analysis_dict["sentiment"], "情绪判断"),
                ("priority", analysis_dict["priority"], "紧急度评估"),
                ("action", analysis_dict["action"], "系统动作建议"),
                ("reply", analysis_dict["reply"], "生成回复"),
                ("model", analysis_dict["model"], "模型信息"),
            ]

            for key, value, label in fields:
                time.sleep(0.25)
                yield f"event: field\ndata: {json.dumps({'key': key, 'value': value, 'label': label})}\n\n"

            yield f"event: done\ndata: {json.dumps({'session_id': session_id, 'round': round_num})}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/history", methods=["POST"])
def get_history():
    """获取某个会话的历史记录（给前端展示对话时间线）。"""
    data = request.get_json()
    session_id = data.get("session_id", "").strip()

    if not session_id or session_id not in conversation_store:
        return jsonify({"rounds": [], "session_id": ""})

    session = conversation_store[session_id]
    return jsonify({
        "session_id": session_id,
        "rounds": session["rounds"],
        "round_count": len(session["rounds"]),
    })


@app.route("/reset", methods=["POST"])
def reset():
    """重置会话记忆，开始新对话。"""
    data = request.get_json()
    session_id = data.get("session_id", "").strip()

    if session_id and session_id in conversation_store:
        del conversation_store[session_id]

    new_id = str(uuid.uuid4())[:8]
    return jsonify({"ok": True, "session_id": new_id})


if __name__ == "__main__":
    print(f"\n🎧 客服分析 Agent Demo 启动中...（多轮记忆版）")
    print(f"   本地 Ollama：{OLLAMA_MODEL}")
    print(f"   Anthropic  ：{'可用' if HAS_ANTHROPIC else '未配置'}")
    print(f"   打开浏览器 → http://localhost:{PORT}")
    print(f"   试试多轮：输入'我要退款'→ 再输入'刚才说的订单号是88234'\n")
    app.run(debug=True, port=PORT)
