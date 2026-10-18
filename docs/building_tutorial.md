# 0-11 后的新手基地

`post_tutorial` 使用独立的新手基地基线；`sandbox` 继续使用原有全开放基地。

## 已确认的初始结构

新手基地的 `building.roomSlots` 初始只包含一级控制中枢 `slot_34`。未清理区域
来自客户端静态布局，不得把所有布局槽位伪造成 `level=0` 的房间。

结构区域清理完成后才进入 `roomSlots`。当前已确认：

- `slot_33`、`slot_27` 是 `ELEVATOR`；清理后为 `level=1,state=2`。
- `slot_26`、`slot_25`、`slot_24` 是后续功能房槽位；清理后、建造前的客户端
  协议尚未确认，因此服务端暂时拒绝该操作，不写入猜测数据。

登录和 `/building/sync` 会运行只读结构校验，输出
`building_structure_validation`/`structureIssues` DEBUG 诊断。校验器不会补造槽位，
也不会修复未知 `roomId`。

## 0-11 引导顺序

客户端故事脚本明确指定以下目标：

1. `0_building_init`：清理 `slot_33`，完成后 progress 1100 → 1101。
2. `1a_slot_clean`：清理 `slot_27`。
3. `1b_slot_clean`：清理 `slot_26`。
4. `1c_slot_clean`：清理 `slot_25`。
5. `1d_slot_clean`：清理 `slot_24`。
6. 随后依次建造发电站、制造站、贸易站并进驻干员。

如果 `slot_33` 清理已经持久化，但客户端在收到响应前中断，存档会出现
`progress=1100`、缺少 `obt/guide/l0-11/0_building_init` flag、但 `slot_33` 已清理
的部分提交状态。客户端此时仍执行 Part0，却找不到可清理的 `slot_33`。

先只读检查：

```powershell
uv run building-save inspect --username test123
```

确认命中上述唯一断点后，可显式修复。命令会先备份整个账号目录，只补写 Part0
完成 flag 和 `progress=1101`，不会回退房间或修改战斗、干员、任务和物品：

```powershell
uv run building-save repair-slot33-tutorial --username test123 --yes
```

## Part2-Part11 audit

The current resource version defines the three B1 OUTPUT slots as `slot_26`,
`slot_25`, and `slot_24`. Cleaning and construction are separate tutorial
steps. The server represents a cleaned but unbuilt OUTPUT slot as the client
enum pair `PlayerRoomSlotState.EMPTY` / `RoomType.NONE`:

```json
{"level": 0, "state": 0, "roomId": "NONE", "charInstIds": [], "completeConstructTime": -1}
```

Such a slot intentionally has no `building.rooms` runtime object. Building
POWER, MANUFACTURE, or TRADING changes it to `BUILT`, level 1 and creates the
matching runtime object atomically. The structure validator accepts exactly
this intermediate shape; other unknown room types remain diagnostic errors.

Current implementation coverage:

- Part2-Part4: all three OUTPUT cleans, idempotent resource grants, and narrow
  interrupted-clean recovery are implemented.
- Part5-Part7: resource-driven level-1 POWER/MANUFACTURE/TRADING construction
  is implemented, including material, labor and electricity validation.
- Part8/Part10: the existing `assignChar` endpoint validates capacity and owned
  building characters and persists assignments.
- Part9: `changeManufactureSolution` persists a resource-known formula, but its
  current-client request body still needs a real trace.
- Part11 entry: current `Player.log` proves the client reaches the Tutorial
  command but reports
  `Target has no graphic:pool_btn_building_trading_first_order`. The persisted
  level-1 TRADING runtime had `stock=[]`, so no first-order card/button could
  be created. Finishing `3c_station_trade` now atomically seeds exactly one
  level-1 `O_GOLD` order (2 Pure Gold to 1000 LMD). Its object shape is backed
  by a legal saved order sample; its contents are corroborated by current
  trading-post mechanics. It does not consume items, grant LMD, or advance
  Part11.
- Part11 delivery: after the order was rendered, the current client was
  observed calling `/building/deliveryOrder`. The route now accepts the
  protocol fields `slotId` and `orderId`, resolves all costs and gains from the
  persisted server order, validates inventory before mutation, removes that
  exact order, consumes its Pure Gold and credits its LMD atomically. A retry
  cannot pay twice because the order no longer exists. Request field names are
  included in the structured event log for current-client confirmation.

The EMPTY/NONE representation is supported by the type model and local current
resources, but still requires verification against the current PC client's
actual `cleanRoomSlot(slot_26)` response handling. Historical public dumps and
story scripts are corroborating evidence only, not the primary proof.

An account already stopped at progress 1111 is deliberately not changed at
login. After inspecting it, the missing order can be added explicitly with an
automatic backup:

```powershell
uv run building-save seed-tutorial-trade-order --username test123 --yes
```

The command requires progress 1111, the Part10 flag, a built staffed slot_24,
an empty trading stock, and an empty pending delta. It changes only that stock
and refuses retries.

该操作在 `pending_delta` 非空、slot 状态不符、progress 不符或 Part0 已完成时会拒绝。

## 既有基地迁移

没有基地架构版本的旧 `post_tutorial` 存档不会被静默重置。检查与显式迁移：

```powershell
uv run building-save inspect --username test123
uv run building-save migrate --username test123 --yes
```

备份位于 `save_backups/post_tutorial/building/`。恢复架构标记：

```powershell
uv run building-save restore --username test123 --archive <备份 zip> --yes
```

## 已知限制

功能房清理后的协议、贸易订单和制造产出尚缺真实客户端样本。服务端不会用空 200
或猜测数据绕过这些步骤。模拟测试通过不代表真实客户端引导已验证完成。
