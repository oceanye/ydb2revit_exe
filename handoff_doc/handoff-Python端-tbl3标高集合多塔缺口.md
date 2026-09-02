# Handoff：Python 端（ydb转换）tbl3 标高集合——多塔模型缺口

提出日期：2026-09-02
提出方：Revit 插件端（CreateNewExtern 维护）
现象：`颛桥测试版\dtlmodel.ydb`（多塔）转换后导入 Revit 失败，
`tbl2.ID=1080 在 Z=16795mm 处找不到顶部标高`，整个建模中止。
结论先行：**问题在 ydb转换.py 的 tbl3 生成，Revit 端无责、无需改动。**

## 1. 已核实事实（无需再调研）

* C# 端消费方式：`SqliteDataToRevit` 按 tbl3(Floor, LevelB) 建 Revit 标高，
  柱/梁/墙按 Z 找标高，`FindLevelAtElevation` 容差 **1mm**，按标高值匹配（与名称无关）。
* 现有 tbl3 生成（`ydb转换.py` 约 655-661 行）：
  ```python
  tbl3 = [(f"{i}F", floor.LevelB) for i, floor in floors] + [(RF, 最后一层.LevelB+Height)]
  ```
  即：**只有每层底标高 + 最后一层顶标高**。
* 颛桥 dtlmodel.ydb 楼层实测（tblFloor，16 层多塔）：
  * 裙房 1~7 层：−7200 → 14500，第 7 层为斜屋面标准层（StdFlrID=9103，
    LevelB=14500, Height=2295，**层顶 = 16795**）；
  * 塔楼第 8 层起**重起算**（LevelB=5100），8~16 层连续至 30750；
  * 逐层核对：除 16795 外，所有层顶都恰为某一层的 LevelB（被"下一层底"覆盖），
    **唯独斜屋面层顶 16795 不等于任何层底、也不是最后层顶** → tbl3 缺失。
* 复核：`dtlmodel-斜屋面.ydb`（单层）tbl3 = 底+顶两行齐全，不受影响；
  连续单栋模型同样天然齐全——此缺口只在**多塔/层顶不衔接**时出现。
* 插件端排查记录：中间库 tbl2 该柱 EndZ=16795 与 ydb 重建一致（转换提取本身无误，
  纯 tbl3 集合覆盖问题）。

## 2. 输出契约（⚠️ 必须遵守）

1. tbl3 列布局与类型不变：`tbl3(Floor TEXT, LevelB REAL)`，不新增列；
2. **集合 = ∪各层 LevelB ∪ ∪各层 (LevelB+Height)，去重（按标高值，容差建议 1e-6）**；
3. 命名：
   * 各层底沿用现行 `{序号}F` 规则不变；
   * 最后一层顶仍命名 `RF`；
   * 新增的中间层顶（如 16795）命名 `RF2`、`RF3`…（**不得与已有名重复**——
     C# 端 `Level.Name` 赋重名会抛异常）；
4. 去重后普通单栋/单层模型的 tbl3 输出**与现状完全一致**（零回归：
   连续模型的内层顶都被下一层底吸收，只剩最后一层 RF）；
5. 重复底标高（本模型 5100 出现两次）也一并去重，只保留先出现者。

## 3. 建议实现（定位到现有代码，最小改动）

在现有 `tbl3_rows` 构造处改为：

```python
tbl3_rows = []
seen_elevations = set()

def _add_level(name, elevation):
    key = round(_as_float(elevation), 6)
    if key in seen_elevations:
        return
    seen_elevations.add(key)
    tbl3_rows.append((name, elevation))

for index, floor in enumerate(floors):
    _add_level(str(index + 1) + "F", _value(floor, "LevelB"))      # 现行底标高命名
for index, floor in enumerate(floors):                              # 补各层顶
    top = _as_float(_value(floor, "LevelB")) + _as_float(_value(floor, "Height"))
    is_last = (index == len(floors) - 1)
    _add_level("RF" if is_last else "RF" + str(index + 2 - 1), top)  # 名字不得撞 RF
```

（命名细节可自行调整，只需满足契约第 3 条；`_as_float/_value` 沿用文件内现有辅助。）

## 4. 验收标准

1. 颛桥 `dtlmodel.ydb` 转换后：`SELECT * FROM tbl3` 含 16795 行；
   Revit 端重跑"合并梁并生成模型"不再报 ID=1080 顶部标高错误，模型完整导入；
2. `dtlmodel-斜屋面.ydb` 转换后 tbl3 与修改前**逐行相同**（1F@10800、RF@13095）；
3. 任一单栋连续模型 tbl3 与修改前逐行相同（零回归抽查）；
4. 表结构不变（PRAGMA table_info 一致）；行序建议按标高升序或保持底在前、顶在后。

## 5. 部署与联调

* 修改后需 `py -m PyInstaller --noconfirm --clean ydb转换.spec` 重打包，
  并替换 `C:\ProgramData\Autodesk\Revit\Addins\2018\数据库\dist\ydb转换.exe`；
* Revit 插件端零改动；验证由插件端配合执行（重转+重跑一键流程）。

## 6. 参考

* 楼层数据见 `颛桥测试版\dtlmodel.ydb` 的 tblFloor；
* C# 端标高匹配：`CreateNewExtern\SqliteDataToRevit.cs` 的 `FindLevelAtElevation`（1mm 容差）；
* 相关背景：`handoff_doc/handoff-python-PEC墙提取与Revit建模.md`（表契约总则）。
