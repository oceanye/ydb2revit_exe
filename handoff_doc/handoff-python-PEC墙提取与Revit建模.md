# Handoff：PEC 梁柱截面、I/L 墙提取与 Revit 建模

更新日期：2026-08-26
Python 实现：`ydb转换.py`
本地验证样例（未提交公开仓库）：`PEC剪力墙测试/dtlmodel_PECWall.ydb`

## 1. 本次接口边界

1. 梁、柱继续使用原有 `tbl1`、`tbl2`，不增加 `YdbColID`、`YdbSectID`、`YdbJtID`、`YdbFloorID` 等源数据库 ID。
2. PEC 梁柱只通过 H 型钢截面名的 `@PEC` 后缀识别，不增加 PEC 专用构件表。
3. 墙保持“一条直墙段对应 `tbl4` 一行”。
4. I 墙由一个直段组成；L 墙由两个直段组成，两行共享逻辑墙编号。
5. Revit 只建立混凝土墙外轮廓。墙内 H 型钢、连接板和钢筋不建立实体，仅在 `WInfo` 中保留参数。
6. 独立存在于 `tbl2` 的边缘柱仍作为柱建模；它与“墙内 H 型钢信息”不是同一套几何，禁止重复创建。

所有坐标和截面尺寸均为 mm，角度为度。Revit API 负责把 mm 转换为内部英尺。

## 2. Python 使用方式

保留原有双击 exe / 文件选择窗口的使用方式，同时支持命令行，便于测试和自动化：

```powershell
python .\ydb转换.py C:\path\to\model.ydb -o C:\temp\ydb转换数据库.db
```

不指定 `-o` 时仍写入原有默认数据库位置。

## 3. 梁柱截面契约

`tbl1`、`tbl2` 的既有列顺序不变，也不追加源 YDB ID。

PEC 对称 H 型钢统一输出为：

```text
H{截面高度}x{翼缘宽度}x{腹板厚度}x{翼缘厚度}@PEC
```

例如：

```text
H400x200x8x16@PEC
```

当前样例实际结果：

- 梁：`H400x150x10x20@PEC`
- 边缘柱：`H244x175x8x12@PEC`

数据来源：

- `tblBeamSect.Kind=209`：按 `t=H、d=W、u=tw、f=tf` 读取；字段缺失时回退到同 ID 的 `tblSubSectionSect`。
- `tblColSect.Kind=2`：按 `h=H、u=W、b=tw、t=tf` 读取。
- 只有处于已识别 PEC 墙端点的 Kind 2 柱才追加 `@PEC`；普通模型中的普通 H 型钢柱保持原格式。

Revit 端处理要求：

1. 以最后一个 `@PEC` 判断 PEC 属性。
2. 用去除后缀后的 `H...` 查找或生成 H 型钢族类型。
3. 不把 `@PEC` 当作第五个截面尺寸。
4. 不改变现有梁柱偏心和旋转处理。

## 4. `tbl4` 输出契约

前 12 列严格保持不变：

| 下标 | 字段 |
|---:|---|
| 0–5 | `WStartX` 至 `WEndZ` |
| 6 | `WSection` |
| 7 | `Tag` |
| 8 | `ID` |
| 9 | `RvtID` |
| 10 | `BottomFloor` |
| 11 | `WEConn` |

末尾追加 5 列：

| 下标 | 字段 | 含义 |
|---:|---|---|
| 12 | `WGroupID` | 逻辑 PEC 墙编号，例如 `PECW0001` |
| 13 | `WLegID` | 直墙肢编号，例如 `PECW0001-L1` |
| 14 | `WLegRole` | `MAIN` 或 `SECONDARY` |
| 15 | `WShape` | `I` 或 `L` |
| 16 | `WInfo` | UTF-8 JSON 参数信息 |

普通墙这 5 列均为 NULL。既有 Revit 代码读取 0–6 下标仍可继续工作，但新代码应使用明确列名，避免后续再依赖 `SELECT *` 的位置下标。

`WSection` 对 PEC 墙只保存混凝土墙厚，例如 `175`。钢板厚度不能再拼接到 `WSection`，避免 Revit 把钢板参数误当成墙厚或墙类型。

## 5. I/L 墙组合规则

### I 墙

- 一个 `tbl4` 行。
- `WShape=I`。
- `WLegRole=MAIN`。
- `WLegID={WGroupID}-L1`。

### L 墙

- 两个 `tbl4` 行。
- 两行 `WShape=L` 且 `WGroupID` 相同。
- 主墙肢：`WLegRole=MAIN`、编号后缀 `-L1`。
- 副墙肢：`WLegRole=SECONDARY`、编号后缀 `-L2`。
- 两条输出定位线均从公共角点指向外端，因此两行的 `WStartX/WStartY` 相同。
- `WInfo.layout.turn_sign` 保存主肢到副肢的平面叉积符号：`+1` 为逆时针，`-1` 为顺时针。

当前适配器使用以下已验证组合：

- Kind 211：主墙肢候选。
- Kind 212：副墙肢候选。
- 同一楼层、共享一个端点、夹角在约 90° 的两肢组合为 L。
- 没有匹配副肢的 Kind 211 输出为 I。
- 未匹配到主肢的 Kind 212 暂按 I 输出，并在 `WInfo.warning` 标记，不能静默丢弃。

