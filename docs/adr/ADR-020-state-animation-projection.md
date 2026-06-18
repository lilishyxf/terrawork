# ADR-020: 状态-动画协议 + View 动画投影(M3-1)

## 背景

§7「状态-动画协议」是 View 层核心资产:每个动画状态严格映射一个 Harness 真实状态(ADR-003),表外不加装饰动画,新增动画须登记 §7。§7 现有 10 条映射(无任务/排队/think/工具执行/等待审查/返工/验证中/卡死/HITL/合并)。

ADR-001 定 View = Session 订阅者,**自己从事件派生动画状态**(Catch-up 快进 → Live 逐条播)。

M3 进入 View,需把 §7 落成可执行契约。两个现实:
1. **§7 现有状态不够**:M2.6/M2.7 引入的新机制——双审查(两个审查席)、并行多专家(各占小屋)、专家职业各异、merge-then-verify——需要补登记状态与空间语义。按"新增动画须登记 §7"+ ADR-008,本 ADR 扩展 §7(不直接改 ARCHITECTURE 正文)。
2. **派生逻辑需权威参考**:View(TS)还不存在,但"事件流 → 每个 NPC 当前动画状态"的派生是核心资产。本 ADR 落一个 **Python 参考投影**(纯函数,可测),作为权威规范供 TS 镜像,亦可供后端 catch-up 做快照。

## 决策

### 1. 动画状态集(§7 + 扩展)
落成 `docs/contracts/animation_protocol.json`(canonical 表,src/game/protocol/ 镜像之):

| state | 含义 | §7 来源 |
|---|---|---|
| idle | 资源空闲可分配 | 无任务 |
| decomposing | 向导拆解需求 | 扩展(向导编排) |
| thinking | LLM 推理中,勿扰(think 对人可悬停) | LLM 推理中 |
| working | 工具执行中,正在干活(按职业皮肤:商人钱币泡…) | 工具执行中 |
| rework | 上轮被退回,返工中 | 被退回返工 |
| awaiting_review | 完工,流程阻塞在审查 | 等待审查 |
| verifying | 机器验证在跑 | 验证执行中 |
| reviewing | 审查中(代码/安全) | 扩展(双审查 ADR-019) |
| hitl | 需要人类,最高优先级 | 等待人类(HITL) |
| error | 卡死/循环/报错 | 卡死/报错 |

全局信号(非 per-NPC 状态):`merge` → 钟楼敲钟(合并完成)。

### 2. 空间语义区(把"大房子"模型编码)
| zone | 含义 | 谁在此 |
|---|---|---|
| yard | 室外院子(睡/钓/玩) | idle 的 builder 工人 |
| lobby | 大堂/前台 | 向导(decomposing/thinking/idle) |
| workshop | 工坊小屋(每个活跃 builder 实例一间) | working/thinking/rework 的 builder |
| review_door | 审查间门口(踱步) | awaiting_review 的 builder |
| verify_room | 验证间 | 爆破工(verifying;idle 留守) |
| review_room | 审查间(裁缝代码 + appsec 安全两席) | tailor/appsec(reviewing;idle 留守) |
| at_glass | 屏幕前 | hitl(向导敲玻璃) |

**移动 = 区间转移**(派活:yard→workshop;完工:workshop→review_door;验证:→verify_room;审查:→review_room;返工:回 workshop;合并:钟楼)。固定班子(向导/爆破/裁缝/appsec)idle 时留守各自 home zone;builder 工人 idle 时回院子——闲置即"资源空闲"信息(ADR-003)。

### 3. 实例 → sprite-key 间接层(色块→精灵零摩擦换皮)
- guide → `guide`;`<specialty>#N`(builder)→ `<specialty>`;`blaster#N`→ `blaster`;`tailor#N`→ `tailor`;`appsec#N`→ `appsec`。
- 色块阶段按 sprite-key 取色/标签;精灵阶段按同一 key 取图,协议不变。

### 4. View 动画投影(Python 参考实现)
`harness/view/projection.py` 的 `project(events)` —— **纯函数 reducer**,顺序消费事件,维护每个 NPC 的 `{kind, sprite_key, state, zone, task_id}`,返回快照 + 全局 `last_merge` + `cursor`。这正是 Catch-up 快进的语义(replay 到游标即得当前状态)。派生规则(摘要):

- `user_command`→guide:decomposing;`guide_think`→guide:thinking
- `guide_assign(inst)`:builder→working(同实例再次派=rework)、blaster→verifying、tailor/appsec→reviewing;guide 回 lobby idle
- `npc_think`→该实例:thinking;`tool_intent/tool_done`→working;`review_request`→awaiting_review
- `verify_run`→blaster:idle(验完);`review_verdict`→该 reviewer:idle(审完)
- `merge(tid)`→该卡 builder 实例:idle(回院子);置全局 last_merge
- `hitl_request`→guide:hitl(敲玻璃);`error`→该 NPC:error(保留原 zone,冒烟叠加)

固定班子(guide/blaster/tailor/appsec)恒存在;builder 实例在首次 `guide_assign` 出现。

### 5. 治理
- 扩展 §7 的可追溯性由本 ADR + animation_protocol.json + git diff 承载,不改 ARCHITECTURE.md §7 正文(沿用 ADR-014/016 同款治理)。
- View 只读派生(§1 红线#1),投影是纯函数无副作用;玩家交互(M4)另经 user_* 事件,不在本 ADR。

## 不变量(INV,fixture + 测试)

- **INV-1**:投影产出的所有 state 都在 animation_protocol.json 的 states 内(无表外状态,ADR-003)。
- **INV-2**:happy 单卡双审查流的关键游标快照正确——working@workshop、awaiting_review@review_door、verifying→idle、reviewing→idle、merge 后 builder idle@yard + last_merge。
- **INV-3**:同实例再次 guide_assign(返工)→ rework(非 working)。
- **INV-4**:hitl_request → guide hitl@at_glass;error → 该 NPC error 且保留原 zone。
- **INV-5**:实例→sprite-key 映射正确(frontend#1→frontend、tailor#1→tailor、guide→guide)。

## 后果

- M3 View 有了**权威、可测**的动画派生规范;TS 端(M3-3/4)镜像本投影 + 读 animation_protocol.json。
- 投影是纯读 reducer,等价 Catch-up 快进;后端(M3-2)可复用它做快照。
- §7 在不动正文的前提下扩展登记了新机制状态,维持"表外无装饰动画"红线。
- 后续:M3-2 订阅线协议 + FastAPI(catch-up/live);M3-3 前端骨架;M3-4 像素渲染(§12 验收)。
