# M1 验收记录

## M1.1 (2026-06-13)

- 解释器: Python 3.11.5 (.venv)
- 依赖锁: harness/requirements.lock (jsonschema==4.26.0, referencing==0.37.0, pytest==9.0.3 + 子依赖)
- pytest: 5 passed

```
============================= test session starts =============================
platform win32 -- Python 3.11.5, pytest-9.0.3, pluggy-1.6.0 -- D:\Projects\TerraWorks\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\Projects\TerraWorks
collecting ... collected 5 items

harness/tests/test_session.py::test_login_chain_inserts_and_replays PASSED [ 20%]
harness/tests/test_session.py::test_event_id_autoincrement_contiguous PASSED [ 40%]
harness/tests/test_session.py::test_invalid_events_rejected PASSED       [ 60%]
harness/tests/test_session.py::test_append_only_blocks_update_and_delete PASSED [ 80%]
harness/tests/test_session.py::test_missing_parent_rejected PASSED       [100%]

============================== 5 passed in 0.44s ==============================
```

- 验收信号 1 (因果树重建): ✅ login_chain 10 事件灌入，按 parent_event_id 重建因果树，tool_done 挂配对 tool_intent、review_verdict 挂 review_request
- 验收信号 2 (非法事件全拒、落盘 0): ✅ 自然语言谓词 / 缺 parent (parent_event_id: None is not of type 'integer') / 未知 type 三类全拒，落盘 0 拒绝 3
- 验收信号 3 (db 文件 strings 可读、UPDATE/DELETE 被触发器拦下): ✅ db 内 "做个登录"/"machine_verifiable"/"t-login-impl" 明文 FOUND；UPDATE/DELETE 被 append-only 触发器 RAISE(ABORT)
- 3.11 与 3.13 行为一致性: 9 个回归 case 拒绝集合与错误信息完全相同
- ADR-007: 后果段已回填 venv 二级证据

## M1.2 (2026-06-14)

**M1.2 完成定义**:Guide 在真实 LLM 上能把自然语言模糊指令分解成符合契约的任务卡序列,全程 schema 校验闭环。

### 关键交付

- 4 份新增 ADR:ADR-007(parent_event_id 非空整数)、ADR-008(ADR 文件治理)、ADR-009(LLM provider 设计)、ADR-010(guide_delegate.assignee 放宽)
- 契约 fixture:`harness/tests/fixtures/m12_login_command.json`(7 条 invariants + reference output)
- LLM 抽象层:`harness/llm/`(LiteLLM facade + fixture-driven mock client + 工厂方法)
- Guide 代码骨架:`harness/guide/`(prompt + parser + step 纯函数 + 重试循环 + hitl 兜底)
- 真实 LLM 测试 scaffolding:`harness/tests/conftest.py` + `.env.example` + 参数化 provider 测试

### 测试通过情况

- 解释器:Python 3.11.5 (.venv)
- 依赖锁:`harness/requirements.lock` 已含 litellm / python-dotenv / pyyaml 精确版本
- **Mock LLM**:`test_guide_step_with_mock_llm_satisfies_invariants` PASSED
- **真实 LLM(DeepSeek `deepseek/deepseek-chat`)**:PASSED,**16.89 秒**,一次输出就合规、未触发 hitl_request 兜底
- 其他两家 provider(`openai/gpt-4o` / `anthropic/claude-sonnet-4-6`):暂未配置 key,skipif 自动 skip,接入后可一并验证

### 关键经验(供后续阶段参照)

1. **schema-as-ground-truth**:任何涉及契约字段或异常类型的代码,**先 grep 契约文件,不能凭记忆**。M1.2 起手凭记忆设计的 fixture 字段名(`goal`/`tools_allowed`/`verification` 单对象 / `hitl_request` 字段)与 M0 schema 全部对不上;step.py 的异常类型(`SchemaError` vs `ValidationError`)又踩同款认知陷阱。
2. **append-only 是硬约束**:任何"修改既有事件"的设计动作必须当场否决。ADR-010 起初写"M1.3 给既有 guide_delegate 追加 assignee 字段"——这是 UPDATE,与 M1.1 落地的 `BEFORE UPDATE … RAISE(ABORT)` 触发器直接冲突,改为"新增 guide_assign 事件"才与 append-only 兼容。
3. **新依赖常已在传递依赖里**:litellm 把 yaml / dotenv 拉为传递依赖。声明新直接依赖时,先 `pip show <pkg>` 看是否已可用,显式声明到 requirements.txt(显式胜过隐式),lock 文件可能无 diff——这种情况据实提交,不强塞。
4. **API key 永远不在终端命令行出现**——粘贴 secret 的唯一安全位置是文本编辑器写到 `.gitignored` 文件。Reflex 应建立。