分组编号由转换器按楼层和源构件顺序稳定生成。它是交接数据库内部的逻辑编号，不是 YDB 源 ID。

## 6. `WInfo` JSON

`WInfo` 只承载信息，不直接驱动本阶段的钢构造几何。主要结构：

```json
{
  "version": 1,
  "layout": {
    "shape": "L",
    "group_id": "PECW0001",
    "leg_id": "PECW0001-L1",
    "leg_role": "MAIN",
    "corner_mm": {"x": 0, "y": 1500},
    "turn_sign": 1,
    "source_direction_reversed": true
  },
  "concrete_outer": {"thickness_mm": 175},
  "section_parameters": {
    "Mat": 6,
    "Kind": 211,
    "B": 175,
    "H": 6,
    "T2": 1,
    "Dis": 175,
    "Dis1": 6
  },
  "segment_parameters": {
    "Ecc": 0,
    "HDiff1": 0,
    "HDiff2": 0,
    "offset1": 0,
    "offset2": 0
  },
  "boundary_h": {
    "start": [{"section": "H244x175x8x12"}],
    "end": []
  },
  "modeling": {
    "create_concrete_wall": true,
    "create_wall_internal_h": false,
    "create_connection_plate_geometry": false,
    "create_rebar_instances": false
  }
}
```

说明：

- `section_parameters` 保留 `tblWallSect` 的全部业务参数，包括当前尚不能统一解释的 `H/T2/Dis/Dis1` 和端部截面文本。
- `segment_parameters` 保留偏心、端部高差、斜墙、端部偏移、墙铰等布置参数。
- `boundary_h` 保存墙端点附近 H 型柱的尺寸、偏心和旋转，仅供查询和深化，不据此重复建柱。
- `modeling` 中的 false 表示本阶段不得创建对应实体，不表示源数据中不存在该信息。
- 不输出 `YdbWallID/YdbSectID/YdbJtID/YdbFloorID`。

## 7. Revit API 建模顺序

### 7.1 读取

建议显式查询字段：

```sql
SELECT
    WStartX,WStartY,WStartZ,WEndX,WEndY,WEndZ,WSection,
    ID,RvtID,WGroupID,WLegID,WLegRole,WShape,WInfo
FROM tbl4
ORDER BY ID;
```

不得因 `WInfo` 解析失败而阻止混凝土墙创建。`WInfo` 属于可选深化信息，解析失败应记录日志并继续。

### 7.2 创建混凝土墙

1. 每个 `tbl4` 行创建一个 Revit `Wall`。
2. 用 `WStart*`、`WEnd*` 创建墙定位线。
3. 用 `WSection` 的数值查找或复制对应厚度的 Basic WallType。
4. 底标高按现有 `tbl3` 与 Z 坐标逻辑处理，层高继续沿用现有楼层定义。
5. `WShape=I` 时无需组合处理。
6. `WShape=L` 时先分别创建 L1、L2，再按共同 `WGroupID` 校验公共起点并调用墙端连接逻辑。
7. 可把 `WGroupID/WLegID/WLegRole/WShape/WInfo` 写入 Revit 共享参数，方便查询与回写。

### 7.3 禁止创建的对象

本阶段不得根据 `WInfo` 创建：

- 墙内 H 型钢实体；
- 连接板实体；
- 逐根钢筋；
- 由 `boundary_h` 重复生成的边缘柱。

只有 `tbl2` 中真实存在的边缘柱才按柱流程创建。

### 7.4 偏心和其他原始参数

当前 PEC 样例的墙偏心和端部偏移均为 0。Python 已完整保留这些字段，但没有在未知语义下擅自修改墙定位线。后续获得非零 PEC 墙样例后，应在 Python 或 Revit 中选择唯一一端应用偏移，禁止两端重复应用。

## 8. 当前样例验收结果

本地 `dtlmodel_PECWall.ydb` 转换结果：

- `tbl1`：1 行，`H400x150x10x20@PEC`。
- `tbl2`：4 行，均为 `H244x175x8x12@PEC`。
- `tbl3`：`1F=0`、`RF=3300`。
- `tbl4`：3 行。
- 逻辑 PEC 墙：2 组。
  - `PECW0001-L1` + `PECW0001-L2`：L 墙。
  - `PECW0002-L1`：I 墙。
- L1/L2 的输出起点均为公共角点 `(0,1500)`。
- 普通墙样例 `dtlmodelsw.ydb`、`dtlmodelww.ydb` 均可正常转换，且不会产生 PEC 分组字段。

## 9. 后续 C# 最小改动清单

1. 梁柱截面解析增加 `@PEC` 后缀识别。
2. 墙读取改为显式列名，并读取新增 5 列。
3. 每行仍调用 `Wall.Create`；不得将 PEC 墙转为 Column。
4. 根据 `WGroupID` 对 L1/L2 做连接和参数回写。
5. 墙内 H 型钢、连接板、钢筋只保存信息，不生成几何。
6. 建模完成后继续按现有方式回写每行唯一的 `RvtID`，不要用共同 `WGroupID` 覆盖行级 ID。
