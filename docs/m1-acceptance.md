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
