# Handoff：Revit 插件端（CreateNewExtern）待排查与修改事项

更新日期：2026-09-01
接收方：CreateNewExtern 插件维护/开发人员
背景：经 2026-08~09 排查验证，数据链路 ①YJK→②转换器→③统一中间库 全线健康
（T2/腹板厚等参数零丢失，31 项回归测试通过）；缺口与已知问题集中在 ④ 插件端。
本机插件已回滚至原版 dll（纯净备份 `CreateNewExtern.dll.bak-20260831`），
钢板功能按本 handoff 安排独立实现。

## 事项 1（高优先 · 功能新增）：PEC 主墙钢板实体生成

**详见同目录《需求-CSharp插件-PEC墙钢板实体生成.md》**（输入数据契约、定位公式、
非功能需求、6 条验收标准），要点重申：

* 在"结构模型 → 合并梁并生成模型"流程末尾生成：**贯通腹板**（`web_thickness_mm`，
  两端 H 内侧边缘之间、沿墙轴、通高）+ **加劲板**（净腹板等分：1 道居中 / 2 道三分点）；
* 用 `tbl4.RvtID` 锚定已建墙、从墙自身曲线推导几何（对坐标系免疫）；
* 幂等（重跑不叠加）+ 异常隔离（钢板环节出错不得影响主命令、不得回滚已建图元）；
* Revit 2018 已知坑：`TessellatedShapeBuilder.Build` 抛 InternalException（用
  `CreateSweptGeometry`，路径须 CurveLoop）；无 `Document.IsTransacting`；
  `ElementId` 无 long 构造；
* 参考实现（可选采用）：`revit_addin/PecStiffenerCommand.cs`。

## 事项 2（中优先 · Bug 排查）：柱偏心变换的角度单位错误

* 位置：`SqliteDataToRevit.cs:206-219`；
* 现象：偏心变换 `ΔX = EccX·cosθ + EccY·sinθ；ΔY = EccX·sinθ − EccY·cosθ` 中
  θ 按**弧度**使用，而 `tblColSeg.Rotation` 实测为**度**（样例 90.0）——带转角的
  偏心柱会算错位（EccX=0、θ=90 时应得 ΔX=EccY，实得 ΔY=EccY·cos(90rad)≠0）；
* 建议：统一度→弧度换算；用 EccX=0、EccY=200、Rotation=90 的柱实测验证
  （正确结果应为 ΔX=200、ΔY=0）；
* 依据：《YJK坐标系与梁柱偏心定义.md》§5.2（含实测数据）。

## 事项 3（中优先 · 语义确认）：梁偏心 Ecc 与 Ecc2 是否为两端值

* 现有模型 `Ecc == Ecc2` 恒成立，无法区分语义；
* 建议：在 YJK 做一根两端偏心不同的试验梁（如 +25 / −25），转换后核对
  `tbl1.Ecc / Ecc2` 是否分别对应起点端 / 终点端；确认后 C# 端按两端分别施加
  平面内偏移（Δ = Ecc·(ty, −tx) 方案见《YJK坐标系与梁柱偏心定义.md》§5.3）；
* 依据：《handoff-python-梁偏心导出.md》§5 的三个待确认项。

## 事项 4（低优先 · 健壮性评估）：RvtID 回写与 CombineBeam 表生命周期

* `CombineBeam` 表由 C# 端建表/迁移（Python 依契约绝不触碰）——建议核对当前
  正式库中该表 `Ecc` 列迁移是否完成；
* RvtID 回写建议评估"重跑前清旧值/覆盖"策略，避免数据库留下指向已删除元素的
  陈旧 RvtID（钢板需求的参考实现采用"按名称清理旧板"，主流程墙/柱的同类策略
  请一并评估）。

## 附：排查经验

同类问题的定位顺序："**模型里缺构件/表现不对**"→ 先查 ④ 插件端；
"**数据缺字段/值不对**"→ 查 ②③（转换器与中间库）。本次全部"缺钢板"类问题
根因均在 ④，①②③ 经反复实测无损。
