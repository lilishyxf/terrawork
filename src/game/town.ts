// M3-4/M3.6 像素小镇渲染(Phaser 3)。
// 按 ADR-020 把 NPC 放进 7 个语义区,事件来时在区间走动。
// M3.6:有 PNG 精灵则用精灵(public/sprites/<sprite_key>.png),缺图回退色块;
// NPC 当前 state 由角上的状态色点表示(精灵=职业身份静态,状态=色点)。
import Phaser from "phaser";
import type { NpcSnapshot, NpcState, Zone, ViewSnapshot } from "./protocol/projection";
import FRAME_META from "./sprite-frames.json";

type FrameMeta = Record<string, { frameWidth: number; frameHeight: number; frames: number }>;
const META = FRAME_META as FrameMeta;
const DRAW_H = 52; // 精灵目标显示高(按帧宽高比定宽,不压扁)
const WALK_END = 13; // Terraria 帧布局:1-13 走路,14+ 招手手势(按住鼠标才播)

const W = 880, H = 560;

// 语义区中心位置(相对画布)。同区多 NPC 自动错位避叠。
const ZONE_POS: Record<Zone, { x: number; y: number; w: number; h: number; label: string }> = {
  at_glass:    { x: 440, y: 26,  w: 320, h: 28,  label: "屏幕前(HITL)" },
  lobby:       { x: 440, y: 130, w: 300, h: 140, label: "大堂(向导)" },   // 变大、居中:指挥中枢
  verify_room: { x: 150, y: 340, w: 240, h: 150, label: "验证间" },        // 填左
  workshop:    { x: 450, y: 340, w: 290, h: 150, label: "工坊小屋" },
  review_room: { x: 740, y: 340, w: 210, h: 150, label: "审查间" },         // 靠右
  review_door: { x: 612, y: 318, w: 56,  h: 130, label: "候审" },           // 不画框:候审 NPC 站审查间门口
  yard:        { x: 440, y: 505, w: 840, h: 80,  label: "院子(idle)" },     // 全宽底部
};
// 画背景框的区(at_glass/各室/院子);review_door 不画框,只作站位
const DRAWN_ZONES: Zone[] = ["at_glass", "lobby", "verify_room", "workshop", "review_room", "yard"];
// 可配室内图的区:public/rooms/<zone>.png(缺图回退半透明色框)
const ROOM_ZONES: Zone[] = ["lobby", "verify_room", "workshop", "review_room", "yard"];

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
  avatar: Phaser.GameObjects.Sprite | Phaser.GameObjects.Rectangle;
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
    this.load.on("loaderror", () => {});  // 缺图静默 → 回退
    for (const key of SPRITE_KEYS) {
      const m = META[key];
      if (m) this.load.spritesheet(key, `/sprites/${key}.png`,
                                   { frameWidth: m.frameWidth, frameHeight: m.frameHeight });
      else this.load.image(key, `/sprites/${key}.png`);  // 无帧元数据 → 当单帧
    }
    // 场景图(可选,缺图回退):全局背景图 + 整屋地板底纹 + 各室内图
    this.load.image("__bg", encodeURI("/rooms/背景.png"));
    this.load.image("__floor", "/tiles/floor.png");
    for (const z of ROOM_ZONES) this.load.image(`room_${z}`, `/rooms/${z}.png`);
  }

  create() {
    // Terraria 帧布局:0=站立,1-13=走路,14+=说话/招手手势。
    // 走路循环只用 1-13(不含举手);14+ 的招手帧改为"按住鼠标才播"(见 _placeOrUpdate)。
    for (const [key, m] of Object.entries(META)) {
      if (!this.textures.exists(key) || m.frames < 2) continue;
      const walkEnd = Math.min(WALK_END, m.frames - 1);
      this.anims.create({
        key: `${key}_walk`,
        frames: this.anims.generateFrameNumbers(key, { start: 1, end: walkEnd }),
        frameRate: 8, repeat: -1,
      });
      if (m.frames - 1 >= WALK_END + 1) {  // 有招手帧才建 talk(按住时循环手势)
        this.anims.create({
          key: `${key}_talk`,
          frames: this.anims.generateFrameNumbers(key, { start: WALK_END + 1, end: m.frames - 1 }),
          frameRate: 8, repeat: -1,
        });
      }
    }
    // 全局背景图(最底层,cover 铺满画布;有则盖过纯色底)
    if (this.textures.exists("__bg")) {
      const bg = this.add.image(W / 2, H / 2, "__bg").setDepth(-20);
      const s = this.textures.get("__bg").getSourceImage() as HTMLImageElement;
      bg.setScale(Math.max(W / s.width, H / s.height));
    }
    // 整屋地板底纹(有 /tiles/floor.png 用图平铺,缺图用配置纯色底)
    if (this.textures.exists("__floor")) {
      this.add.tileSprite(W / 2, H / 2, W, H, "__floor").setDepth(-10);
    }
    // 区:有室内图(/rooms/<zone>.png)则铺图,缺图回退半透明色框;review_door 不画框
    for (const key of DRAWN_ZONES) {
      const z = ZONE_POS[key];
      const roomKey = `room_${key}`;
      if (this.textures.exists(roomKey)) {
        // cover:等比放大铺满框,溢出部分用几何遮罩裁掉(不压扁原图)
        const img = this.add.image(z.x, z.y, roomKey).setDepth(-5);
        const src = this.textures.get(roomKey).getSourceImage() as HTMLImageElement;
        img.setScale(Math.max(z.w / src.width, z.h / src.height));
        const mask = this.make.graphics({}).fillStyle(0xffffff).fillRect(z.x - z.w / 2, z.y - z.h / 2, z.w, z.h);
        img.setMask(mask.createGeometryMask());
        this.add.rectangle(z.x, z.y, z.w, z.h).setStrokeStyle(2, 0x8a7a55).setDepth(-4);
      } else {
        const bg = this.add.rectangle(z.x, z.y, z.w, z.h, 0xf5f0e6, 0.5).setStrokeStyle(1, 0xcfc7b6);
        if (key === "at_glass") this.hitlPulse = bg;
      }
      this.add.text(z.x - z.w / 2 + 6, z.y - z.h / 2 + 4, z.label,
        { fontSize: "12px", color: "#5a4f3a", backgroundColor: "rgba(255,255,255,0.7)" }).setDepth(-3);
      if (key === "at_glass" && this.textures.exists(roomKey)) {
        this.hitlPulse = this.add.rectangle(z.x, z.y, z.w, z.h, 0xffe0e0, 0).setDepth(-3);
      }
    }
    // 钟楼(左上角地标:merge 成功时敲钟闪亮)
    this.bell = this.add.text(60, 70, "🔔", { fontSize: "44px" }).setOrigin(0.5).setAlpha(0.25);
    this.add.text(60, 102, "钟楼", { fontSize: "12px", color: "#5a4f3a" }).setOrigin(0.5);
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
    const x = z.x - z.w / 2 + 30 + col * 46;
    const y = z.y + 2 + row * 54;
    const color = STATE_COLOR[n.state];
    const stroke = SPRITE_STROKE[n.sprite_key] ?? 0x555555;
    const hasSprite = this.textures.exists(n.sprite_key);

    let v = this.npcs.get(id);
    if (!v) {
      let avatar: Phaser.GameObjects.Sprite | Phaser.GameObjects.Rectangle;
      if (hasSprite) {
        // frame 0 = 站立帧;按帧宽高比定尺寸,不压扁(走路动画待朋友给帧区间再开)
        const m = META[n.sprite_key];
        const sp = this.add.sprite(x, y, n.sprite_key, 0);
        if (m) sp.setDisplaySize(DRAW_H * (m.frameWidth / m.frameHeight), DRAW_H);
        else sp.setDisplaySize(40, 40);
        const key = n.sprite_key;
        if (this.anims.exists(`${key}_walk`)) sp.play(`${key}_walk`);  // 默认走路循环
        // 按住鼠标 → 招手手势帧(14+);松开(在/离精灵)→ 回走路
        const toTalk = () => { if (this.anims.exists(`${key}_talk`)) sp.play(`${key}_talk`); };
        const toWalk = () => { if (this.anims.exists(`${key}_walk`)) sp.play(`${key}_walk`); };
        sp.on("pointerdown", toTalk);
        sp.on("pointerup", toWalk);
        sp.on("pointerupoutside", toWalk);
        avatar = sp;
      } else {
        avatar = this.add.rectangle(x, y, 28, 24, color).setStrokeStyle(2, stroke);
      }
      const dot = this.add.circle(x + 15, y - 22, 5, color).setStrokeStyle(1, 0xffffff);
      const label = this.add.text(x - 18, y + 28, id, { fontSize: "9px", color: "#444" });
      // 悬停:通知 React 端展示 think 浮窗(ADR-002 对人全透明)
      avatar.setInteractive({ useHandCursor: true });
      avatar.on("pointerover", () => this.hoverCb(id, { x: avatar.x, y: avatar.y }));
      avatar.on("pointerout", () => this.hoverCb(null, null));
      v = { avatar, dot, label, usesSprite: hasSprite };
      this.npcs.set(id, v);
    } else {
      this.tweens.add({ targets: v.avatar, x, y, duration: 280, ease: "Sine.easeInOut" });
      this.tweens.add({ targets: v.dot, x: x + 15, y: y - 22, duration: 280 });
      this.tweens.add({ targets: v.label, x: x - 18, y: y + 28, duration: 280 });
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
