# 模组任务模板覆盖审计（GameData 78.2.0）

`res/excel/uniequip_table.json` 共包含 964 条模组任务、32 种模板。本实现只消费能够由当前 PC 战斗结算密文直接证明的字段；客户端描述文本不是结算证据。

## 已实现：10 种模板，608 条

| 模板 | 数量 | 服务端证据 |
|---|---:|---|
| EquipmentSkillCast | 138 | `skillTrigStats(charId, skillId)`，并要求该干员存在 `SPAWN` |
| EquipmentSkillCastStage | 81 | 指定关卡、星级、`SPAWN`、技能触发次数 |
| EquipmentSkillCastBattle | 71 | 成功战斗、`SPAWN`、单场技能触发阈值 |
| EquipmentCharKilled | 63 | `extraBattleInfo[KILL_COUNT,charId]` 与 `SPAWN` |
| EquipmentCharKilledStage | 65 | 指定关卡、星级、干员击杀数与 `SPAWN` |
| EquipmentBattleCharKilled | 13 | 成功战斗、单场干员击杀阈值与 `SPAWN` |
| EquipmentEventTotal | 26 | GameData 给出的精确 `extraBattleInfo` 键与 `SPAWN` |
| EquipmentEventStageMore | 114 | 指定关卡、星级、精确事件键与 `SPAWN` |
| EquipmentEventBattleMore | 10 | 成功战斗、单场精确事件阈值与 `SPAWN` |
| EquipmentSquadNoAnyDead | 27 | 指定关卡、星级、目标干员 `SPAWN`，且 `charStats` 无 `DEAD` |

所有进度只在服务端判定为成功的普通战斗中计划，并与关卡结算使用同一持久化会话和绝对 mutation。失败、撤退、中断、旧结算重放均不会重复累计。

## 暂不推进：22 种模板，356 条

下列模板会以 GameData 的真实目标值显示，但不会被自动完成：

- 伤害统计：`EquipmentDamageTotal`、`EquipmentDamageTotalWithToken`、`EquipmentDamageStage`、`EquipmentBattleCharDamage`、`EquipmentDamageTypeTotal`、`EquipmentDamageTypeStage`。
- 部署/召唤细节：`EquipmentDeployTotal`、`EquipmentDeployStage`、`EquipmentDeployCharOrder`、`EquipmentDeployCharAndKillCnt`、`EquipmentDeployOneNoEvac`、`EquipmentStageDeployCntAndSpec`。
- 编队约束：`EquipmentSquadNum`、`EquipmentSquadPro`、`EquipmentSquadProEx`、`EquipmentSquadProStage`、`EquipmentSquadPos`、`EquipmentSquadStar`、`EquipmentSquadStarStage`。
- 元素爆发：`EquipmentElementBurst`、`EquipmentElementBurstStage`。

原因分别是当前战斗证据缺少已确认的数据语义（部署先后、召唤物次数、完整伤害归属、元素爆发归属），或尚未完成对编队参数边界和助战计数规则的协议验证。即使描述看起来可以猜，也不会据此写入存档。

## 数据生命周期

- 当玩家实际拥有对应干员，且达到模组 `unlockEvolvePhase/unlockLevel` 后，登录协调器补齐任务行；已有 `value/target` 永不覆盖。
- 任务目标来自 `missionList[*].template/paramList`，不是 Python 中按任务 ID 硬编码。
- 达成任务后，既有 `/charBuild/unlockEquipment` 才允许按 `itemCost[1]` 解锁一级模组；二、三级继续使用各级 GameData 成本和信赖阈值。
