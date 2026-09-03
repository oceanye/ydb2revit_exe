# 需求：CreateNewExtern 插件 · 墙高显式化（WTopZ）与层间梁标高兼容

提出日期：2026-09-03
提出方：Python 转换器侧（E:\ydb2revit_exe）
接收方：CreateNewExtern 插件维护（源码仓库 `E:\revit-external-tool2.git`，部署于
`C:\ProgramData\Autodesk\Revit\Addins\2018\CreateNewExtern.dll`）
性质：**插件端代码修改需求**（Python 侧配合改数据契约，具体见 §4）

---

## 1. 问题背景（数据实证，2026-09-03）

### 1.1 现象链

1. 颛桥多塔模型存在 329 根**降标高（层间）梁**：2F 楼层顶 1900 + HDiff(−1500)
   → 梁顶实际 Z=400，不落在任何楼层底/顶上；
2. 插件建梁逻辑：按梁的 Z **精确找同标高 Level**（`FindLevelAtElevation`，1mm 容差）
   → tbl3 无 400 标高 → 这批梁创建失败被跳过（网格 152/154 等混凝土梁"缺失"）；
3. Python 侧"方案A"修复：tbl3 增加**构件标高补集**行（RF3@400）→ 梁能建了；
4. **副作用**：插件建墙逻辑是"墙高 = 底标高之上的**下一条标高**差"
   → RF3@400 插在 2F（底 −3500、顶 1900）中间 → **2F 全层墙从 5400 矮成 3900**。

### 1.2 冲突本质

同一个"标高表"被插件用于两种互相冲突的语义：

| 构件 | 需要的标高语义 | 与补集标高的关系 |
|---|---|---|
| 梁 | 精确匹配自身 Z 的标高 | **需要**层间补集标高（400） |
| 墙 | 底标高 + 到"楼层顶"的高度 | 补集标高一旦落在该层内部，墙高即被截断 |

数据层无解（任何 2F 内部的标高都会截断 2F 的墙），必须让墙高**不再依赖
标高排序**——即墙高显式化。

## 2. 修改对象（是什么插件、在哪个文件夹）

| 项 | 内容 |
|---|---|
| 插件 | **CreateNewExtern**（Revit 2018 外部插件，"结构模型→合并梁并生成模型"） |
| 源码仓库 | `E:\revit-external-tool2.git`（裸仓库，分支 main/dev-wangxinyu） |
| **修改文件** | `CreateNewExtern/SqliteDataToRevit.cs`（墙创建事务"创建墙"，约 451–547 行） |
| 关联文件 | `CreateNewExtern/WallImportData.cs`（tbl4 行读取记录类，需加一个字段） |
| 部署产物 | 编译后的 `CreateNewExtern.dll` → `C:\ProgramData\Autodesk\Revit\Addins\2018\` |

## 3. 具体改法（现有代码 → 建议代码）

### 3.1 现有代码（SqliteDataToRevit.cs，实证摘录）

```csharp
Level level = FindLevelAtElevation(orderedLevels, wallStartPoint.Z);   // 墙底标高
if (level == null) throw ...;

Level topLevel = orderedLevels.FirstOrDefault(
    candidate => candidate.Elevation > level.Elevation + 1e-6);       // ← 问题所在：
                                        // "上一条更高的标高"，补集标高会截断墙高
if (topLevel == null) throw ...;

double wallHeight = topLevel.Elevation - level.Elevation;             // 墙高=标高差
```

### 3.2 建议代码（墙高显式化，向后兼容）

**① 数据契约（Python 侧配合，见 §4）**：tbl4 末尾**追加一列** `WTopZ REAL`
（mm，该墙真实顶标高）。列只追加在末尾，不影响现有下标消费者。

**② WallImportData.cs**：`WallImportRecord` 增加可空属性（列不存在或为空时为 null）：

```csharp
public double? ExplicitTopZ { get; private set; }   // 来自新列 WTopZ，mm
```

读取处（`FromDataRow`）按"列存在且可解析"取值，旧库无此列 → null。

**③ SqliteDataToRevit.cs 墙高计算替换为**：

```csharp
double wallHeight;
if (wallRecord.ExplicitTopZ.HasValue &&
    wallRecord.ExplicitTopZ.Value > wallRecord.StartZ + 1e-6)
{
    // 新契约：墙高显式给定（WTopZ − WStartZ），与标高排序无关
    wallHeight = (wallRecord.ExplicitTopZ.Value - wallRecord.StartZ) / 304.8; // 英尺
}
else
{
    // 旧数据回退：沿用"上一条标高"逻辑（保持对旧中间库兼容）
    Level topLevel = orderedLevels.FirstOrDefault(
        candidate => candidate.Elevation > level.Elevation + 1e-6);
    if (topLevel == null) throw ...;
    wallHeight = topLevel.Elevation - level.Elevation;
}
```

注意：墙的**定位线**仍用 `WStartZ`（两端 Z 都取底标高，与现状一致）；
`WTopZ` 只用于高度，不参与定位线——避免产生斜线墙。

### 3.3 不改的部分

* 梁的创建逻辑（按 Z 找 Level）**不变**——层间梁继续靠补集标高挂接；
* 柱、楼板、合并梁等其余流程不变；
* tbl3 契约不变（仍可能含 RFn 补集行）。

## 4. Python 侧配合改动（由 ydb2revit_exe 侧实施）

1. tbl4 末尾追加 `WTopZ` 列：普通墙 = 楼层顶标高；后续如需传"每面墙不同顶"
   （如斜屋面下外圈墙参差），可从墙段 HDiff 类字段派生——本次先统一楼层顶；
2. 墙高显式化上线后，**恢复 tbl3 构件标高补集**（方案A，现已临时撤销）：
   层间梁（Z=400 的 329 根）重新获得标高，且不再影响墙高；
3. 契约变更将补记入 `handoff_doc/handoff-python-PEC墙提取与Revit建模.md`。

## 5. 上线顺序（关键）

| 步骤 | 方 | 动作 |
|---|---|---|
| 0（当前状态） | Python | 补集已撤销：墙高正确、329 根层间梁暂缺（接受的过渡态） |
| 1 | **插件端** | 按 §3 实现 WTopZ（含旧数据回退），编译部署 |
| 2 | Python | tbl4 追加 WTopZ（=楼层顶）→ 重转 |
| 3 | 联调 | 验证墙高不变；随后 Python 恢复补集（RF3@400 回归）→ 再验证层间梁出现且墙高仍正确 |

顺序不可颠倒：步骤 2 若先于步骤 1 上线，旧插件不读 WTopZ 无影响（安全）；
但补集（步骤 3）必须在墙高显式化生效后才能恢复。

## 6. 验收标准

1. 颛桥重导后：2F（底 −3500）墙高全部恢复 **5400**（顶 = 1900），
   与补集标高 RF3@400 是否存在**无关**；
2. Z=400 的 329 根层间梁全部出现（挂 RF3），梁顶标高 = 400；
3. 旧格式中间库（无 WTopZ 列）导入行为与现在完全一致（回退逻辑）；
4. 墙定位线仍位于底标高平面（无斜线墙）；
5. 柱、梁、板行为零变化。

## 7. 参考

* 墙高逻辑实证：`SqliteDataToRevit.cs`（orderedLevels.FirstOrDefault 片段）；
* tbl3 补集契约：`handoff_doc/handoff-Python端-tbl3标高集合多塔缺口.md` §7；
* 层间梁缺失排查与方案A论证：同目录 handoff 及桌面
  《技术说明-20260903-梁缺失方案A与PEC截面主子表矛盾修复.md》。
