# Handoff：Revit 端柱偏心、旋转与近期接口联动

日期：2026-09-07
发送方：`ydb2revit_exe` 数据转换端
接收方：CreateNewExtern / Revit 2018 插件开发
数据契约：`Upper.ContractVersion = UPPER_HANDOFF_V1`，`Upper.ColumnPlacementVersion = 1`

## 1. 本次交付结论

普通柱和独立 PEC 柱的 `EccX`、`EccY`、`Rotation` 可以可靠地从 YJK `tblColSeg` 传到中间库 `tbl2`。转换器不换轴、不换符号、不换单位，三个值原样传递。旋转单位明确为 **degree**，偏心单位为 **mm**。

2026-09-07 对仓库内两份真实上部结构样本逐行核对：共有 38 行同时出现“非零偏心 + 非零旋转”，源 `tblColSeg` 到目标 `tbl2` 为 0 处不一致，其中 12 行三个字段均非零。回归夹具另锁定 `(EccX,EccY,Rotation)=(150,-175,90)` 的原值传递。

墙端 PEC H 柱是明确例外：它仍占用 `tbl2` 行，但三个定位字段固定写 0，Revit 必须通过 `tbl4.WInfo.tbl2_column_refs` 识别并按墙关系定位。不能仅凭 `CSection` 是否以 `@PEC` 结尾判断是否为墙端柱，因为独立 PEC 柱同样带 `@PEC`。

## 2. 版本和能力门禁

新转换结果包含：

| metadata 键 | 值 | 用途 |
|---|---|---|
| `Upper.SchemaVersion` | `1` | 当前上部结构表模式版本 |
| `Upper.ContractVersion` | `UPPER_HANDOFF_V1` | 本文所述接口版本 |
| `Upper.ContractTables` | `tbl1,tbl2,tbl3,tbl4` | 本提取器拥有的表 |
| `Upper.CoordinateUnit` | `mm` | 坐标、长度、偏移单位 |
| `Upper.AngleUnit` | `degree` | 所有 Rotation 字段单位 |
| `Upper.ColumnPlacementVersion` | `1` | 柱定位公式版本 |
| `Upper.ColumnTopSemantics` | `TRUE_TOP_Z` | `CEndZ` 是真实柱顶绝对标高 |
| `Upper.ColumnWallEndRule` | `DERIVE_FROM_TBL4_WINFO` | 墙端 H 柱例外规则 |
| `Upper.Features` | 逗号分隔能力名 | 当前可用的尾部扩展 |
| `Upper.SourceFile` / `Upper.SourceSHA256` | 源路径 / SHA-256 | 追溯输入文件 |

建议插件先读 metadata，再按 SQLite 列名探测扩展列。旧中间库没有 `handoff_meta` 或没有 `Upper.*` 时，应进入旧版兼容流程；不应按固定 `SELECT *` 下标读取新增尾列。

## 3. tbl2 柱定位契约

`tbl2` 列顺序保持：

```text
0 CStartX   1 CStartY   2 CStartZ
3 CEndX     4 CEndY     5 CEndZ
6 CSection  7 Tag       8 ID       9 RvtID
10 EccX     11 EccY     12 Rotation
```

### 3.1 普通柱与独立 PEC 柱

设：

```text
θ = Rotation × π / 180
ΔX = EccX × cos(θ) + EccY × sin(θ)
ΔY = EccX × sin(θ) - EccY × cos(θ)
```

Revit 端必须按以下顺序执行：

1. 读取原始起、终点以及三个定位字段；把 mm 统一换算成 Revit 内部单位。
2. 同时平移柱轴线两端：`Pstart' = Pstart + (ΔX,ΔY,0)`，`Pend' = Pend + (ΔX,ΔY,0)`。
3. 在平移后的轴线创建柱实例。
4. 以平移后的竖向轴线为旋转轴，再旋转 `θ`。

