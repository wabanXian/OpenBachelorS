# 干员养成 API 审计（2026-10-17）

## 本轮已实现

`POST /charBuild/upgradeChar`

- 请求形状：`charInstId: int`、`expMats: [{id: string, count: int}]`。
- 作战记录经验值来自 `item_table.json.expItems`。
- 当前精英阶段的逐级经验和龙门币成本来自
  `gamedata_const.json.characterExpMap` 与 `characterUpgradeCostMap`。
- 等级上限来自 `character_table.json.chars[charId].phases[phase].maxLevel`。
- 先验证干员、阶段、等级、作战记录、龙门币和升级上限，再在同一个
  `player_data_decorator` 保存单元内扣除资产和更新 `troop.chars`。
- 若存档中已经存在 `UpgradeChar` 类型见习任务（包括 `guide_1`），成功强化后推进一次；
  本接口不会凭空创建尚未由任务链开放的任务。
- `extra.json.last_char_upgrade` 保存最近一次请求摘要及结算后状态。完全相同的网络重放在
  玩家状态仍等于上次结算后状态时作为幂等重放忽略。

## 协议证据与限制

已静态观察到当前 CN 客户端
`Arknights_Data/il2cpp_data/Metadata/global-metadata.dat` 在文件偏移
`2776347` 附近连续包含 `UpgradeCharRequest`、`expMats`、`ExpMat`、
`UpgradeCharResponse`。这确认了请求类型和 `expMats` 字段；`ExpMat` 的
`id/count` 形状仍需真实 HTTP 请求日志最终确认。`GameAssembly.dll` SHA-256 为
`583b04ff4d1b1a4b35eaf09d77d936408c25447c9e345ef0463d814b6a123fc5`。

资源只给出每一级完整 EXP 与完整龙门币成本。当前对半级经验采用“完整成本按该级经验比例向上取整”；
这是资源驱动推导，不是已经由真实服务端响应证明的舍入边界。开启现有 HTTP DEBUG 后，客户端首次调用会
记录脱敏请求体和 `playerDataDelta`，应据此核对字段及边界。

协议未观察到客户端请求 ID。最近一次状态摘要可以防止断线后的相同请求重放，但无法完美区分“网络重试”
与玩家立即再次提交完全相同的强化选择；这是协议本身缺少幂等键时的固有限制。

## `/charBuild` 路由清单

| 路由 | 状态 | 说明 |
| --- | --- | --- |
| `/charBuild/upgradeChar` | 已实现，待真实客户端确认 | 本轮资源驱动实现 |
| `/charBuild/setDefaultSkill` | 已有 | 未在本轮重验协议校验 |
| `/charBuild/setEquipment` | 已有 | 未在本轮重验解锁校验 |
| `/charBuild/setCharVoiceLan` | 已有 | 已注册 |
| `/charBuild/batchSetCharVoiceLan` | 已有 | 已注册 |
| `/charBuild/changeCharSkin` | 已有 | 已注册 |
| `/charBuild/changeCharTemplate` | 已有 | 已注册 |
| `/charBuild/changeSkinSpState` | 已有 | 已注册 |
| `/charBuild/addonStage/battleStart` | 已有占位 | 不等同普通关卡结算 |
| `/charBuild/addonStage/battleFinish` | 已有占位 | 只记录战斗日志并返回空首通奖励 |
| `/charBuild/evolveChar` | 缺失 | 新手期常规精英化必经；请求/响应和材料扣除尚未取证 |
| `/charBuild/upgradeSkill` | 缺失 | `guide_2` 依赖；请求结构尚未取证 |
| `/charBuild/boostPotential` | 缺失 | 潜能提升；请求结构尚未取证 |
| `/charBuild/upgradeSpecialization` | 缺失 | 专精开始；涉及计时状态 |
| `/charBuild/completeUpgradeSpecialization` | 缺失 | 专精完成；涉及计时状态 |
| `/charBuild/unlockEquipment` | 缺失 | 模组解锁；条件和消耗尚未实现 |
| `/charBuild/upgradeEquipment` | 缺失 | 模组升级；条件和消耗尚未实现 |
| `/charBuild/addonStory/unlock` | 缺失 | 模组故事相关 |
| `/charBuild/getSpCharMissionReward` | 缺失 | 特殊干员任务奖励 |

参考项目中出现但本仓库同样缺失的还有 `lockChar`、`sellChar`、
`evolveCharUseItem`、`upgradeCharLevelMaxUseItem` 和
`upgradeSpecializedSkillUseItem`。参考项目对应 `upgradeChar` 仅返回 HTTP 202，
没有可复用的资产或升级实现，因此不能作为正确协议依据。

## 新手期 API 缺口优先级

静态路由审计显示，战斗、剧情、教学招募、编队、单项/批量任务领奖目前已有路由；
养成链中最先会再次造成 404 的是 `/charBuild/upgradeSkill`、
`/charBuild/evolveChar` 和 `/charBuild/boostPotential`。这些接口没有在本轮返回假的成功结果，
应分别取得真实请求体后再接入资源表消耗与任务推进。
