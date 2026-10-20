# 干员养成 API 审计（2026-10-20）

## 已实现

### `POST /charBuild/upgradeChar`

- 请求：`charInstId`、`expMats[{id,count}]`。
- 作战记录经验来自 `item_table.json.expItems`；等级经验、龙门币曲线来自 `gamedata_const.json.characterExpMap/characterUpgradeCostMap`。
- 校验精英阶段等级上限、材料、龙门币，并更新 `UpgradeChar` 任务。

### `POST /charBuild/evolveChar`

- 请求：`charInstId`、`destEvolvePhase`；仅允许 E0→E1、E1→E2。
- 等级上限、材料来自 `character_table.json`，龙门币来自 `gamedata_const.json.evolveGoldCost`。
- 晋升后回到 1 级、0 经验，解锁对应技能；内置立绘资格依据 `skin_table.json.buildinEvolveMap/buildinPatchMap` 更新，不覆盖玩家主动装备的合法皮肤。

### `POST /charBuild/upgradeSkill`

- 请求：`charInstId`、`targetLevel`；普通技能每次只升一级，最高 7。
- 条件和材料来自 `character_table.json.allSkillLvlup`。
- 只更新 `mainSkillLvl`，不改任何技能的 `specializeLevel`。

### `POST /charBuild/boostPotential`

- PC 类型证据：`BoostPotentialRequest(charInstId,itemId,targetRank)`，响应额外包含 `resultOneSuc`。
- 只允许相邻潜能提升，最高 `potentialRank=5`。
- 专属信物按 1 枚消耗；职业通用信物必须匹配职业与稀有度并按 4 枚消耗。物品映射来自 `item_table.json.potentialItems`。
- 失败不扣库存；成功更新 `potentialRank` 和 `BoostPotential` 任务。最近一次完全相同的请求幂等返回，不会再次扣除信物。

### `POST /charBuild/upgradeSpecialization`

- PC 类型证据：`UpgradeSpecializationRequest(charInstId,skillIndex,targetLevel)`。
- 校验 E2、普通技能 7 级、技能索引和解锁条件；M0→M1→M2→M3 只能逐级开始。
- 材料、基础训练时间来自对应技能 `levelUpCostCond`；训练室等级上限来自 `building_data.json.trainingData.phases`。
- 开始时扣材料，并原子写入技能 `completeUpgradeTime`、训练室 trainer/trainee、槽位与基建干员占用状态。

### `POST /charBuild/completeUpgradeSpecialization`

- PC 类型证据：空的 `CompleteUpgradeSpecializationRequest`。
- 只完成当前训练室任务；到时前拒绝。完成后仅提升目标技能的 `specializeLevel`，清理训练占用，不修改 `mainSkillLvl`。
- 已完成请求重复发送不会再次提升或发放资源。

### 模组核心 API

- `POST /charBuild/unlockEquipment`：请求为 `charInstId/templateId/equipId`。根据 `uniequip_table.json` 校验归属、E2/等级、信赖、模组任务和材料；只创建 1 级模组。
- `POST /charBuild/upgradeEquipment`：增加 `targetLevel`，只允许逐级升级；每级材料、信赖门槛来自资源。
- `POST /charBuild/setEquipment`：只能装备目标模板已解锁的兼容模组，也允许按协议卸下；不再接受任意 `equipId`。
- 普通干员与多模板干员分别在自身 loadout 的 `equip/currentEquip` 中维护状态。

## 明确未伪造的部分

- 模组任务不会自动完成。当前解锁只读取真实写入的 `equipment.missions[*].value/target`；`EquipmentEventTotal`、`EquipmentEventStageMore` 等特殊条件尚未接入通用战斗验证。
- `/charBuild/addonStage/battleStart` 与 `battleFinish` 原先是假成功占位；现明确返回 `addon_stage_settlement_not_verified`。在特殊战斗条件、会话和幂等协议确认前不会伪造完成。
- `/charBuild/addonStory/unlock` 尚无足以安全实现的当前 PC 请求及存档证据。
- 专精训练加速、协助者和加速道具流程未实现；当前使用资源中的基础训练时间。

## 兼容与边界

- 全部接口沿用 `player_data_decorator`、OverlayJson 差量和现有存档事务；所有校验在扣费前完成。
- 不会在登录时修改现有干员、赠送信物或材料、开放训练室或解锁模组。
- HTTP 重放没有通用请求 ID。潜能与专精保存窄范围操作日志；模组因状态单调变化而在重放时安全拒绝，不会二次扣费。
- `setDefaultSkill`、`changeCharSkin`、`changeCharTemplate` 保留原行为，本轮只补充模组 loadout 合法性。

## 协议与资源依据

- PC 类型：`OpenBachelorC/dump/pc/20261019_144647_unknown/dump.cs`。
- 资源：`character_table.json`、`item_table.json`、`gamedata_const.json`、`skin_table.json`、`char_patch_table.json`、`building_data.json`、`uniequip_table.json`。
- 自动化测试覆盖资源扣除、非法条件、连续提升、计时、重放以及普通/多模板存档结构。
- PC 客户端请求字段与 UI 展示仍需实机抽样确认；自动化测试通过不等于客户端验收通过。
-

## 2026-10-21 状态分级补充

### 技能专精

- **代码已实现**：`upgradeSpecialization(charInstId, skillIndex,targetLevel)` 与空请求体的 `completeUpgradeSpecialization`；按 GameData 扣材料、校验训练室等级并使用真实计时。
- **自动化测试通过**：M0→M1→M2→M3、提前完成拒绝、请求重放、重登持久化、完成后 `trainee.state=WAITING(3)`，以及通过 `/building/assignChar` 更换学员并释放旧占用。
- **PC 协议证据**：当前客户端 `PlayerBuildingTraineeState` 为 `EMPTY=0/TRAINING=1/OUTOFDATE=2/WAITING=3`；完成响应为纯 `PlayerDeltaResponse`，训练界面具有独立完成动画和成功界面。因此完成后保留 WAITING，而不是伪装成 EMPTY。
- **Windows PC 实机验证**：尚未完成。训练协助者、基建技能加速、立即完成道具不在本轮支持范围。

### 模组任务、解锁与装备

- **代码已实现**：登录时仅为达到模组任务开放等级的已拥有干员补齐 `{value,target}` 记录，不赠送进度；成功战斗依据解码后的 `charStats`、`skillTrigStats` 与 `extraBattleInfo` 推进可验证任务。
- **自动化测试通过**：真实部署证据、累计条件、失败/中断不推进、战斗结算原子写入、重复结算幂等、重登持久化，以及已有解锁、升级、装备/卸下、跨干员拒绝测试。
- **PC 协议证据**：客户端存档类型 `PlayerEquipMission` 只有 `value/target`；GameData `UniEquipMissionData` 提供 `template/paramList/uniEquipId/jumpStageId`。真实 PC 战斗记录已观察到 `SPAWN/DEAD`、技能触发次数和精确的 `extraBattleInfo` 条件键。
- **Windows PC 实机验证**：尚未完成。未被真实战斗证据覆盖的模板不会自动完成，详见 `doc/module_mission_audit.md`。

### 非本轮系统

- `addonStage/battleStart`、`addonStage/battleFinish`、`addonStory/unlock` 属于干员密录/悖论模拟相关流程，不作为模组任务的替代入口；未经协议确认仍明确拒绝，绝不返回假成功。
