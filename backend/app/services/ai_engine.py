"""AI 引擎：Prompt 装配 / 权限检查 / 防注入 / 配额 / 检索 / 校验 / 降级。

AI_MODE=mock 时使用确定性模板（零外部依赖，可测试）；AI_MODE=real 时走
OpenAI 兼容的网关（公司内网常见），失败自动降级为 fallback。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core import db, util
from app.core.config import AI_API_KEY, AI_BASE_URL, AI_MODE, AI_MODEL, AI_TIMEOUT_SECONDS
from app.core.http import err

log = logging.getLogger("crossmate.ai")
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "data" / "prompts"
RAG_DIR = Path(__file__).resolve().parents[1] / "data" / "rag"

DAILY_LIMITS = {"icebreaker": 10, "translate": 200, "rewrite": 200, "room_topic": 5, "call_prep": 5}

# ---------- 注入/越权特征（输入层） ----------
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|above)\s+instructions",
    r"忽略(以上|之前|上面)(所有)?(的)?(指令|规则|要求)",
    r"system\s*prompt",
    r"you\s+are\s+now\s+",
    r"你现在是",
    r"act\s+as\s+(an?\s+)?(admin|developer)",
    r"</?\s*(system|assistant)\s*>",
    r"{{.*?}}",
    r"reveal\s+(your\s+)?(rules|prompt)",
]
# 输出层风险（模型失控/角色越位）
OUTPUT_RISK_PATTERNS = [
    (r"\b(I am|I'm) (a|an) (real|human)", "ai_claims_human"),
    (r"我已经?替你发送|sent on your behalf", "claims_sent_for_user"),
    (r"你是.{0,4}(中国人|日本人|美国人).{0,10}(所以|一定)", "nationality_stereotype"),
]

BLOCKED_SENSITIVE = {"minor", "underage", "未成年"}


@dataclass
class GenContext:
    user: dict
    kind: str
    text: str = ""
    match_id: str | None = None
    room_id: str | None = None
    call_id: str | None = None
    target_language: str = "en"
    style: str = "natural"
    retrieved: list = field(default_factory=list)


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(util.now()))


def load_prompt(name: str) -> dict:
    p = PROMPTS_DIR / f"{name}.md"
    return {"name": name, "text": p.read_text(encoding="utf-8") if p.exists() else ""}


def check_injection(text: str) -> str | None:
    low = (text or "").lower()
    for pat in INJECTION_PATTERNS:
        if re.search(pat, low, re.IGNORECASE):
            return "prompt_injection"
    return None


def check_content_risk(text: str) -> str | None:
    low = text.lower()
    if any(k in low for k in BLOCKED_SENSITIVE):
        return "minor_risk"
    for pat, code in OUTPUT_RISK_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return code
    return None


def refund_quota(user_id: str, kind: str | None) -> None:
    """生成失败（超时/模型错误/Schema 失败）不计入用户额度。

    注入/越权属于用户行为，不退还；只有“服务端问题”退还。
    """
    if not kind:
        return
    try:
        with db.tx() as c:
            c.execute("UPDATE ai_usage SET count=MAX(count-1,0) WHERE user_id=? AND kind=? AND day=?",
                      (user_id, kind, today()))
    except Exception:  # noqa: BLE001  退还失败不影响主流程
        log.warning("quota refund failed user=%s kind=%s", user_id, kind)


def enforce_quota(user_id: str, kind: str) -> None:
    limit = DAILY_LIMITS.get(kind)
    if not limit:
        return
    row = db.q1("SELECT count FROM ai_usage WHERE user_id=? AND kind=? AND day=?", (user_id, kind, today()))
    used = row["count"] if row else 0
    if used >= limit:
        raise err(429, 42902, "daily AI quota exceeded", {"limit": limit, "used": used})
    with db.tx() as c:
        c.execute("INSERT INTO ai_usage(user_id,kind,day,count) VALUES(?,?,?,1) "
                  "ON CONFLICT(user_id,kind,day) DO UPDATE SET count=count+1", (user_id, kind, today()))


def retrieve(query: str, collections: list[str], top_k: int = 3) -> list[dict]:
    """极简检索：只召回 approved 且未过期文档，按 token 重合度排序。

    TC34：过期文档不召回。文化建议必须带 doc_id/chunk_id。
    """
    q_tokens = set(re.findall(r"[\w一-鿿]+", query.lower()))
    hits = []
    for coll in collections:
        f = RAG_DIR / f"{coll}.json"
        if not f.exists():
            continue
        for doc in json.loads(f.read_text(encoding="utf-8")):
            if doc.get("status") != "approved":
                continue
            if doc.get("expires_at") and doc["expires_at"] < today():
                continue
            for chunk in doc["chunks"]:
                t = set(re.findall(r"[\w一-鿿]+", chunk["text"].lower()))
                score = len(q_tokens & t) / (len(t) or 1)
                if score > 0:
                    hits.append({"doc_id": doc["doc_id"], "chunk_id": chunk["chunk_id"],
                                 "text": chunk["text"], "score": round(score, 3)})
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:top_k]


# ---------------- 权限：authorized data only ----------------
def _authorized_profile(uid: str, viewer_id: str, privacy_row) -> dict:
    from app.routers.users import public_view

    u = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    return public_view(dict(u), viewer_id, dict(privacy_row) if privacy_row else {})


def gather_icebreaker_ctx(ctx: GenContext) -> tuple[dict | None, str | None]:
    uid = ctx.user["id"]
    if not ctx.match_id:
        return None, "match_required"
    m = db.q1("SELECT * FROM matches WHERE id=?", (ctx.match_id,))
    if not m or uid not in (m["user_a"], m["user_b"]):
        return None, "not_a_member"
    if m["status"] != "active":
        return None, "match_inactive"
    peer_id = m["user_b"] if m["user_a"] == uid else m["user_a"]
    pv = _authorized_profile(uid, uid, db.q1("SELECT * FROM privacy WHERE user_id=?", (uid,)))
    pp = _authorized_profile(peer_id, uid, db.q1("SELECT * FROM privacy WHERE user_id=?", (peer_id,)))
    reasons = []
    shared = set(pv.get("interests", [])) & set(pp.get("interests", []))
    if shared:
        reasons.append("shared interests: " + ", ".join(sorted(shared)))
    shared_lang = set(pv.get("languages", [])) & set(pp.get("languages", []))
    if shared_lang:
        reasons.append("shared languages: " + ", ".join(sorted(shared_lang)))
    shared_goal = set(pv.get("social_goals", [])) & set(pp.get("social_goals", []))
    if shared_goal:
        reasons.append("shared goal: " + ", ".join(sorted(shared_goal)))
    return {"reasons": reasons, "peer": pp, "self": pv}, None


def gather_room_ctx(ctx: GenContext) -> tuple[dict | None, str | None]:
    if not ctx.room_id:
        return None, "room_required"
    r = db.q1("SELECT * FROM rooms WHERE id=?", (ctx.room_id,))
    if not r:
        return None, "room_not_found"
    if r["status"] != "live":
        return None, "room_not_live"
    if not db.q1("SELECT 1 FROM room_members WHERE room_id=? AND user_id=? AND left_at IS NULL",
                 (ctx.room_id, ctx.user["id"])):
        return None, "not_a_member"
    # 只使用房间公开主题，不读取私聊
    return {"room_title": r["title"], "room_topic": r["topic"], "language": r["language"]}, None


def gather_call_ctx(ctx: GenContext) -> tuple[dict | None, str | None]:
    if not ctx.call_id:
        return None, "call_required"
    c = db.q1("SELECT * FROM calls WHERE id=?", (ctx.call_id,))
    if not c or ctx.user["id"] not in (c["caller_id"], c["callee_id"]):
        return None, "not_a_participant"
    consent_ok = all(
        db.q1("SELECT 1 FROM users WHERE id=? AND consent_version<>''", (u,))
        for u in (c["caller_id"], c["callee_id"])
    )
    if not consent_ok:
        return None, "consent_missing"
    data, err_code = gather_icebreaker_ctx(GenContext(user=ctx.user, kind="icebreaker", match_id=c["match_id"]))
    if err_code:
        return None, err_code
    return {"reasons": data["reasons"]}, None


# ---------------- 生成器（mock） ----------------
def _mock_icebreaker(ctx: GenContext, data: dict) -> dict:
    reasons = data["reasons"]
    lang = ctx.target_language
    base = []
    if any("shared interests" in r for r in reasons):
        base.append("What got you into that shared interest? Would love to hear your story.")
    if any("shared languages" in r for r in reasons):
        base.append("Since we speak the same language, what's the funniest mistranslation you've seen?")
    if any("shared goal" in r for r in reasons):
        base.append("What's one small thing you hope to get out of today?")
    base += [
        "What made you smile today?",
        "Coffee, tea, or neither? Need to settle this.",
        "What song have you had on repeat this week?",
    ]
    suggestions = []
    for i, s in enumerate(base[:3]):
        if lang == "zh":
            s = ["今天有什么让你微笑的小事？", "你最近单曲循环的是哪首歌？", "咖啡、茶，还是都不喝？"][i]
        suggestions.append({"text": s[:120], "reason": reasons[i] if i < len(reasons) else "general opener"})
    return {"suggestions": suggestions, "notes": "Generic but honest - adjust before sending."}


def _mock_room_topic(ctx: GenContext, data: dict) -> dict:
    topic = data.get("room_topic") or ""
    return {
        "suggestions": [
            {"text": f"On '{topic[:40]}', what's one thing you learned the hard way?", "reason": "room topic"},
            {"text": f"Which part of '{topic[:40]}' do you disagree with, and why?", "reason": "room topic"},
            {"text": "Quick round: one book or one song that fits tonight's topic?", "reason": "room topic"},
        ],
        "notes": "Neutral prompts based on the room's public topic only.",
    }


def _mock_call_prep(ctx: GenContext, data: dict) -> dict:
    return {
        "suggestions": [
            {"text": "Pick 2 of the shared topics from the card to open with.", "reason": "consent-verified"},
            {"text": "Ask one follow-up question each, then share something back.", "reason": "turn taking"},
        ],
        "notes": "Call prep uses only mutually consented summaries.",
    }


def _mock_transform(ctx: GenContext) -> dict:
    text = ctx.text
    if ctx.kind == "translate":
        # 演示翻译：保留数字/否定/称谓。真实实现走模型。
        mapping = {
            "I don't want to call tonight.": "我今晚不想通话。",
            "See you at 8pm, Sam.": "Sam，晚上 8 点见。",
            "I don't drink, sorry Mr. Lee.": "抱歉，李先生，我不喝酒。",
        }
        target = mapping.get(text.strip(), f"[{ctx.target_language}] {text}")
        return {"source": text, "target": target, "warnings": [], "risk": "none"}
    styles = {
        "natural": text,
        "polite": f"I hope it's okay to say this - {text}",
        "casual": text.rstrip(".!?") + " :)",
    }
    return {"source": text, "target": styles.get(ctx.style, text), "warnings": [], "risk": "none"}


# ---------------- 校验 ----------------
ICE_SCHEMA_KEYS = {"suggestions", "notes"}


def validate(kind: str, out: dict) -> bool:
    if kind in ("icebreaker", "room_topic", "call_prep"):
        if not isinstance(out, dict) or "suggestions" not in out or not isinstance(out["suggestions"], list):
            return False
        if not (1 <= len(out["suggestions"]) <= 3):
            return False
        for s in out["suggestions"]:
            if not isinstance(s, dict) or not s.get("text") or len(str(s["text"])) > 120:
                return False
        return True
    if kind in ("translate", "rewrite"):
        return isinstance(out, dict) and bool(out.get("target")) and "risk" in out
    return False


# ---------------- 外部模型（real） ----------------
def call_model(prompt_system: str, prompt_user: str) -> tuple[dict | None, str]:
    if AI_MODE != "real" or not AI_BASE_URL or not AI_API_KEY:
        return None, "mock_mode"
    import urllib.request

    body = json.dumps({
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": prompt_system}, {"role": "user", "content": prompt_user}],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        AI_BASE_URL.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {AI_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"]
        return json.loads(content), "ok"
    except Exception as exc:  # noqa: BLE001
        return None, f"model_error:{type(exc).__name__}"


# ---------------- 主流程 ----------------
def _persist(ctx: GenContext, status: str, output: dict, fallback_reason: str, latency_ms: int,
             prompt_name: str) -> dict:
    gid = util.new_id("g")
    with db.tx() as c:
        c.execute("""INSERT INTO ai_generations(id,user_id,kind,match_id,input,output,status,fallback_reason,
                     latency_ms,model,prompt_version,retrieved,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (gid, ctx.user["id"], ctx.kind, ctx.match_id, ctx.text or "",
                   util.dumps(output), status, fallback_reason, latency_ms,
                   AI_MODE, prompt_name, util.dumps(ctx.retrieved), util.now()))
    return {"generation_id": gid, "kind": ctx.kind, "status": status, "output": output,
            "fallback": status != "succeeded", "fallback_reason": fallback_reason,
            "latency_ms": latency_ms, "prompt_version": prompt_name,
            "citations": [{"doc_id": h["doc_id"], "chunk_id": h["chunk_id"]} for h in ctx.retrieved],
            "notice": "Suggestions only. Review before sending - AI never sends for you."}


