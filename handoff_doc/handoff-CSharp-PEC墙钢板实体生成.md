# Handoff：C# 端 PEC 墙钢板实体生成（PecStiffenerCommand）

更新日期：2026-09-01

源码：`revit_addin/PecStiffenerCommand.cs`（单文件，可直接加入 CreateNewExtern 工程）

目标：在"结构模型 → 合并梁并生成模型"一键流程中，为 PEC 主墙（Kind 211）
补建**贯通腹板**与**加劲板**实体，弥补第一阶段"钢板只写参数不建实体"的边界。

## 1. 集成方式（一行代码）

在 `CombineBeam.Execute` 的建模流程**末尾、返回前**追加：

```csharp
PecStiffenerCommand.GenerateAllPlates(doc);
```

该方法为静态方法、自带事务处理与全量异常捕获，**任何情况下不会向宿主抛异常**，
不影响原命令的成功/失败语义。也可保留独立命令入口 `PecStiffenerCommand.Execute`
（IExternalCommand，附加模块→外部工具→PEC加劲板生成）用于单独补板。

## 2. 数据来源（只读统一中间库）

路径：`C:\ProgramData\Autodesk\Revit\Addins\2018\数据库\ydb转换数据库.db`

| 数据 | 来源 | 用途 |
|---|---|---|
| 墙肢定位/编号 | `tbl4`（WStartX/Y、WEndX/Y、WLegID、WLegRole、WShape、WSection） | 墙轴、节点长度 |
| `WInfo` v4 JSON | `tbl4.WInfo` | `web_thickness_mm`、`internal_stiffener{count,width_mm,thickness_mm}`、`tbl2_column_refs{start,end}` |
| H 截面高度 | `tbl2.CSection`（如 `H600x300x7x22@PEC` 取 600） | 净腹板范围 |
| 墙实例 | `tbl4.RvtID` → `ElementId` | **锚定已建 Wall，不重算坐标** |

## 3. 定位公式（与 WInfo v4 协议 §6 一致）

* 比例尺 `s = 墙曲线长度(ft) / 数据库节点长度(mm)` —— 对坐标系/单位约定免疫；
* 净腹板范围：起点侧 `hH`（I 墙）或 `hH − Secondary.B/2`（L 墙角点）；终点侧 `L − hH_end`；
* **腹板**：厚 `web_thickness_mm`，沿墙轴居中，净腹板范围全长，墙包围盒通高；
* **加劲板**：净腹板长度等分——1 道居中、2 道三分点；厚沿墙轴，宽垂直墙轴，居中横穿。

## 4. 关键实现（Revit 2018 兼容）

* 实体用 `GeometryCreationUtilities.CreateSweptGeometry`（路径须为 `CurveLoop`）+
  `DirectShape`（OST_GenericModel，无需族）；
* **勿用** `TessellatedShapeBuilder`（2018 下 Build 抛 InternalException）；
* 2018 无 `Document.IsTransacting`：用"尝试开事务、失败则并入宿主事务"模式；
* `ElementId` 构造用 int（2018 无 long 重载）；
* **幂等**：每次生成前按名称（含"加劲板"或以"腹板"结尾的 DirectShape）删除旧板，
  支持反复执行"合并梁并生成模型"；
* 源码按 C#5 语法编写（机器 csc 为老编译器）；合入官方工程后可自由升格。

## 5. 编译引用

```
RevitAPI.dll / RevitAPIUI.dll  （Revit 2018 安装目录）
System.Data.SQLite.dll         （插件目录已有）
目标框架 .NET Framework 4.7.2
```

## 6. 验收标准

样例 `dtlmodel2.ydb`（2 个 PEC 墙组）：

* `PECW0001-L1`（I 主墙，墙厚 200，T2=0）：生成**腹板 1 块**，无加劲板；
* `PECW0002-L1`（L 主墙，墙厚 300，T2=2）：**腹板 1 块 + 加劲板 2 块（250×8，三分点）**；
* 板厚方向、位置与 `tools/export_pec_wall_check_lite.py` 生成的核对图一致；
* 重复执行不叠加；钢板环节异常不阻断主命令。

## 7. 过渡期说明（本机现状）

在官方源码合入前，本机通过 ildasm/ilasm 把该类 IL 合并进了已部署的
`CreateNewExtern.dll` 并在 `CombineBeam::Execute` 末尾插桩调用
（脚本与基线 IL 在 `C:\Users\0015398\Desktop\1\PecSteelTools\`）。
**插件一旦升级分发，该合并即被覆盖**——请以本文件为准尽快在源码工程合入。
