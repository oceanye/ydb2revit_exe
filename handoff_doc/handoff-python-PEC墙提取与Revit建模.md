# Handoff：PEC 梁柱截面、Main/Secondary 墙提取与 Revit 建模

更新日期：2026-08-26

Python 实现：`ydb转换.py`

本地验证样例（不提交公开仓库）：`PEC剪力墙测试/dtlmodel_PECWall.ydb`

## 1. 构件边界

1. 梁、柱继续使用原有 `tbl1`、`tbl2`，不追加 `YdbColID`、`YdbSectID`、`YdbJtID`、`YdbFloorID`。
2. PEC 梁以及 PEC 墙端柱用截面名末尾的 `@PEC` 标识，不增加专用梁柱表。
3. Main 墙两端的普通 `tblColSect.Kind=2` H 型钢是独立建模的 PEC 端柱，必须进入 `tbl2`。
4. 墙保持“一条直墙段对应 `tbl4` 一行”；Revit 墙必须基于 `Wall`，不得用 `Column` 代替。
5. Main 墙采用 I 形钢构造：两端 H 柱、中间腹板及可选内部加劲肋。
6. Secondary 墙采用 T 形钢构造：只有腹板和外端翼缘，自身没有 H 柱；尾端连接 Main 的 H 柱。
7. Revit 本阶段创建混凝土外轮廓墙和 `tbl2` 端柱。墙内腹板、加劲肋、T 形翼缘、连接板和钢筋仅作为参数保留，不创建实体。

所有坐标和截面尺寸均为 mm，角度为度。Revit API 负责换算为内部英尺。

## 2. Python 使用方式

```powershell
python .\ydb转换.py C:\path\to\model.ydb -o C:\temp\ydb转换数据库.db
```

不指定 `-o` 时仍使用原有默认数据库位置；双击 EXE 时使用 Windows 原生文件选择窗口。

## 3. 梁柱截面契约

`tbl1`、`tbl2` 的既有列顺序不变。PEC H 型钢统一输出：

```text
H{截面高度}x{翼缘宽度}x{腹板厚度}x{翼缘厚度}@PEC
```

识别规则：

- `tblBeamSect.Kind=209`：按 `t=H、d=W、u=tw、f=tf` 读取，字段缺失时回退到同 ID 的 `tblSubSectionSect`。
- `tblColSect.Kind=209`：作为显式 PEC 柱输出 `H...@PEC`。
- `tblColSect.Kind=2` 且柱节点为 `Kind=211` Main 墙端点：作为 PEC 墙端柱输出 `H...@PEC`。
- Secondary 外端不属于 Main 墙端柱判定范围，因为 Secondary 自身不设置 H。
- 其他普通柱保持既有 `tbl2` 截面格式。

Revit 端以最后一个 `@PEC` 判断 PEC 属性，去除后缀后再查找或生成 H 型钢族类型；`@PEC` 不是第五个截面尺寸。

当前样例：

- PEC 梁：`H400x150x10x20@PEC`，1 根。
- PEC Main 端柱：`H244x175x8x12@PEC`，4 根。
- 其他独立柱：0 根。

## 4. `tbl4` 输出契约

前 12 列保持不变：

| 下标 | 字段 |
|---:|---|
| 0–5 | `WStartX` 至 `WEndZ` |
| 6 | `WSection` |
| 7 | `Tag` |
| 8 | `ID` |
| 9 | `RvtID` |
| 10 | `BottomFloor` |
| 11 | `WEConn` |

末尾 5 列：

| 下标 | 字段 | 含义 |
|---:|---|---|
| 12 | `WGroupID` | 逻辑 PEC 墙编号，例如 `PECW0001` |
| 13 | `WLegID` | 直墙肢编号，例如 `PECW0001-L1` |
| 14 | `WLegRole` | `MAIN` 或 `SECONDARY` |
| 15 | `WShape` | 逻辑平面组合：`I` 或 `L` |
| 16 | `WInfo` | UTF-8 JSON 参数信息，当前版本 2 |

`WShape` 表示墙肢的平面组合，不等于钢构造截面形式。钢构造形式读取 `WInfo.steel_configuration.cross_section_form`：

