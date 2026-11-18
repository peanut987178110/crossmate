# CrossMate AI 工作流与 Skills v0.2

> 本文回答两个问题：**AI 在 CrossMate 里由哪些可复用的“技能”组成？** 以及**它们在一次用户操作中被按什么状态顺序串联、如何限流、如何降级、如何追踪？**

---

## 1. 角色分层

| 角色 | 做什么 | 不做什么 |
|---|---|---|
| **Skill**（技能） | 一个原子能力：输入结构化参数 → 输出符合 Schema 的结果 | 不直接操作数据库、不发消息、不做权限判断 |
| **Workflow**（工作流） | 编排：权限 → 限流 → 取上下文 → 调 Skill → 校验 → 降级 → 入库 → 埋点 | 不写 Prompt |
| **Prompt** | Skill 的“大脑”，见 `docs/03-Prompt与RAG.md` | 不含业务权限逻辑 |
| **Policy / Guard**（策略与护栏） | 注入检测、输出风险检测、Schema 校验、配额 | 不做生成 |

这样的分层让“换个模型 / 换个 Prompt 版本”不影响业务编排，也方便测试：Skill 可以单独单测（mock 模式就是 Skill 的确定性实现）。

---

## 2. Skills 定义

每个 Skill 用统一契约描述：`id / 触发 / 输入 / 输出 / 前置条件 / 配额 / 降级 / 测试`。

### S1 `icebreaker.suggest`

| 项 | 内容 |
|---|---|
| 目的 | 匹配后给出 3 条自然、具体、有依据的开场白 |
| 触发 | 聊天页“需要开场白”、匹配成功页“需要开场白” |
| 输入 | `match_id`、`target_language`、`style(natural/polite/casual)` |
| 上下文 | 自己 + 对方的 **已授权公开字段**（共同兴趣/语言/社交目标） |
| 输出 | `{suggestions:[{text(≤120), reason}], notes}`，恰好 3 条 |
| 前置 | 调用者是该匹配成员；匹配 `active` |
| 配额 | 10 次/人/日（UTC） |
| 降级 | 超时/错误 → 1 条模板建议；注入 → `safety_blocked` |
| 测试 | TC11 / TC12 / TC14 / TC35、E2E 回填不发送 |

### S2 `translate.explain`

| 项 | 内容 |
|---|---|
| 目的 | 把对方的消息翻成我的语言，并解释语气/语境 |
| 输入 | `text`、`target_language`、`style` |
| 输出 | `{source,target,warnings[],risk}` |
| 规则 | 保留否定、数字、时间、称谓；不解释他人隐私 |
| 配额 | 200/日 |
| 降级 | 原文返回 + `warnings:["fallback"]` |
| 测试 | TC13 |

### S3 `rewrite.tone`

| 项 | 内容 |
|---|---|
| 目的 | 在不改变意思的前提下调整语气（更礼貌/更轻松） |
| 输入/输出 | 同 S2 |
| 规则 | 不新增事实；不把拒绝改为答应；不删除关键信息 |
| 配额 | 200/日 |

### S4 `room.topic`

| 项 | 内容 |
|---|---|
| 目的 | 给主持人提供 3 个中性讨论问题 |
| 输入 | `room_id`（只读房间标题和公开主题） |
| 前置 | 调用者是房间主持人 |
| 输出 | 同 S1 结构 |
| 规则 | 不分析成员、不引用私聊；高风险话题 → 3 条替代话题 + `risk:high` |
| 配额 | 5/日 |
| 测试 | `test_room_rag_topic_uses_public_topic_only` |

### S5 `call.prep`

| 项 | 内容 |
|---|---|
| 目的 | 通话前给 2 条“可以从哪聊起”的方向（不是逐字稿） |
| 前置 | 双方均已授权（consent） |
| 配额 | 5/日 |

### S6 `safety.triage`

| 项 | 内容 |
|---|---|
| 目的 | 举报分流，不给封禁 |
| 输入 | 脱敏证据：`category`、`target_type`、`detail_length`、历史有效举报数——**无原文** |
| 输出 | `{risk_level, reason_codes[], queue, evidence_id}` |
| 规则 | 高/中/低 → 队列；低置信交人工 |
| 测试 | TC22 |

### S7 `recommend.explain`（非 LLM）

| 项 | 内容 |
|---|---|
| 目的 | 为每张卡片生成 `match_hint`（“为什么推荐你们聊”） |
| 实现 | 规则模板：共同兴趣 / 语言互补 / 时区接近，取最高分且可展示的一项 |
| 为什么不用 LLM | 确定性、零成本、零延迟、可回归测试；LLM 版以后只做“润色不改变事实” |
| 测试 | `test_discover_never_returns_points_or_sensitive` |

