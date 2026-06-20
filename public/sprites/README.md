# 像素精灵资产(M3.6)

把**原创** PNG 精灵放这里,文件名 = `<sprite_key>.png`(清单见 [manifest.json](manifest.json))。

- 前端启动时按 sprite_key 自动加载 `/sprites/<key>.png`;**有图用图,缺图回退色块**(不阻塞)。
- sprite_key 与 `roles/<name>.md` 的 `name` 一致(ADR-020 实例→sprite-key 间接层)。
- 规格:透明 PNG、建议 32×32、`pixelArt` 模式缩放不模糊。
- **IP**:原创职业形象;勿用泰拉瑞亚原素材/角色名/商标(PROJECT.md 禁止#9)。
- 精灵 = 职业身份(静态);**当前状态**由精灵角上的状态色点表示。

需要的 13 个 key:
`guide` `merchant` `frontend` `backend` `database` `desktop_shell` `ai_engineer`
`rapid_proto` `tech_writer` `mobile` `blaster` `tailor` `appsec`

放好后 `npm run dev`,小镇里对应 NPC 即从色块变成你的精灵。本目录(除 README/manifest)
里的 PNG **会入库**(它们是产品资产,非临时数据);如需保密可改 .gitignore。