不得把 degree 直接传给 `Math.Sin/Math.Cos`，也不得先绕原始未偏心轴旋转。`EccX/EccY` 为 0 时仍可执行同一流程。

验算点：

| EccX | EccY | Rotation | ΔX | ΔY |
|---:|---:|---:|---:|---:|
| 100 | 0 | 0° | 100 | 0 |
| 0 | 100 | 0° | 0 | -100 |
| 150 | -175 | 90° | -175 | 150 |
| 0 | 200 | 90° | 200 | 0 |

当前清单实际加载的 `CreateNewExtern.InformationEntryLifecycle.20260827.dll` 已能在柱定位路径中先把 degree 转为 radian，并采用上述公式；本次要求是把公式写入正式接口与回归测试，防止后续版本倒退。

### 3.2 墙端 PEC H 柱

先扫描所有非空 `tbl4.WInfo`，建立其中 `tbl2_column_refs.start/end/connected_main` 引用的 `tbl2.ID` 集合。被引用的 ID 才属于墙端关系柱。

- 这些行的 `EccX/EccY/Rotation` 固定为 0，不能走 3.1 的普通柱定位。
- Main 墙的 `start/end` 引用分别对应已按公共角点方向整理后的墙起、终端 H。
- Secondary 的 `connected_main` 只引用已有 Main 角点 H，禁止重复创建。
- H 的中心、方向和尺寸由 Main/Secondary 墙端点、墙方向、`WSection`、`CSection` 与 `WInfo.steel_configuration` 推导。
- 完整几何公式继续以 `handoff-python-PEC墙提取与Revit建模.md` 第 6 节为准。

## 4. CEndZ 与标高约束

`CStartZ`、`CEndZ` 均为 mm 绝对标高。当前 `CEndZ = 层顶 + tblJoint.HDiff`，语义已经从“默认层顶”升级为“真实柱顶”。因此 `CEndZ` 可能不与任一 Revit Level 精确相等。

插件端顶部约束应为：

1. 在候选 Level 中寻找与 `CEndZ` 距离最小者。
2. 距离相同取标高更高的 Level。
3. `顶部偏移 = CEndZ - 所选 Level.Elevation`，换算为 Revit 内部单位写入实例。
4. 不得因不存在 1 mm 内的精确 Level 而跳过或抛错。

底部仍使用 `CStartZ` 对应的层及底部偏移；创建后应核对柱几何顶端回算值与 `CEndZ`，允许误差 1 mm。

## 5. 近期扩展列

### 5.1 梁标高偏移

`tbl1.BZOffset/BZOffset2` 是梁起、终点相对同一参考 Level 的带符号 mm 偏移。参考标高可由 `BStartZ-BZOffset` 还原。插件应设置 Revit 起点/终点标高偏移；列缺失时走旧版行为。

### 5.2 墙顶标高

`tbl4.WTopZ/WTopZ2` 是墙起、终端真实顶绝对标高，已经包含墙段与节点的 HDiff。L 墙肢因方向规范化发生反向时，转换器同步交换两端顶标高；插件只需按输出端点使用。两值不同时应按斜顶轮廓处理并亮显人工核对，不能把两值静默压成同一高度。

### 5.3 弧梁提示

按 2026-09-07 决定，数据端保留现有 `tbl1.BIsArc` 启发式结果，不再扩展弧梁识别。Revit 端对 `BIsArc=1` 的梁保持分段、跳过直梁合并并亮显提示人工复核。该标记用于提示，不作为圆弧几何真值；`BIsArc=0` 也不能证明构件一定不是缓弧。

## 6. Revit 端必须完成的修改

