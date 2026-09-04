# 需求：CreateNewExtern 插件 · 标高偏移继承建模（BZOffset / WTopZ）

版本：v4（2026-09-04，BZOffset 基准改为"偏移绝对值最小的层标高"，平手取层顶）
提出方：Python 转换器侧（E:\ydb2revit_exe）
接收方：CreateNewExtern 插件维护（源码 `E:\revit-external-tool2.git`，
部署 `C:\ProgramData\Autodesk\Revit\Addins\2018\CreateNewExtern.dll`）

## 1. 设计定案（与 v1 的差异）

按用户决策（2026-09-04）：**ydb 提取时就把梁/墙的标高偏移数值提取进数据，
插件端继承建模**——不再采用"构件标高补集"（补集标高会插进楼层内部，
与插件"墙高=上一条标高差"逻辑冲突，曾致 2F 全层墙矮 1500，已撤销）。

Python 侧**已落地**的数据契约（tbl3 已回到"楼层底∪楼层顶"，不再有层间标高）：

| 表 | 末尾追加列 | 语义 |
|---|---|---|
| `tbl1` | `BZOffset REAL`、`BZOffset2 REAL`（mm，带符号） | 梁**起点/终点**相对**偏移绝对值最小的层标高**（本层底或层顶，平手取层顶；基准由起端 Z 判定，两端同基准）的偏移；普通梁 = 0（即挂本层标高）；降标高梁如颛桥 2F 挂层顶 1900、偏移 −1500（169 根）。已全量校验：BStartZ − BZOffset 必落在 tbl3 某标高上（7557 根零违例） |
| `tbl4` | `WTopZ REAL`、`WTopZ2 REAL`（mm，绝对标高） | 墙**起端/终端真实顶标高** = 层顶 + tblWallSeg.HDiff1/HDiff2。平顶墙两列相等；斜顶墙两列不同（如颛桥 2F 一段 400→1900）。颛桥 2F 实测：9 段 400/400、1 段 400/1900、60 段 1900/1900 |

## 2. 插件端修改点

### 2.1 梁创建（SqliteDataToRevit 梁事务 / CombineBeam 流程）

现状：按梁的 Z 精确找同标高 Level → 层间梁（Z=400 无标高）被跳过。

改为**偏移继承**：

```csharp
// 列存在且可解析时：
double refStartZ = BStartZ - BZOffset;      // = 本层层顶标高（mm）
Level refLevel = FindLevelAtElevation(orderedLevels, refStartZ);
// 建梁挂 refLevel，并设 Revit 实例参数（mm→ft /304.8）：
//   STRUCTURAL_BEAM_END0_ELEVATION（起点标高偏移） = BZOffset
//   STRUCTURAL_BEAM_END1_ELEVATION（终点标高偏移） = BZOffset2
// 无 BZOffset/BZOffset2 列（旧库）→ 回退现行为（按 Z 找 Level）。
```

普通梁 offset=0 → 行为与现状完全一致；层间梁落在层顶 Level 上、
用偏移参数降到真实标高（Revit 原生"起点/终点标高偏移"语义）。

### 2.2 墙创建（SqliteDataToRevit.cs 墙事务，约 481-489 行）

现状：`wallHeight = 上一条标高 − 底标高`。

改为（`WTopZ` 列存在且 > WStartZ 时）：

```csharp
if (WTopZ 与 WTopZ2 相等)
    wallHeight = (WTopZ - WStartZ) / 304.8;      // 平顶：显式高度
else
    // 斜顶墙：建议取两端较高者为墙高 + 编辑墙轮廓成斜线；
    // 或按项目约定取较低者并记录差异。两端值均已提供。
// 无 WTopZ/WTopZ2 列（旧库）→ 回退"上一条标高"逻辑。
```

墙定位线仍两端取 `WStartZ`（底平面），`WTopZ` 只用于高度。
此项在补集撤销后暂非必需（墙高已恢复正确），但**建议一并实现**：
它是将来传"每面墙不同顶标高"的唯一通道。

### 2.3 WallImportData.cs

`WallImportRecord` 增加 `ExplicitTopZ`（来自 WTopZ，可空）；梁侧读取
`BZOffset/BZOffset2`（可空 double?）。列不存在 → null → 走回退逻辑。

## 3. 上线顺序

| 步骤 | 方 | 动作 | 状态 |
|---|---|---|---|
| 0 | Python | 撤销补集；tbl1 加 BZOffset/BZOffset2；tbl4 加 WTopZ | ✅ 已上线（2026-09-04） |
| 1 | **插件端** | §2.1 梁偏移继承（必做）+ §2.2 墙 WTopZ（建议同做） | 待实施 |
| 2 | 联调 | 颛桥重导：墙高按 WTopZ 正确（含 9 降 1 斜）；层间梁以偏移方式出现（梁顶 400） | — |

顺序安全：Python 新列对未升级的旧插件无影响（不读即忽略）；
tbl3 已无层间标高，旧插件墙高立即恢复正确。

## 4. 验收标准

1. 颛桥重导（新插件）：2F 墙高符合 ydb 墙顶调整——60 段 5400（顶 1900）、9 段 3900（顶 400）、1 段斜顶（400→1900）；
2. Z=400 的 329 根层间梁全部出现：挂层顶 Level（1900），起点/终点标高偏移
   = −1500mm，梁顶实测标高 400；
3. 普通梁（offset=0）与现状逐根一致；
4. 旧格式中间库（无 BZOffset/WTopZ 列）导入行为与升级前完全一致；
5. tbl3 永不再出现层间标高（RFn 仅用于楼层顶脱接，如 RF2@16795）。

## 5. 排查与决策记录（背景）

* 层间梁缺失根因、方案A（补集）及墙高截断副作用：见
  `handoff_doc/handoff-Python端-tbl3标高集合多塔缺口.md` §7 及桌面
  《技术说明-20260903-梁缺失方案A与PEC截面主子表矛盾修复.md》；
* v1（仅 WTopZ）文档已被本 v2 取代：偏移继承让层间梁不再需要补集标高，
  墙高冲突从根上消除。
