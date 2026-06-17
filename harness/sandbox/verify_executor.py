"""Verifier execution layer (M1.4-2). Deterministic — no LLM.

爆破专家(verifier)执行 task_card.verification[] 里的验证条件:
- machine_verifiable: 在 builder worktree 内 subprocess 跑 command(逐字),按 expected
  字段做布尔比对,落 verify_run 事件
- hitl_escalation: 落 hitl_request 事件

判定完全等价于"命令 exit_code + expected 字段比对",链路上**不出现 LLM**(ADR-004
信任问题转化为执行问题)。verifier 只读+执行,不写 builder worktree(ADR-014)——本模块
不暴露任何写工具,从 API 层挡掉。

verifier **不产 review_verdict**(那是 Guide 仲裁路径,M1.4-3)。
"""
import re
import subprocess
from pathlib import Path

from harness.session.store import SessionStore


def verify_task(
    verifier_instance_id: str,   # e.g. "blaster#1"
    task_card: dict,             # 含 verification[] 数组
    builder_worktree: Path,      # subprocess cwd（ADR-014：builder 的 worktree）
    session_store: SessionStore,
    guide_assign_event_id: int,  # parent 链锚点
) -> list[int]:
    """执行单项验证条件,落事件,返回落下的事件 id 列表(供测试断言)。"""
    verifications = task_card["verification"]
    if len(verifications) != 1:
        raise NotImplementedError(
            "M1.4 verifier supports single-item verification only; "
            "multi-item aggregation pending ADR"
        )

    item = verifications[0]
    task_id = task_card["task_id"]
    # verify_run / hitl_request 与 trigger 同 session;从 parent 事件继承 session_id。
    session_id = session_store.get_event(guide_assign_event_id)["session_id"]

    vtype = item["type"]

    if vtype == "machine_verifiable":
        command = item["command"]          # 此变量从此一字不改(INV-2 强约束)
        expected = item.get("expected", {})
        if "artifact" in expected:
            raise NotImplementedError(
                "M1.4 verifier does not support 'artifact' expectation yet"
            )

        result = subprocess.run(
            command,
            shell=True,
            cwd=str(builder_worktree),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # 非 UTF-8 控制台(如中文 Windows GBK)不崩溃,同 bash.py
            timeout=60,
        )

        # 通过判定(逐项 AND)
        passed = result.returncode == expected.get("exit_code", 0)
        if passed and "stdout_contains" in expected:
            passed = expected["stdout_contains"] in result.stdout
        if passed and "stdout_matches" in expected:
            passed = re.search(expected["stdout_matches"], result.stdout) is not None
        if passed and expected.get("stderr_empty") is True:
            passed = result.stderr.strip() == ""

        if passed:
            output_summary = result.stdout.strip()[:200] or "OK"
        else:
            output_summary = (
                f"FAIL: exit_code={result.returncode}, "
                f"stdout={result.stdout[:100]}, stderr={result.stderr[:100]}"
            )

        ev = session_store.append_event(
            type="verify_run",
            agent=verifier_instance_id,
            parent_event_id=guide_assign_event_id,
            session_id=session_id,
            payload={
                "task_id": task_id,
                "command": command,        # 逐字
                "exit_code": result.returncode,
                "passed": passed,
                "output_summary": output_summary,
            },
        )
        return [ev["event_id"]]

    if vtype == "hitl_escalation":
        ev = session_store.append_event(
            type="hitl_request",
            agent=verifier_instance_id,
            parent_event_id=guide_assign_event_id,
            session_id=session_id,
            payload={
                "task_id": task_id,
                "reason": item.get("reason", "machine verification not applicable"),
                # verification.schema 的 hitl_escalation 字段是 acceptance_prompt;
                # 映射到 events.schema p_hitl_request 的 required 字段 question。
                # (spec 写的 item['question'] 与 verification.schema 不符,按 schema 为准修正)
                "question": item["acceptance_prompt"],  # 必填,缺失则 KeyError
            },
        )
        return [ev["event_id"]]

    raise ValueError(f"Unknown verification type: {vtype!r}")
