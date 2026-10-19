# 干员养成 API 审计（2026-10-19）

## 当前实现

### `POST /charBuild/upgradeChar`

- 请求：`charInstId: int`、`expMats: [{id: string, count: int}]`。
- 作战记录经验来自 `item_table.json.expItems`。
- 等级经验和龙门币消耗来自 `gamedata_const.json.characterExpMap` 与 `characterUpgradeCostMap`。
- 当前精英阶段等级上限来自 `character_table.json[charId].phases[phase].maxLevel`。
- 修改 `troop.chars`、库存和 `status.gold`，并驱动 `UpgradeChar` 任务事件。
- `extra.json.last_char_upgrade` 用于拦截最近一次完全相同请求的网络重放。

### `POST /charBuild/evolveChar`

- 当前 PC 客户端运行时类型 `Torappu.EvolveCharRequest` 已确认请求为
  `charInstId: int`、`destEvolvePhase: EvolvePhase`；响应 `EvolveCharResponse`
  仅继承 `PlayerDeltaResponse`。
- 只接受相邻阶段 E0→E1 或 E1→E2，且当前阶段必须满级、经验为 0。
- 目标阶段和材料来自 `character_table.json[charId].phases[dest].evolveCost`；
  龙门币来自 `gamedata_const.json.evolveGoldCost[rarity-1][currentPhase]`。
- 成功后目标阶段设为 1 级、0 经验；刷新当前形态技能解锁状态，但不开放其他尚未解锁的职业形态。
- 内置精英化皮肤来自 `skin_table.json.buildinEvolveMap/buildinPatchMap`。只有当前装备仍是上一阶段内置皮肤时才自动切换；玩家主动装备的合法时装不会被覆盖。
- 驱动资源定义的 `EvolveChar` 状态任务；最近一次相同请求通过 `extra.json.last_char_evolve` 幂等拦截。

### `POST /charBuild/upgradeSkill`

- 当前 PC 客户端运行时类型 `Torappu.UpgradeSkillRequest` 已确认请求为
  `charInstId: int`、`targetLevel: int`；响应 `UpgradeSkillResponse` 仅继承 `PlayerDeltaResponse`。
- 普通技能等级保存在 `troop.chars[*].mainSkillLvl`，一次只能提升一级，最高 7。
- 第 N→N+1 级条件和材料来自当前形态资源的 `allSkillLvlup[N-1].unlockCond/lvlUpCost`。
  当前资源的普通技能升级费用表没有龙门币项，因此服务端不会凭空扣除龙门币。
- 不修改各技能的 `specializeLevel`，并兼容技能列表位于当前 `tmpl` 的多形态干员。
- 驱动 `UpgradeSkill` 事件型与状态型任务；最近一次相同请求通过
  `extra.json.last_char_skill_upgrade` 幂等拦截。

## 事务与已知边界

- 两个新增接口均在首次存档修改前完成请求、阶段、资源、库存和 schema 校验；验证失败不会扣除材料或货币。
- 沿用 `player_data_decorator`、OverlayJson 差量与账号独立存储，不迁移或重建旧存档。
- 当前客户端请求没有幂等键。服务端只能安全识别“最近一次相同请求且结算后状态完全一致”的重放；跨多次其他养成操作后再次重放旧包无法被协议完美区分。
- 本轮不实现潜能、技能专精 M1～M3、模组、训练室、职业转换及养成直升道具。

## `/charBuild` 路由状态

| 路由 | 状态 | 说明 |
| --- | --- | --- |
| `/charBuild/upgradeChar` | 已实现 | 等级、经验、作战记录、龙门币、任务、持久化 |
| `/charBuild/evolveChar` | 已实现，待 PC 实机验收 | E0→E1、E1→E2、材料、技能解锁、内置皮肤、任务 |
| `/charBuild/upgradeSkill` | 已实现，待 PC 实机验收 | 普通技能 1→7；不含专精 |
| `/charBuild/setDefaultSkill` | 已有 | 现有实现；本轮未扩展协议校验 |
| `/charBuild/changeCharSkin` | 已有 | 现有实现；精英化不会覆盖主动时装 |
| `/charBuild/changeCharTemplate` | 已有 | 本轮不开放尚未解锁的职业模板 |
| `/charBuild/boostPotential` | 缺失 | 不在本轮范围 |
| `/charBuild/upgradeSpecialization` | 缺失 | 不在本轮范围 |
| `/charBuild/completeUpgradeSpecialization` | 缺失 | 不在本轮范围 |
| `/charBuild/unlockEquipment`、`upgradeEquipment` | 缺失 | 模组，不在本轮范围 |

## 验证依据

- 当前 PC 导出：`OpenBachelorC/dump/pc/20261019_144647_unknown/dump.cs`。
- 静态规则：当前客户端对应版本的 `character_table.json`、`gamedata_const.json`、
  `skin_table.json`、`char_patch_table.json`、`item_table.json`。
- 自动化覆盖普通干员与阿米娅多形态结构、E0→E1、E1→E2、1→2、6→7、
  条件及资源不足原子失败、任务、皮肤、技能解锁、专精不变、重放和重载持久化。
- 自动化与协议结构通过不等同于 PC UI 验收；首次实机操作仍需核对请求日志、
  `playerDataDelta` 和客户端最终显示。
