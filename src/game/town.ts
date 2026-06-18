// M3-4 像素小镇渲染(Phaser 3,色块版)。
// 按 ADR-020 把 NPC 放进 7 个语义区,事件来时按 state 切换颜色 + 在区间走动。
// 色块阶段:每个 NPC = 一个矩形 + 标签;sprite-key 决定颜色。
// M3-5/M5 换皮:矩形换精灵,key 不变。
import Phaser from "phaser";
import type { NpcSnapshot, NpcState, Zone, ViewSnapshot } from "./protocol/projection";

const W = 880, H = 560;

// 语义区中心位置(相对画布)。同区多 NPC 会自动错位避叠。
const ZONE_POS: Record<Zone, { x: number; y: number; w: number; h: number; label: string }> = {
  lobby:        { x: 440, y: 80,  w: 220, h: 80,  label: "大堂(向导)" },
  workshop:     { x: 440, y: 220, w: 360, h: 120, label: "工坊小屋" },
  review_door:  { x: 700, y: 220, w: 140, h: 120, label: "审查间门口" },
  review_room:  { x: 700, y: 340, w: 140, h: 100, label: "审查间" },
  verify_room:  { x: 180, y: 340, w: 140, h: 100, label: "验证间" },
  yard:         { x: 440, y: 470, w: 460, h: 70,  label: "院子(idle)" },
  at_glass:     { x: 440, y: 30,  w: 200, h: 32,  label: "屏幕前(HITL)" },
};

// state → 色块颜色(色块阶段的语义编码;换皮后由精灵动画承担)
const STATE_COLOR: Record<NpcState, number> = {
  idle:            0x4a8c4a,
  decomposing:     0xd4a017,
  thinking:        0x7b6cf2,
  working:         0x2e86c1,
  rework:          0xc0392b,
  awaiting_review: 0xe67e22,
  verifying:       0x9b59b6,
  reviewing:       0x16a085,
  hitl:            0xe74c3c,
  error:           0xff2222,
};

// sprite-key → 默认描边色(职业可辨)
const SPRITE_STROKE: Record<string, number> = {
  guide: 0xffd700, blaster: 0xff7043, tailor: 0x66bb6a, appsec: 0xab47bc,
  frontend: 0x29b6f6, backend: 0x5c6bc0, database: 0x8d6e63, desktop_shell: 0x78909c,
  ai_engineer: 0x9575cd, rapid_proto: 0x26a69a, tech_writer: 0xffa726, mobile: 0xec407a,
  merchant: 0xbdbdbd,
};

interface NpcView { rect: Phaser.GameObjects.Rectangle; label: Phaser.GameObjects.Text; }

export class TownScene extends Phaser.Scene {
  private npcs = new Map<string, NpcView>();
  private bell?: Phaser.GameObjects.Text;
  private lastMergeEid = 0;
  constructor() { super("Town"); }

  create() {
    // 区背景 + 标签
    for (const z of Object.values(ZONE_POS)) {
      this.add.rectangle(z.x, z.y, z.w, z.h, 0xf5f0e6, 0.55).setStrokeStyle(1, 0xcfc7b6);
      this.add.text(z.x - z.w / 2 + 6, z.y - z.h / 2 + 4, z.label,
        { fontSize: "12px", color: "#776" });
    }
    // 钟楼
    this.bell = this.add.text(40, 30, "🔔", { fontSize: "28px", color: "#a90" })
      .setAlpha(0.18);
    this.add.text(40, 60, "钟楼", { fontSize: "11px", color: "#aaa" }).setOrigin(0);
  }

  /** 用最新 snapshot 调和场景:create/move/recolor;触发 bell。 */
  applySnapshot(snap: ViewSnapshot) {
    const occ: Record<Zone, number> = {} as Record<Zone, number>;
    for (const [id, n] of Object.entries(snap.npcs)) {
      this._placeOrUpdate(id, n, occ);
    }
    // 不在 snapshot 里的旧 NPC 不删(固定班子恒存在;builder 实例只增不删,merge 后回 yard)
    if (snap.last_merge && snap.last_merge.event_id !== this.lastMergeEid) {
      this.lastMergeEid = snap.last_merge.event_id;
      this._ringBell();
    }
  }

  private _placeOrUpdate(id: string, n: NpcSnapshot, occ: Record<Zone, number>) {
    const z = ZONE_POS[n.zone];
    const idx = (occ[n.zone] = (occ[n.zone] ?? 0));
    occ[n.zone] = idx + 1;
    // 同区横向排开,每行最多 5 个
    const col = idx % 5, row = Math.floor(idx / 5);
    const x = z.x - z.w / 2 + 24 + col * 36;
    const y = z.y - 4 + row * 30;
    const color = STATE_COLOR[n.state];
    const stroke = SPRITE_STROKE[n.sprite_key] ?? 0x555555;

    let v = this.npcs.get(id);
    if (!v) {
      const rect = this.add.rectangle(x, y, 26, 22, color).setStrokeStyle(2, stroke);
      const label = this.add.text(x - 16, y + 12, id, { fontSize: "9px", color: "#444" });
      v = { rect, label };
      this.npcs.set(id, v);
    } else {
      this.tweens.add({ targets: v.rect, x, y, duration: 280, ease: "Sine.easeInOut" });
      this.tweens.add({ targets: v.label, x: x - 16, y: y + 12, duration: 280 });
      v.rect.setFillStyle(color);
      v.rect.setStrokeStyle(2, stroke);
    }
    // error 叠红边闪烁
    v.rect.setAlpha(n.state === "thinking" ? 0.85 : 1);
  }

  private _ringBell() {
    if (!this.bell) return;
    this.tweens.add({
      targets: this.bell, alpha: 1, duration: 200, yoyo: true, hold: 600,
      onComplete: () => this.bell?.setAlpha(0.18),
    });
  }
}

export function createTown(parent: HTMLElement): { game: Phaser.Game; scene: TownScene } {
  const scene = new TownScene();
  const game = new Phaser.Game({
    type: Phaser.AUTO, parent, width: W, height: H,
    backgroundColor: "#fbf8f1", scene,
  });
  return { game, scene };
}