---

## 3. Workflow：一次“AI 破冰”的完整状态机

```
[1] RECEIVED       POST /ai/generations {kind:"icebreaker", match_id}
        │
[2] AUTHORIZED     会话有效？匹配存在且我是成员？匹配 active？        否 → REJECTED(404/40300)
        │
[3] QUOTED         今日 icebreaker 计数 < 10？                        否 → QUOTA_EXCEEDED(42902)
        │
[4] CONTEXT        public_view(我) + public_view(对方) → verified_public_reasons
        │          RAG: conversation_guidance(approved & 未过期) → retrieved
        │
[5] ASSEMBLED      ICEBREAKER_V2（文件开头已内嵌 GLOBAL_V1 规则）+ JSON 输入
        │
[6] GUARDED        check_injection(输入)                              命中 → SAFETY_BLOCKED
        │
[7] GENERATED      real: 网关调用（≤2s）| mock: 确定性模板             错误/超时 → FALLBACK
        │
[8] VALIDATED      JSON Schema（条数=3、长度、字段齐全）
        │          失败 → 修复 1 次 → 仍失败 → FALLBACK
        │          输出含“自称真人/已发送/刻板印象” → SAFETY_BLOCKED
        │
[9] PERSISTED      写 ai_generations（status、fallback_reason、latency_ms、prompt_name）
        │
[10] DELIVERED     返回前端 → 展示 3 条建议 + 每条 reason
        │
[11] ADOPTED       用户点击某条 → POST /ai/adopt（记录采纳）→ 前端只回填输入框
        │
[12] SENT          用户点击发送 → POST /matches/{id}/messages（source_generation_id）
```

**关键不变量**

- 第 11→12 步中间没有自动跳转；`ai/adopt` 的返回体明确 `"sent": false`。
- 第 9 步之前没有任何副作用可被用户感知为“已发送”。
- 第 2、3 步在任何生成之前，避免用“失败”消耗成本。

## 4. 其他工作流的差异点

| 工作流 | 与 S1 的差异 |
|---|---|
| 翻译/改写 | 无匹配上下文；额外的 `safety_blocked` 风险（原文含未成年/诈骗） |
| 房间话题 | 权限是“主持人”；上下文只有公开主题 |
| 通话准备 | 权限是“通话双方”；必须有 consent |
| 举报分流 | 无用户可见输出（内部）；同步阻塞但 ≤200ms（规则 + 可选模型） |

## 5. 限流与成本

| 层 | 手段 |
|---|---|
| 用户级 | `ai_usage(user_id, kind, day)` 计数；UTC 日重置 |
| 全局 | （生产建议）网关层令牌桶，按上游配额 |
| 缓存 | （建议）同一 `match_id` 10 分钟内复用最近一次生成，不重复计费；v0.2 未做，列入 v0.3 |
| 超时 | 2s；不重试；一次结构修复仅针对 Schema |

## 6. 可观测性

每条生成落库一条 `ai_generations`：

| 字段 | 用途 |
|---|---|
| `user_id, kind, match_id` | 归因 |
| `status` | succeeded / fallback / safety_blocked / failed |
| `fallback_reason` | timeout / model_error / schema_error / prompt_injection / not_a_member / minor_risk |
| `latency_ms` | P50/P95 看板 |
| `prompt_name` | 版本灰度对比（如 ICEBREAKER_V2 vs V3） |
| `output` | **仅存结构化输出**；不存用户原文与 Prompt 全文（合规与体积） |

`ai_adoptions` 记录采纳；埋点 `icebreaker_generated` / `icebreaker_adopted` 计算采纳率。

## 7. 版本与灰度（v0.3 规划）

1. Prompt 文件带版本号，`KIND_TO_PROMPT` 按用户哈希分桶选版本。
2. 指标：采纳率、采纳→发送率、举报率（护栏）。
3. 回滚：改映射表即可，无需发布代码。

## 8. 失败设计原则（面试重点）

> “AI 功能的产品设计，一半是设计它成功时做什么，另一半是设计它失败时用户还能做什么。”

- 每个 AI 入口旁边都有“不用 AI 也能继续”的路径。
- 失败提示说清原因（暂时不可用 / 今天次数用完 / 内容不适合生成），不输出技术错误。
- 失败不计用户额度（`refund_quota`：超时/模型错误/Schema 失败会退还当日计数；注入与越权属于用户行为，不退；见 `test_tc14_fallback_on_model_error` 的断言）。