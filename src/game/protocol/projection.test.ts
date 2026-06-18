// Parity 测试:TS 投影对同一 Python fixture 产出相同结果,锁双端一致(ADR-020)。
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { project } from "./projection";
import type { TerraEvent } from "../../ipc/subscribe";

const FX = JSON.parse(readFileSync(
  resolve(__dirname, "../../../harness/tests/fixtures/m3_projection.json"), "utf-8",
)) as {
  events: TerraEvent[];
  checkpoints: Array<{
    after_event_id: number;
    expect: Record<string, { state: string; zone: string }>;
  }>;
  expect_last_merge_task: string;
};

describe("TS projection 与 Python fixture parity", () => {
  for (const cp of FX.checkpoints) {
    it(`game cursor ≤ ${cp.after_event_id}: 关键 NPC 状态/区匹配`, () => {
      const snap = project(FX.events.filter((e) => e.event_id <= cp.after_event_id));
      for (const [id, exp] of Object.entries(cp.expect)) {
        const got = snap.npcs[id];
        expect(got, `${id} 应在场`).toBeDefined();
        expect({ state: got!.state, zone: got!.zone }).toEqual(exp);
      }
    });
  }

  it("merge 后 last_merge 置位且 builder 回院子", () => {
    const snap = project(FX.events);
    expect(snap.last_merge?.task_id).toBe(FX.expect_last_merge_task);
    expect(snap.npcs["merchant#1"].state).toBe("idle");
    expect(snap.npcs["merchant#1"].zone).toBe("yard");
  });
});