| 优先级 | 修改 | 当前审计状态 |
|---|---|---|
| P0 | 把 3.1 的柱定位公式固化为测试，覆盖偏心与旋转同时非零 | 活动 DLL 行为正确，缺正式契约门禁 |
| P0 | `CEndZ` 改为最近 Level、同距取高层、写顶部偏移 | 活动 DLL 仍按约 1 mm 精确匹配，偏移柱顶可能失败 |
| P0 | 读取并应用 `BZOffset/BZOffset2` | 活动 DLL 中未发现字段引用 |
| P0 | 读取并应用 `WTopZ/WTopZ2`，斜顶亮显 | 活动 DLL 中未发现字段引用 |
| P1 | `BIsArc=1` 跳过合并并亮显 | 活动 DLL 中未发现字段引用；只需按现有标记提示 |
| P1 | 读取 `Upper.*` 版本与能力，记录 SourceSHA256 | 新数据端已提供 |
| P1 | 集成 `revit_addin/PecStiffenerCommand.cs` 的安全修订 | 本仓库仅有参考源码，正式插件工程不在本仓库 |

`PecStiffenerCommand.cs` 本次已修订：H 截面支持 `X/x/×/*`；数字 invariant 解析；只删除带本命令 ApplicationId/DataId 的 DirectShape；按 `Document.IsModifiable` 使用 Transaction/SubTransaction，异常回滚；墙方向仅比较 XY。该文件已使用 Revit 2018 API/UI 与部署目录的 `System.Data.SQLite.dll` 编译通过。首次升级后，旧版按中文名称创建且没有归属标记的钢板不会自动删除，应由实施人员在受控模型中一次性清理。

## 7. 验收矩阵

1. **组合偏心旋转**：输入 `(150,-175,90°)`，柱轴线平移 `(-175,+150) mm`，截面再绕新轴旋转 90°。
2. **零转角符号**：`(0,100,0°)` 的轴线沿全局 Y 负向移动 100 mm。
3. **独立 PEC**：未被任何 WInfo 引用、`CSection` 带 `@PEC` 的柱仍执行普通柱定位，不能被归零或按墙定位。
4. **墙端 PEC**：被 WInfo 引用的柱只创建一次，位置来自墙关系，三个 tbl2 定位值保持 0。
5. **真实柱顶**：`CEndZ` 位于两个 Level 之间时选最近层并写偏移；正好同距时选较高层；模型顶端误差 ≤1 mm。
6. **梁偏移**：`BStartZ-BZOffset` 能命中参考 Level，两个 Revit 端点偏移分别等于两列。
7. **墙顶**：平顶按显式高度，斜顶保留两端差并亮显。
8. **弧梁**：`BIsArc=1` 的段不参与直梁合并并亮显，日志数量与数据库一致；人工复核结果不反写成几何真值。
9. **旧库兼容**：没有 metadata/尾部扩展列的旧库仍可导入；缺列时记录采用了旧流程。
10. **追溯**：导入日志记录 `Upper.ContractVersion`、`Upper.SourceSHA256`、插件版本与数据库路径。

## 8. 部署审计基线

2026-09-07 实机检查：

- Revit 清单 `CreateNewExtern.addin` 实际加载 `C:\ProgramData\Autodesk\Revit\Addins\2018\CreateNewExtern.InformationEntryLifecycle.20260827.dll`。
- 活动 DLL SHA-256：`B596F50061455F3AF4C5339C5DB24AB2D5984743AE34F0BDE9BBAAD8D95629DA`。
- 活动 DLL 字符串扫描中，`BZOffset`、`WTopZ`、`WTopZ2`、`BIsArc` 均为 0 次；需按第 6 节在正式插件仓库实现，不能用目录中其他同名 DLL 推断已上线。
- 部署目录转换器 `数据库\dist\ydb转换.exe` SHA-256：`F14FC6AFDEC844D81DB00726FB901C215705DF3720C1E7522E6FFF987BB16A19`，在本工作包发布时仍为旧构建。部署新转换器时必须先核对 Git 发布提交和 SHA-256，再由 Revit 端按上述顺序升级。
- 本工作包新构建 `dist\ydb转换.exe` SHA-256：`EBDFEFCD2FE44C7DB61E0512B3216570C64C673180CF1E961EDB276E87E9F069`。
