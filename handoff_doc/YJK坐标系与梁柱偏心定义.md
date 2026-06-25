# YJK ydb 杆件坐标系与梁柱偏心定义（梳理稿）

> 用途：为"ydb → ydb转换数据库.db → Revit"管线中梁柱偏心的读取与生成提供定义基准。
> 来源：① 同济大学学报《基于工业基础类标准的参数化实体模型数据交互技术》(张其林等, 2021)；
> ② YJK 官方/社区对杆件局部坐标与梁偏心红箭头的约定；③ 本项目 `ydb转换数据库.db` 实测 + `SqliteDataToRevit.cs` 柱代码实证。

---

## 1. ydb 文件本质与表组织

- `.ydb` = 未加密 **SQLite** 数据库，可用 DB Browser / Python sqlite3 直接读改。
- 命名约定：`tblXxxSeg`（杆件布置/分段，如 `tblBeamSeg`、`tblColSeg`）、`tblXxxSect`（截面，如 `tblBeamSect`、`tblSubSectionSect`）、`tblGrid`/`tblJoint`（轴网/节点定位）、`tblStdFlr`（标准层）。
- 本项目并不直接读原始 `.ydb`，而是经外部 `ydb转换.exe` 转成 `ydb转换数据库.db`，落到 `tbl1`(梁)/`tbl2`(柱)/`tbl3`(标高)/`tbl4`(墙)。

## 2. 三层坐标系框架（同济论文 图 8）

杆件空间方位 = 三层坐标系逐级嵌套：

```
楼层坐标系 (storey)  原点(0,0,0)
   └─ 局部坐标系 (local)  原点(x0,y0,z0)   →  控制【构件插入位置】
        └─ 杆件坐标系 (bar) 原点(x1,y1,z1)  →  控制【偏心 + 朝向】
```

原文关键句：
> "局部坐标系定义了构件相对楼层坐标系的局部空间位置，控制构件的**插入位置**；杆件坐标系定义了杆件**截面与轴线相对于局部坐标系的位置，控制构件的偏心与朝向**。"

**结论：偏心 = 截面形心相对杆件轴线的偏移，表达在杆件坐标系的截面平面（2、3 轴）内。**

## 3. 杆件局部坐标系 1/2/3 轴（U1/U2/U3）方向

- **1 轴 (U1，轴向)**：起点 → 终点方向（沿杆件/拉伸方向）。
- **竖直杆件（柱）**：U2 = 整体坐标系 **Y 轴**方向；U3 右手法则。→ 截面在水平面，故柱偏心用 **(EccX, EccY) 两分量 + Rotation(绕竖轴转角)** 表达。
- **非竖直杆件（梁）**：U2 位于"U1 与整体 Z 轴所成的竖直平面内且 ⊥U1"（对水平梁 ≈ **竖直向上**）；U3 = U1×U2 = **水平面内、垂直梁轴**的方向。

水平梁局部轴示意（俯视/侧视）：

```
        U2 (≈竖直向上, 控制竖向偏心/标高)
        │
        │
        └────────► U1 (梁轴向, 起点→终点)
       ╱
      ╱  U3 (水平面内, 垂直梁轴 → "平面内Y偏心"方向)
```

## 4. 偏心定义

### 4.1 柱（已实现）
- ydb/`tbl2` 存独立列：`EccX(10)`、`EccY(11)`、`Rotation(12)`。
- `EccX/EccY` = 截面形心在柱截面局部 2/3 轴上的偏移；`Rotation` = 柱绕竖轴转角。

### 4.2 梁（YJK 官方约定）
> "梁红色箭头正方向为外侧……选择'梁中线偏心'时，**偏心值为正数时向红色箭头正方向偏移**。"

- **梁偏心 = 单个带符号标量**，沿水平面内垂直梁轴方向（U3）偏移；正值 → 指向"外侧"（红箭头正向）。
- 与柱不同：梁**不需要** EccX/EccY 双分量与 Rotation，朝向由端点轴向唯一确定。
- 竖向（U2 方向）的偏心/标高另算，对应 Revit 的"起点/终点标高偏移"。

## 5. 本项目实证与反推公式