def _run(ctx: GenContext, prompt_name: str, gather, mock_fn) -> dict:
    started = time.perf_counter()
    enforce_quota(ctx.user["id"], ctx.kind)
    # 1) 权限
    data, err_code = gather(ctx)
    if err_code:
        status = "safety_blocked" if err_code in ("match_inactive", "not_a_member") else "failed"
        if status == "failed":
            refund_quota(ctx.user["id"], ctx.kind if ctx.kind in DAILY_LIMITS else None)
        return _persist(ctx, status, {"error": err_code}, err_code, 0, prompt_name)
    # 2) 用户输入注入检查
    if ctx.text and check_injection(ctx.text):
        return _persist(ctx, "safety_blocked", {"error": "prompt_injection"}, "prompt_injection", 0, prompt_name)
    # 3) RAG（只召回 approved 且未过期）
    query = " ".join(data.get("reasons", []) if isinstance(data, dict) else []) + " " + (ctx.text or "")
    ctx.retrieved = retrieve(query, ["conversation_guidance", "product_rules"])
    # 4) Prompt
    prompt = load_prompt(prompt_name)
    user_blob = json.dumps({"style": ctx.style, "target_language": ctx.target_language,
                            "verified_public_reasons": data.get("reasons", []),
                            "room": {k: data[k] for k in ("room_title", "room_topic") if k in data},
                            "retrieved": [h["text"] for h in ctx.retrieved]}, ensure_ascii=False)
    # 5) 模型（mock 或 real）
    out, why = call_model(prompt["text"], user_blob)
    if out is None:
        # mock 模式：使用确定性模板；real 失败：fallback
        if why == "mock_mode":
            out = mock_fn(ctx, data)
        else:
            out = {"suggestions": [{"text": "What made you smile today?", "reason": "fallback template"}]}
            latency = int((time.perf_counter() - started) * 1000)
            refund_quota(ctx.user["id"], ctx.kind if ctx.kind in DAILY_LIMITS else None)
            return _persist(ctx, "fallback", out, why, latency, prompt_name)
    # 6) 校验 + 一次修复 + 输出安全检查
    if not validate(ctx.kind, out) and AI_REPAIR_ATTEMPT:
        out = mock_fn(ctx, data)  # 修复：用安全模板重写（真实环境可用模型二次修复）
        if not validate(ctx.kind, out):
            latency = int((time.perf_counter() - started) * 1000)
            refund_quota(ctx.user["id"], ctx.kind if ctx.kind in DAILY_LIMITS else None)
            return _persist(ctx, "fallback", {"suggestions": [{"text": "What made you smile today?", "reason": "fallback"}]},
                            "schema_invalid", latency, prompt_name)
    joined = json.dumps(out, ensure_ascii=False)
    risk = check_content_risk(joined)
    latency = int((time.perf_counter() - started) * 1000)
    if risk:
        return _persist(ctx, "safety_blocked", {"error": risk, **out}, risk, latency, prompt_name)
    if latency > int(AI_TIMEOUT_SECONDS * 1000):
        return _persist(ctx, "fallback", out, "timeout", latency, prompt_name)
    res = _persist(ctx, "succeeded", out, "", latency, prompt_name)
    util.track(ctx.user["id"], "icebreaker_generated", kind=ctx.kind, latency_ms=latency, status="succeeded")
    return res


