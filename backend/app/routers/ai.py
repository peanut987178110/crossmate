"""AI 工具（FR07）。

四条硬约束：
1) AI 只产出建议，不提供任何发送能力（插件里不存在 send_message 工具）；
2) 用户点击“使用”只回填输入框，仍需手动点发送；
3) 用户文字与检索内容都是数据，不能覆盖系统规则（Prompt 注入防护）；
4) 超时 / Schema 失败 -> fallback 静态模板，并在响应里标记。

路由：
  POST /ai/generations       icebreaker / room_topic / call_prep
  POST /ai/transformations   translate / rewrite
  POST /ai/feedback          对 AI 输出反馈（不奖励）
  GET  /ai/generations/{id}  回看一次生成（审计）
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core import db, util
from app.core.http import err, ok
from app.routers.auth import current_user
from app.services import ai_engine

router = APIRouter()


def me(authorization: str = Header(default="", alias="Authorization")) -> dict:
    return current_user(authorization)


KIND_TO_PROMPT = {
    "icebreaker": "ICEBREAKER_V2",
    "room_topic": "ROOM_PROMPT_V1",
    "call_prep": "CALL_PREP_V1",
}
TRANSFORM_TO_PROMPT = {"translate": "TRANSFORM_V2", "rewrite": "TRANSFORM_V2"}


class GenerationIn(BaseModel):
    kind: str  # icebreaker | room_topic | call_prep
    match_id: str | None = None
    room_id: str | None = None
    call_id: str | None = None
    target_language: str = "en"
    style: str = "natural"


class TransformIn(BaseModel):
    kind: str  # translate | rewrite
    text: str = Field(min_length=1, max_length=2000)
    target_language: str = "zh"
    style: str = "natural"


class FeedbackIn(BaseModel):
    generation_id: str
    rating: int = Field(ge=1, le=5)  # 1 dislike, 5 love
    comment: str = ""


@router.post("/ai/generations")
def create_generation(body: GenerationIn, user: dict = Depends(me)):
    if body.kind not in KIND_TO_PROMPT:
        raise err(400, 40060, "unknown generation kind")
    ctx = ai_engine.GenContext(user=user, kind=body.kind, match_id=body.match_id, room_id=body.room_id,
                               call_id=body.call_id, target_language=body.target_language, style=body.style)
    res = ai_engine.run_generation(ctx)
    return ok(res)


@router.post("/ai/transformations")
def create_transformation(body: TransformIn, user: dict = Depends(me)):
    if body.kind not in TRANSFORM_TO_PROMPT:
        raise err(400, 40060, "unknown transformation kind")
    ctx = ai_engine.GenContext(user=user, kind=body.kind, text=body.text,
                               target_language=body.target_language, style=body.style)
    res = ai_engine.run_transform(ctx)
    return ok(res)


@router.post("/ai/feedback")
def ai_feedback(body: FeedbackIn, user: dict = Depends(me)):
    g = db.q1("SELECT * FROM ai_generations WHERE id=? AND user_id=?", (body.generation_id, user["id"]))
    if not g:
        raise err(404, 40420, "generation not found")
    fid = util.new_id("fb")
    with db.tx() as c:
        c.execute("INSERT INTO feedback(id,user_id,target_type,target_id,rating,comment,quality,created_at) "
                  "VALUES(?,?,?,?,?,?,'ai',?)",
                  (fid, user["id"], "ai_generation", body.generation_id, body.rating, body.comment[:500], util.now()))
    # AI 反馈不发积分（防止刷分），但计入采纳率指标来源之一
    return ok({"feedback_id": fid})  # 明确：不发放积分


@router.get("/ai/generations/{gen_id}")
def get_generation(gen_id: str, user: dict = Depends(me)):
    g = db.q1("SELECT * FROM ai_generations WHERE id=? AND user_id=?", (gen_id, user["id"]))
    if not g:
        raise err(404, 40420, "generation not found")
    d = dict(g)
    d["output"] = util.loads(d["output"], {})
    d["retrieved"] = util.loads(d["retrieved"], [])
    return ok(d)


@router.post("/ai/adopt")
def adopt(body: dict, user: dict = Depends(me)):
    """记录“AI 建议被采用”（采纳 ≠ 发送）。真正发送要用户再点发送按钮。"""
    gid = body.get("generation_id")
    g = db.q1("SELECT * FROM ai_generations WHERE id=? AND user_id=?", (gid, user["id"]))
    if not g:
        raise err(404, 40420, "generation not found")
    idx = int(body.get("index", 0))
    with db.tx() as c:
        c.execute("INSERT INTO ai_adoptions(id,generation_id,user_id,suggestion_index,created_at) VALUES(?,?,?,?,?)",
                  (util.new_id("ad"), gid, user["id"], idx, util.now()))
    util.track(user["id"], "icebreaker_adopted", generation_id=gid, kind=g["kind"])
    return ok({"adopted": True, "filled_into_input": True, "sent": False})


@router.get("/ai/usage")
def usage(user: dict = Depends(me)):
    rows = db.q("SELECT kind,count FROM ai_usage WHERE user_id=? AND day=?", (user["id"], ai_engine.today()))
    return ok({r["kind"]: r["count"] for r in rows})


_ = time  # 保持 import 以便将来做延迟统计