- Main：`I`
- Secondary：`T`

`WSection` 只保存混凝土外轮廓厚度 `tblWallSect.B`，例如 `175`。内部钢板参数不得拼接到 `WSection`。

## 5. I/L 布置与追溯

### I 墙

- 一个 Main 直段、一行 `tbl4`。
- `WShape=I`、`WLegRole=MAIN`。
- `WLegID={WGroupID}-L1`。

### L 墙

- 一个 Main 直段和一个 Secondary 直段，共两行 `tbl4`。
- 两行共享 `WGroupID`，并均为 `WShape=L`。
- Main：`WLegRole=MAIN`、`WLegID=...-L1`。
- Secondary：`WLegRole=SECONDARY`、`WLegID=...-L2`。
- 两条输出定位线均从公共角点指向外端，因此起点相同。
- `WInfo.layout.turn_sign`：`+1` 为 Main 到 Secondary 逆时针，`-1` 为顺时针。

组合条件为同一标准层、Main `Kind=211`、Secondary `Kind=212`、共享一个端点且近似垂直。没有 Secondary 的 Main 输出为 I。孤立 Secondary 仍保留输出并写入警告，但其 T 尾端连接状态为 `UNRESOLVED`，不能把它静默改造成 Main。

## 6. Main I 形构造参数

Main 的字段映射已经由当前 YDB 与 DXF 截面相互验证：

