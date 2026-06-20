# ADR-022: 双向交互 — 写端点 + 服务端编排宿主(M4 v0.1)

## 背景

§1 红线#1:View 不直接写状态;玩家交互**转成 `user_*` 事件追加进 Session**,由 Harness 消费(CQRS 单向数据流)。M3 完成了"读半边"(只读事件服务 + 像素小镇)。M4 补"写半边",让产品成为真正的**双向编排**(动画 ⇄ Harness 操作闭环)——这是 §0 定位、区别于 Pixel Agents(单向镜子)的立身之本。

现状:
- 写路径事件 schema **早已预留**(M1 前瞻):`user_command{text}`、`user_interact{target,interaction}`、`hitl_response{decision:approve|reject|answer, text?}`(经 `parent_event_id` 指向某 `hitl_request`,ADR-007 双约束)。
- 编排器**只处理 user_command**(stage 1 分解),且判据是"全 session 无 delegate/hitl"——**第二条 user_command 不会被分解**;`hitl_response`/`user_interact` **完全没接**;`hitl_request` 是死胡同(发完即静止)。
- FastAPI sidecar(ADR-021)是**只读**事件管道。

## 决策(v0.1 最小主回路:下指令 → 看它跑 → 卡住时回应 → 继续)

### 1. 服务端从"只读管道"升级为"编排宿主"
- create_app 增 `repo_root` / `worktrees_base` / 可选 `llm_client`(测试注入;缺省 None→`get_llm_client()` 按 `TERRA_LLM_MODE`)。
- 新增**写端点**(append user 事件 → 触发 advance);读端点(events/snapshot/live, ADR-021)不变。
- 仍**不违反 §1**:写的是 `user_*` 事件(经 Session),不是直接改状态。

### 2. 写端点(wire 契约)
```
POST /sessions/{sid}/command   body {"text": "..."}
  → append user_command(agent="user") → 确保 advance 在跑 → 202 {"event_id": N}
POST /sessions/{sid}/hitl       body {"hitl_event_id": M, "decision": "answer|reject", "text"?: "..."}
  → 校验 M 是该 session 的 hitl_request → append hitl_response(parent_event_id=M) → 触发 advance → 202 {"event_id": N}
```
- 都是 202 + 返回落盘 event_id;**编排结果经现有 WS `live` 异步流回**(不在 HTTP 响应里等)。
- `approve` 决策 v0.1 不开放(留"强制合并"等高级场景);仅 `answer` / `reject`。
- `user_interact`(点 NPC)v0.1 不做后端:`inspect` 复用悬停;"跟向导下指令"前端直接走 `/command`。

### 3. advance-runner:后台 + 每 session 单飞
- POST 落事件后,若该 session **无 advance 在跑** → 后台启一个 `advance(store,...)` 跑到静止;**若已在跑** → 不另起(advance 每轮 re-query 事件,会自然吸收新 user 事件)。
- 单飞锁防同 session 并发跑乱(避免两个 advance 抢同一 worktree/事件)。跑在线程池(advance 阻塞 + 子进程,ADR-017)。
- 异常不崩服务:advance 抛错 → 记日志 + 落 `error` 事件(可选),runner 退出,锁释放。

### 4. 编排器改动(M4-3)
- **逐指令分解**:stage 1 改为"分解**每一条尚未分解**的 user_command"(按 event 粒度,非全 session 全局)——支持追加指令。判据:某 user_command 之后无引用它的 guide_think/guide_delegate(或该命令未产出 delegate)。
- **消费 hitl_response**(新 stage):
  - `answer`:把 `text` 作整改指引,**重派该 hitl 对应任务的 builder 返工**(人授权 → **绕过 max_rework 上限**,这是人工延长预算)。该卡 task_id 由 hitl_request.payload.task_id 取。
  - `reject`:该任务标记**放弃(终态)**,不再派发;wake/projection 状态 = `rejected`。
  - 一条 hitl_response 只消费一次(其后已有对应动作即"已处理",防重复)。

### 5. 投影/wake 兼容
- projection `task_board`:hitl_response answer → 任务回 `building`;reject → `rejected`/`abandoned`。
- 新 user_command → 新任务进板。

## 不变量(INV)

data-scope(M4-1 fixture + 自洽 test):
- **INV-1**:`hitl_response` 过 `events.schema`(p_hitl_response),`decision∈{answer,reject}`,`parent_event_id` 指向同 session 的 `hitl_request`。
- **INV-2**:第二条 `user_command` 过 schema(支持追加指令)。
- **INV-3**:`/command`、`/hitl` 的请求体字段与 wire 契约一致(端点契约自洽)。

runtime-scope(M4-2/3 e2e):
- **INV-4**:`/command` → 落 user_command + 后台 advance 跑通(WS 收到后续事件)。
- **INV-5**:`hitl_response.answer` → 该任务 builder 被**重派**(注入 text 为 rework_notes),即便此前 max_rework 已耗尽。
- **INV-6**:`hitl_response.reject` → 任务终态 rejected,无新派发。
- **INV-7**:追加的第二条 user_command → 触发新分解(新任务进板),不影响已有任务。
- **INV-8**:同 session advance 单飞(不并发)。

## 后果

- 闭合 §1 写半边:**下指令 → 小镇里看专家干活 → 卡住(HITL 屏幕前敲玻璃)→ 你回应 → 继续**,全程不开终端。这是 §12 M4 验收的核心。
- 服务端变编排宿主:承担 LLM 编排 + 并发管理;读路径与 WS 复用不变。
- v0.1 故意小:`approve`/强制合并、`user_interact` 的 intervene(暂停/重定向 NPC)、多 session 并发治理 留后续。
- 拆分:M4-1 契约(本)/ M4-2 写端点+runner+测试 / M4-3 编排器(逐指令分解 + hitl_response)/ M4-4 前端(指令框 + HITL 弹窗)。
- 不改既有契约 schema(user_* 事件 schema 已就绪);ADR-021 的只读约束在本 ADR 显式放宽为"读 + 经 user_* 事件写"。
