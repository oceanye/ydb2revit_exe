# 需求：CreateNewExtern 插件 · PEC 墙钢板实体生成

提出日期：2026-09-01
提出背景：PEC 主墙（Kind 211/212）转换管线中，钢板（腹板/加劲板）数据已完整进入统一中间库，但插件建模只生成混凝土墙与端部 H 柱，钢板无实体，用户在模型中不可见、不可用。
期望：独立实现并合入 CreateNewExtern 插件源码工程，随官方版本分发。

参考实现（可选采用）：`revit_addin/PecStiffenerCommand.cs`（已在本仓库，
含 Revit 2018 兼容性与 API 踩坑记录；采用与否由实现方决定）。

## 1. 功能需求

### 1.1 触发场景

* 主场景：在"结构模型 → 合并梁并生成模型"一键流程的**末尾**自动生成钢板（墙、H 柱已就位后）；
* 辅助场景（可选）：提供独立命令手动补板，便于单独刷新钢板而不重建模型。

### 1.2 生成对象（仅 PEC 主墙 WLegRole=MAIN）

| 对象 | 几何定义 |
|---|---|
| **贯通腹板** | 厚 = `WInfo.web_thickness_mm`；平面沿墙轴线居中；纵向范围 = 两端 H **内侧边缘**之间；高度 = 墙实例全高 |
| **加劲板** | 数量 = `WInfo.internal_stiffener.count`；在净腹板长度上**等分**布置（1 道居中、2 道三分点）；单块尺寸 = `width_mm × thickness_mm`；板厚沿墙轴、板宽垂直墙轴、居中横穿腹板、墙实例全高 |

### 1.3 定位规则（净腹板范围）

设墙节点长度 `L`，起/终点 H 截面高度 `hH_start`/`hH_end`：

* 起点侧内边缘：I 墙为 `hH_start`；L 墙角点侧为 `hH_start − Secondary.WSection/2`；
* 终点侧内边缘：`L − hH_end`。

墙长一律按节点坐标计算（`|WEnd − WStart|`），不得使用截面字段替代。
完整公式见 `handoff_doc/handoff-python-PEC墙提取与Revit建模.md` §6。

## 2. 输入数据（统一中间库，只读）

路径：`C:\ProgramData\Autodesk\Revit\Addins\2018\数据库\ydb转换数据库.db`（与现有建模命令同库）

| 表 | 字段 | 用途 |
|---|---|---|
| `tbl4` | `WStartX/Y`、`WEndX/Y`、`WSection`、`WLegID`、`WLegRole`、`WShape`、`WGroupID` | 墙轴线、节点长度、厚度、L/I 识别 |
| `tbl4` | `WInfo`（v4 JSON） | `web_thickness_mm`、`internal_stiffener{count,width_mm,thickness_mm}`、`tbl2_column_refs{start,end}` |
| `tbl4` | `RvtID` | **锚定已建 Wall 实例**（按 ElementId 取墙，从墙自身曲线推导几何，不重算世界坐标） |
| `tbl2` | `CSection`（`H600x300x7x22@PEC`） | 取 H 截面高度 hH |

比例尺建议：`s = 墙曲线长度(ft) / 数据库节点长度(mm)`，对坐标系/单位约定免疫。

## 3. 非功能需求

1. **幂等**：重复执行一键流程不得叠加钢板；生成前按可识别标记（如名称规则）删除旧板；
2. **异常隔离**：钢板生成环节的任何异常**不得**影响"合并梁并生成模型"主流程的成功语义，也不得回滚主流程已建图元；
3. **事务兼容**：实现需兼容宿主命令结束时事务/事务组的开关状态（自管事务或并入宿主任务）；
4. **Revit 2018 兼容**（已知坑，详见参考实现注释）：
   * `TessellatedShapeBuilder.Build` 在 2018 会抛 InternalException——建议用 `GeometryCreationUtilities.CreateSweptGeometry`（注意其路径参数是 `CurveLoop`）；
   * `Document.IsTransacting` 在 2018 不存在；
   * `ElementId` 无 long 构造；
5. 无需族/族库：建议 DirectShape（如 OST_GenericModel）承载钢板实体。

## 4. 验收标准

样例 `dtlmodel2.ydb`（2 个 PEC 墙组）：

1. `PECW0001-L1`（I 主墙，墙厚 200，T2=0）：生成**腹板 1 块**、无加劲板；
2. `PECW0002-L1`（L 主墙，墙厚 300，T2=2）：**腹板 1 块 + 加劲板 2 块（250×8，三分点）**；
3. 板的位置/尺寸与 `tools/export_pec_wall_check_lite.py` 生成的核对 DXF 一致；
4. 连续执行两次一键流程，钢板数量不变（幂等）；
5. 人为制造钢板环节异常（如断开数据库），主流程仍正常完成建模；
6. Secondary 墙（T 形钢构）本期**不**生成实体，仅 MAIN 墙。

## 5. 边界与后续

* 本期不建：T 翼缘、Secondary 腹板、连接板、钢筋——待本期验收后另行提需求；
* Python 转换器侧无任何改动需求，数据契约以 `WInfo v4` 为准（如需扩字段，双方另行评审版本）。