### 5.1 实测表结构（`ydb转换数据库.db`）
```
tbl2(柱): CStartX..CEndZ, CSection, Tag, ID, RvtID, EccX(10), EccY(11), Rotation(12)
tbl1(梁): BStartX..BEndZ, BSection, Tag, ID, RvtID, BSConn(10), BEConn(11)   ← 无偏心列
CombineBeam: id, StartX..EndZ, ShapeValue, Info(GKL/GL)                       ← 无偏心列
```
样例柱：`EccX=0, EccY=200, Rotation=0`。

**根因（实测 `ydb转换.py` + 真实 .ydb）**：原始 `.ydb` 的 `tblBeamSeg` **本就含 `Ecc`（梁偏心）**，但转换器只 SELECT 了 `GridID,SectID,StdFlrID,ID,HDiff1,HDiff2`，丢弃了 `Ecc/Ecc2/Rotation`，导致 tbl1 没有偏心列。
- `tblBeamSeg` 实测列：`ID,No_,StdFlrID,SectID,GridID, Ecc, HDiff1,HDiff2, Rotation, JYDef, …, Ecc2`
- `tblColSeg` 实测列：`ID,No_,StdFlrID,SectID,JtID, EccX,EccY,Rotation, HDiffB, …`（柱的 `Ecc*/Rotation` 已被 `ydb转换.py:162` 正确 SELECT）
- 实测 `tblBeamSeg.Ecc`：`{0, +25, −23}` mm，带符号；`Ecc2` 与 `Ecc` 等值（疑为起/终点两端值）；梁 `Rotation=0`。
- 实测 `tblColSeg.Rotation = 90.0`（**度**）。

### 5.2 柱变换公式（`SqliteDataToRevit.cs:206-219`，含反射）
```
ΔX = EccX·cosθ + EccY·sinθ
ΔY = EccX·sinθ − EccY·cosθ        // θ=Rotation
```
代入样例(θ=0,EccY=200) → ΔX=0, ΔY=−200（正 EccY 在 θ=0 时偏 −Y）。
> ⚠️ 已证实的 bug：`tblColSeg.Rotation` 是**度**（实测 90.0）。此式 `Math.Cos(θ)` 却按**弧度**用，而 `:277` 旋转又 `(π·θ)/180` 按度——带转角的偏心柱会算错位（EccX=0,θ=90 时应 `ΔX=EccY`，实得 `ΔY=EccY·cos(90rad)≠0`）。梁改造时一并评估是否修此处。

### 5.3 梁的目标公式（反推，待 Python 与测试模型定符号）
设梁水平单位轴向 `t=(tx,ty)`，水平垂直方向 `n = U1×U2 = (ty, −tx)`：
```
梁起点、终点同量平移：
   ΔX = Ecc · ty
   ΔY = Ecc · (−tx)
```
- 这是"端点坐标 + 平面内 Y 偏心"的落地式（与柱思路一致，旋转基准换成梁自身方向，不依赖 Rotation 列）。
- **符号**（n 取 `(ty,−tx)` 还是 `(−ty,tx)`）= YJK 红箭头正向，需用一根带偏心的梁实测一次确定。

## 6. Revit 梁"平面内偏移"的定义与实现

### 6.1 Revit 梁的定位模型
- 梁(结构框架 FamilyInstance)由一条 **定位线 (Location Line / LocationCurve)** 驱动；截面相对定位线的位置由"**对正 Justification + 偏移值 Offset**"参数决定。
- 梁截面局部轴：沿梁轴为延伸方向；**y 轴 = 水平横向（平面内、垂直梁轴）**；**z 轴 = 竖向**。
  → Revit 的 **"y 轴偏移值"即平面内 Y 偏心**；"z 轴偏移值"是竖向偏心。这与 YJK 梁 U3(水平垂直轴)/U2(竖向) 一一对应。

### 6.2 关键实例参数（中文名 / BuiltInParameter / 代码引用）
- **YZ 轴对正**：统一(Uniform)/独立(Independent)。`BuiltInParameter.YZ_JUSTIFICATION`（`DateToSqlite.cs:1314` 在"统一"时 `Set(1)`——注意 1 的语义需核，Revit 常规 0=统一/1=独立）。
  - 统一：起终点共用一组 y/z 偏移；独立：起、终点各一组（"起点/终点 Y 轴偏移值"…）。