构件归属也与盈建科公开说明一致：端部两颗柱子属于端柱，加劲肋设置在墙体内部。参考：[盈建科“端柱带肋钢板墙”说明](https://www.yjk.cn/article/787/)。

| YDB 字段 | `WInfo` 字段 | 含义 |
|---|---|---|
| `B` | `concrete_outer.thickness_mm` | 混凝土外轮廓厚度 |
| `H` | `steel_configuration.web_thickness_mm` | 中间腹板厚度 `tw` |
| `T2` | `internal_stiffener.count` | 内部垂直加劲肋数量 |
| `T2+3` | `partition_count` | 区隔数量 |
| `Dis` | `internal_stiffener.width_mm` | 加劲肋宽度 |
| `Dis1` | `internal_stiffener.thickness_mm` | 加劲肋厚度 |
| Main 两端 `Kind=2` 柱 | `boundary_h.start/end` | 两端 H 柱参数引用，实体来源为 `tbl2` |

区隔规则：

| 区隔数 | `T2` | 内部加劲肋 |
|---:|---:|---:|
| 3 | 0 | 0 道 |
| 4 | 1 | 1 道 |
| 5 | 2 | 2 道 |

当前 Main 参数为：混凝土厚度 175、腹板厚 6、4 区隔、1 道 175×6 加劲肋，两端 H 柱为 H244×175×8×12。

## 7. Secondary T 形构造参数

| YDB 字段 | `WInfo` 字段 | 含义 |
|---|---|---|
| `B` | `concrete_outer.thickness_mm` | 混凝土外轮廓厚度 |
| `H` | `steel_configuration.web_thickness_mm` | T 形腹板厚度 |
| `Dis` | `steel_configuration.flange.thickness_mm` | 外端翼缘厚度 |
| `Dis1` | `steel_configuration.flange.width_mm` | 外端翼缘宽度 |

Secondary 的约束：

- `has_own_end_h=false`，其 `boundary_h.start/end` 均为空。
- 输出方向为公共角点到外端，故 T 尾位于 `start`。
- `tail_connection.type=TAIL_TO_MAIN_H`。
- `tail_connection.main_leg_id` 指向同组 Main 的 `WLegID`。
- `tail_connection.connected_main_h` 仅引用 Main 端柱参数，不能据此再创建一根柱。

当前 Secondary 参数为：混凝土厚度 175、腹板厚 8、翼缘宽 150、翼缘厚 20。

## 8. `WInfo v2` 示例

Main：

```json
{
  "version": 2,
  "layout": {
    "shape": "L",
    "group_id": "PECW0001",
    "leg_id": "PECW0001-L1",
    "leg_role": "MAIN"
  },
  "concrete_outer": {"thickness_mm": 175},
  "steel_configuration": {
    "component_role": "MAIN",
    "cross_section_form": "I",
    "web_thickness_mm": 6,
    "partition_count": 4,
    "internal_stiffener": {
      "count": 1,
      "width_mm": 175,
      "thickness_mm": 6
    },
    "end_h_columns": {
      "expected_count": 2,
      "details_path": "boundary_h",
      "modeling_source": "tbl2"
    }
  },
  "boundary_h": {
    "start": [{"section": "H244x175x8x12"}],
    "end": [{"section": "H244x175x8x12"}]
  }
}
```

Secondary：

```json
{
  "version": 2,
  "layout": {
    "shape": "L",
    "group_id": "PECW0001",
    "leg_id": "PECW0001-L2",
    "leg_role": "SECONDARY"
  },
  "concrete_outer": {"thickness_mm": 175},
  "steel_configuration": {
    "component_role": "SECONDARY",
    "cross_section_form": "T",
    "web_thickness_mm": 8,
    "flange": {"width_mm": 150, "thickness_mm": 20},
    "has_own_end_h": false,
    "tail_connection": {
      "type": "TAIL_TO_MAIN_H",
      "location": "start",
      "main_leg_id": "PECW0001-L1",
      "connected_main_h": [{"section": "H244x175x8x12"}]
    }
  },
  "boundary_h": {"start": [], "end": []}
}
```

`section_parameters` 和 `segment_parameters` 仍完整保留 YDB 业务字段。连接板、钢筋及当前未直接驱动几何的字段继续作为信息透传，不输出额外 YDB ID 列。

## 9. Revit API 建模要求

### 9.1 建模顺序

1. 按既有 `tbl3` 创建或匹配楼层，禁止二次提取层高和标准层。
2. 读取 `tbl2`，创建 PEC Main 两端 H 柱；应用原有柱偏心和旋转。
3. 读取 `tbl4`，每行创建一个 Revit `Wall`，类型厚度取 `WSection`。
4. 对 `WShape=L` 的 L1/L2 按共同 `WGroupID` 连接墙端。
5. 将 `WGroupID/WLegID/WLegRole/WShape/WInfo` 写入共享参数或可追溯存储。
6. 按既有方式分别回写每行 `RvtID`，共同 `WGroupID` 不能覆盖行级 ID。

### 9.2 去重规则

- Main 端 H 柱只从 `tbl2` 创建一次。
- `boundary_h` 和 `tail_connection.connected_main_h` 都是关系与参数引用，禁止再次创建 Column。
- Secondary 自身没有 H 柱，不得在其外端或尾端自动补柱。
- 腹板、加劲肋、T 形翼缘、连接板和逐根钢筋本阶段不创建几何。
- `WInfo` 解析失败不得阻止混凝土墙创建，应记录日志并继续。

## 10. 当前样例验收结果

- `tbl1`：1 行，`H400x150x10x20@PEC`。
- `tbl2`：4 行，均为 `H244x175x8x12@PEC`。
- `tbl3`：`1F=0`、`RF=3300`。
- `tbl4`：3 行。
- `PECW0001-L1`：Main，I 形钢构造。
- `PECW0001-L2`：Secondary，T 形钢构造，尾接 L1 的 H 柱。
- `PECW0002-L1`：独立 Main，I 墙。
- 逻辑墙共 2 组：一个 L、一个 I。
- 普通墙样例继续正常转换，不产生 PEC 分组或 `WInfo`。

## 11. 后续 C# 最小改动

1. 梁柱截面解析支持末尾 `@PEC`。
2. 墙查询使用显式列名并读取新增 5 列。
3. PEC 墙始终调用 `Wall.Create`；端 H 柱走现有 Column 流程。
4. 按 `WGroupID` 组合 L1/L2，按 `WLegRole` 区分 Main/Secondary。
5. 解析 `WInfo.version=2`，把 I/T、腹板、区隔、加劲肋及连接信息写入参数。
6. 严格执行去重规则：`tbl2` 建柱，`WInfo` 只引用。