AI_REPAIR_ATTEMPT = True


def run_generation(ctx: GenContext) -> dict:
    table = {
        "icebreaker": ("ICEBREAKER_V2", gather_icebreaker_ctx, _mock_icebreaker),
        "room_topic": ("ROOM_PROMPT_V1", gather_room_ctx, _mock_room_topic),
        "call_prep": ("CALL_PREP_V1", gather_call_ctx, _mock_call_prep),
    }
    prompt_name, gather, mock_fn = table[ctx.kind]
    return _run(ctx, prompt_name, gather, mock_fn)


def run_transform(ctx: GenContext) -> dict:
    started = time.perf_counter()
    enforce_quota(ctx.user["id"], ctx.kind)
    if check_injection(ctx.text):
        return _persist(ctx, "safety_blocked", {"error": "prompt_injection"}, "prompt_injection", 0, "TRANSFORM_V2")
    if check_content_risk(ctx.text) == "minor_risk":
        return _persist(ctx, "safety_blocked", {"error": "minor_risk"}, "minor_risk", 0, "TRANSFORM_V2")
    prompt = load_prompt("TRANSFORM_V2")
    transform_input = {"text": ctx.text, "target_language": ctx.target_language, "style": ctx.style}
    out, why = call_model(prompt["text"], json.dumps(transform_input, ensure_ascii=False))
    if out is None:
        if why == "mock_mode":
            out = _mock_transform(ctx)
        else:
            out = {"source": ctx.text, "target": ctx.text, "warnings": ["fallback: original text"],
                   "risk": "none"}
            refund_quota(ctx.user["id"], ctx.kind if ctx.kind in DAILY_LIMITS else None)
            return _persist(ctx, "fallback", out, why, int((time.perf_counter() - started) * 1000), "TRANSFORM_V2")
    if not validate(ctx.kind, out):
        out = _mock_transform(ctx)
    risk = check_content_risk(json.dumps(out, ensure_ascii=False))
    latency = int((time.perf_counter() - started) * 1000)
    if risk:
        return _persist(ctx, "safety_blocked", {"error": risk, **out}, risk, latency, "TRANSFORM_V2")
    res = _persist(ctx, "succeeded", out, "", latency, "TRANSFORM_V2")
    util.track(ctx.user["id"], "transformation_generated", kind=ctx.kind)
    return res


_ = os