- **y 轴对正**(原点/左/中心/右) + **y 轴偏移值** → 平面内横移：`Y_JUSTIFICATION` / `Y_OFFSET_VALUE`；独立模式 `START_Y_OFFSET_VALUE`/`END_Y_OFFSET_VALUE`。
- **z 轴对正**(原点/顶/中心/底) + **z 轴偏移值** → 竖向：`Z_JUSTIFICATION`/`Z_OFFSET_VALUE`。
- **起点/终点标高偏移**(斜梁两端竖向)：`STRUCTURAL_BEAM_END0_ELEVATION`/`END1_ELEVATION`（`SqliteToRevit.cs:524-525`）。
- 注：精确 BuiltInParameter 拼写以 Revit API/实测为准；上列"统一/独立、各对正项"均为本项目代码已出现者。

### 6.3 在 Revit 实现"平面内 Y 偏心"的两条路径（核心）
- **(A) 几何法：平移定位线**（= 当前选定方案）。起终点按 `Ecc·(ty,−tx)` 平移，y 轴偏移值保持 0。
  - 优点：定位线即真实偏心中线，与转换器坐标一致，无需映射对正项；分析模型/连接自然落在偏心位置。
  - 代价：定位线不再落在 YJK 轴线/Revit 轴网线上（偏心被"烘焙"进几何）；事后看不到"偏多少"这个可编辑参数。
- **(B) 原生参数法：定位线留轴线，设 y 轴偏移值**。`Y 轴对正=原点`、`Y 轴偏移值=Ecc`（统一）。
  - 优点：定位线对齐轴网，偏心是可见可改参数，最贴近 YJK"梁中线偏心"语义；本项目旧回路 `SqliteToRevit.cs:359-363` 已用此法。
  - 代价：需正确映射符号与对正基准；统一/独立、起终点差异需处理。

### 6.4 符号与对正映射
- **A 方案**：不依赖对正项，只需把 `Ecc` 正负与 `n=(ty,−tx)` 指向用一根测试梁对齐一次。
- **B 方案**：还需确认 Revit "y 轴偏移值"正向（通常沿截面局部 +y）是否与 YJK 红箭头同向，不同则取反。

### 6.5 Revit 侧实现注意点（坑）
1. **族原点假设**：A 方案默认截面插入点(族原点)=形心；若梁族原点不在形心，会再叠一层偏移。需确认"生成模型族库"里梁族原点位置。
2. **统一 vs 独立**：`SetBeamAlignmentProperties`（`DateToSqlite.cs:1292-1327`）在"统一"时 `Set(YZ_JUSTIFICATION,1)` 并取"Y 轴偏移值"——梁生成逻辑要与此约定一致，避免两套对正打架。
3. **分析模型投影**：`SetAnalyticalBeamProperties` 用 `StickElementProjectionY.LocationLine`（`DateToSqlite.cs:1342-1345`），分析线跟随定位线——A 方案下分析线也随偏心移动，确认是否期望。
4. **梁端连接(join)**：偏心后梁端与柱/梁的自动连接可能改变端部裁剪；必要时 `DisallowJoinAtEnd`。
5. **斜梁标高偏移**：按当前要求暂不处理斜向 → 生成时不设 `起点/终点标高偏移`（移除 `SqliteDataToRevit.cs:390-396`），梁按参照标高水平生成。

## 7. 待 Python 源码确认的开放项

1. 转换器是否往 `tbl1` 写梁偏心？字段名（建议 `Ecc`，单值）/ 单位(mm) / 是否区分起终点。
2. 偏心符号约定（正值偏哪侧）。
3. `CombineBeam` 合并多段共线梁时，各段偏心若不同取谁（通常应一致）。
4. 是否同时去掉斜梁的"起点/终点标高偏移"处理（`SqliteDataToRevit.cs:390-396`）。

---

**Sources**
- 同济大学学报(自然科学版) 2021,49(2):195-203《基于工业基础类标准的参数化实体模型数据交互技术》张其林,舒沈睿,满延磊
- 技术邻 / 知乎：DB Browser for SQLite 读 ydb（表结构）
- 上海交大 BIM 中心：YJK→IFC 偏心输出
- 土木在线：YJK 杆件局部坐标 U1/U2/U3 与梁偏心红箭头约定
- 本项目 `ydb转换数据库.db` 实测、`SqliteDataToRevit.cs`
