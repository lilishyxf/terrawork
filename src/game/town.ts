// M3-4/M3.6 像素小镇渲染(Phaser 3)。
// 按 ADR-020 把 NPC 放进 7 个语义区,事件来时在区间走动。
// M3.6:有 PNG 精灵则用精灵(public/sprites/<sprite_key>.png),缺图回退色块;
// NPC 当前 state 由角上的状态色点表示(精灵=职业身份静态,状态=色点)。
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

// sprite-key → 默认描边色(无精灵时的职业可辨色)
const SPRITE_STROKE: Record<string, number> = {
  guide: 0xffd700, blaster: 0xff7043, tailor: 0x66bb6a, appsec: 0xab47bc,
  frontend: 0x29b6f6, backend: 0x5c6bc0, database: 0x8d6e63, desktop_shell: 0x78909c,
  ai_engineer: 0x9575cd, rapid_proto: 0x26a69a, tech_writer: 0xffa726, mobile: 0xec407a,
  merchant: 0xbdbdbd,
};

// 需预加载的精灵 key(= 全部职业;缺图静默回退色块)
const SPRITE_KEYS = Object.keys(SPRITE_STROKE);

interface NpcView {
  avatar: Phaser.GameObjects.Image | Phaser.GameObjects.Rectangle;
  dot: Phaser.GameObjects.Arc;     // 状态色点(精灵模式下用它表 state)
  label: Phaser.GameObjects.Text;
  usesSprite: boolean;
}

export type HoverCallback = (npcId: string | null, screen: { x: number; y: number } | null) => void;

export class TownScene extends Phaser.Scene {
  private npcs = new Map<string, NpcView>();
  private bell?: Phaser.GameObjects.Text;
  private lastMergeEid = 0;
  private hitlPulse?: Phaser.GameObjects.Rectangle;
  private hoverCb: HoverCallback = () => {};
  constructor() { super("Town"); }

  setHoverCallback(cb: HoverCallback) { this.hoverCb = cb; }

  preload() {
    // 缺图静默(回退色块),不刷 console
    this.load.on("loaderror", () => {});
    for (const key of SPRITE_KEYS) this.load.image(key, `/sprites/${key}.png`);
  }

  create() {
    // 区背景 + 标签
    for (const [key, z] of Object.entries(ZONE_POS)) {
      const bg = this.add.rectangle(z.x, z.y, z.w, z.h, 0xf5f0e6, 0.55)
        .setStrokeStyle(1, 0xcfc7b6);
      this.add.text(z.x - z.w / 2 + 6, z.y - z.h / 2 + 4, z.label,
        { fontSize: "12px", color: "#776" });
      if (key === "at_glass") this.hitlPulse = bg; // HITL 时此区闪烁
    }
    // 钟楼
    this.bell = this.add.text(40, 30, "🔔", { fontSize: "28px", color: "#a90" })
      .setAlpha(0.18);
    this.add.text(40, 60, "钟楼", { fontSize: "11px", color: "#aaa" }).setOrigin(0);
  }

  /** 用最新 snapshot 调和场景:create/move/recolor;触发 bell;HITL 闪烁。 */
  applySnapshot(snap: ViewSnapshot) {
    const occ: Record<Zone, number> = {} as Record<Zone, number>;
    let hitlActive = false;
    for (const [id, n] of Object.entries(snap.npcs)) {
      this._placeOrUpdate(id, n, occ);
      if (n.state === "hitl") hitlActive = true;
    }
    if (snap.last_merge && snap.last_merge.event_id !== this.lastMergeEid) {
      this.lastMergeEid = snap.last_merge.event_id;
      this._ringBell();
    }
    this._setHitlPulse(hitlActive);
  }

  private _placeOrUpdate(id: string, n: NpcSnapshot, occ: Record<Zone, number>) {
    const z = ZONE_POS[n.zone];
    const idx = (occ[n.zone] = (occ[n.zone] ?? 0));
    occ[n.zone] = idx + 1;
    // 同区横向排开,每行最多 5 个
    const col = idx % 5, row = Math.floor(idx / 5);
    const x = z.x - z.w / 2 + 28 + col * 44;
    const y = z.y - 2 + row * 38;
    const color = STATE_COLOR[n.state];
    const stroke = SPRITE_STROKE[n.sprite_key] ?? 0x555555;
    const hasSprite = this.textures.exists(n.sprite_key);

    let v = this.npcs.get(id);
    if (!v) {
      const avatar = hasSprite
        ? this.add.image(x, y, n.sprite_key).setDisplaySize(40, 40)
        : this.add.rectangle(x, y, 28, 24, color).setStrokeStyle(2, stroke);
      const dot = this.add.circle(x + 18, y - 16, 5, color).setStrokeStyle(1, 0xffffff);
      const label = this.add.text(x - 18, y + 20, id, { fontSize: "9px", color: "#444" });
      // 悬停:通知 React 端展示 think 浮窗(ADR-002 对人全透明)
      avatar.setInteractive({ useHandCursor: true });
      avatar.on("pointerover", () => this.hoverCb(id, { x: avatar.x, y: avatar.y }));
      avatar.on("pointerout", () => this.hoverCb(null, null));
      v = { avatar, dot, label, usesSprite: hasSprite };
      this.npcs.set(id, v);
    } else {
      this.tweens.add({ targets: v.avatar, x, y, duration: 280, ease: "Sine.easeInOut" });
      this.tweens.add({ targets: v.dot, x: x + 18, y: y - 16, duration: 280 });
      this.tweens.add({ targets: v.label, x: x - 18, y: y + 20, duration: 280 });
      if (!v.usesSprite) {
        (v.avatar as Phaser.GameObjects.Rectangle).setFillStyle(color).setStrokeStyle(2, stroke);
      }
    }
    v.dot.setFillStyle(color);                                 // 状态色点(始终表 state)
    v.avatar.setAlpha(n.state === "thinking" ? 0.8 : 1);       // thinking 半透明
  }

  private _ringBell() {
    if (!this.bell) return;
    this.tweens.add({
      targets: this.bell, alpha: 1, duration: 200, yoyo: true, hold: 600,
      onComplete: () => this.bell?.setAlpha(0.18),
    });
  }

  private _hitlTween?: Phaser.Tweens.Tween;
  private _setHitlPulse(active: boolean) {
    if (!this.hitlPulse) return;
    if (active && !this._hitlTween) {
      this.hitlPulse.setFillStyle(0xffe0e0, 0.85);
      this._hitlTween = this.tweens.add({
        targets: this.hitlPulse, alpha: { from: 0.85, to: 0.35 },
        duration: 500, yoyo: true, repeat: -1, ease: "Sine.easeInOut",
      });
    } else if (!active && this._hitlTween) {
      this._hitlTween.stop(); this._hitlTween = undefined;
      this.hitlPulse.setFillStyle(0xf5f0e6, 0.55).setAlpha(1);
    }
  }
}

export function createTown(parent: HTMLElement): { game: Phaser.Game; scene: TownScene } {
  const scene = new TownScene();
  const game = new Phaser.Game({
    type: Phaser.AUTO, parent, width: W, height: H,
    backgroundColor: "#fbf8f1", pixelArt: true, scene,  // pixelArt:精灵放大不模糊
  });
  return { game, scene };
}
