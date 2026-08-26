# Handoff：PEC 梁柱截面、Main/Secondary 墙提取与 Revit 建模

更新日期：2026-08-26

Python 实现：`ydb转换.py`

墙参数协议：`WInfo v4`

## 1. 最终构件边界

1. 梁、柱继续使用 `tbl1`、`tbl2`，不追加 YDB 内部 ID 字段。
2. PEC 梁、独立 PEC 柱及 PEC Main 端柱统一使用
   `H{h}x{b}x{tw}x{tf}@PEC` 截面字符串。
3. Main 墙端 H 是独立 Column，实体来源为 `tbl2`；墙通过 `tbl2.ID`
   建立索引关联，不在 `WInfo` 重复 H 截面参数。
4. 每条直墙肢对应 `tbl4` 一行。混凝土墙必须由 Revit `Wall`
   创建，不得使用 Column 代替。
5. Main 的钢构形式为 I：两端 H、中间腹板及可选加劲板。
6. Secondary 的钢构形式为 T：中间腹板和外端翼缘，自身没有 H；
   尾部连接同组 Main 公共角点的 H。
7. 本阶段只创建混凝土 Wall 和 `tbl2` 中的 H Column。墙内腹板、加劲板、
   T 翼缘、连接板和钢筋不创建实体，但参数必须写入数据库，供 Revit
   写入墙参数或后续功能调用。

所有坐标和截面尺寸单位均为 mm，角度为度。Revit API 负责换算内部英尺。

## 2. Python 使用方式

```powershell
python .\ydb转换.py C:\path\to\dtlmodel.ydb -o C:\temp\ydb转换数据库.db
```

不指定 `-o` 时沿用原有默认数据库位置；双击 EXE 时使用 Windows 原生文件选择框。

## 3. 梁柱表契约

### 3.1 截面

PEC H 型钢统一输出：

```text
H{截面高度}x{翼缘宽度}x{腹板厚度}x{翼缘厚度}@PEC
```

例如：

```text
H300x200x8x20@PEC
H600x300x7x22@PEC
```

Revit 以末尾 `@PEC` 判断 PEC 属性，去掉后缀后解析四个截面尺寸。

### 3.2 墙端 H 的 tbl2 字段

- `CStartX/CStartY`：保存 Main 墙端源节点坐标。
- `CStartZ/CEndZ`：继续按现有楼层定义输出。
- `CSection`：保存完整 H 截面及 `@PEC` 后缀。
- `ID`：输出数据库内的稳定行号，供 `tbl4.WInfo.tbl2_column_refs` 引用。
- `EccX/EccY/Rotation`：字段为兼容旧接口继续保留，但 PEC 墙端 H 统一写 `0`。
- 独立 PEC 柱和普通柱不受上述归零规则影响，继续保留自身定位字段。

墙端 H 的中心位置和旋转必须由墙端点、墙方向和 H 尺寸计算，不再读取
YDB 柱偏心或柱旋转。被任一 `tbl4.WInfo.tbl2_column_refs` 引用的 `tbl2.ID`
即为墙端 H；未被引用的独立 PEC 柱仍走普通柱定位流程。

## 4. tbl4 布置契约

前 12 列保持原有顺序：

| 下标 | 字段 |
|---:|---|
| 0–5 | `WStartX` 至 `WEndZ` |
| 6 | `WSection`，即混凝土墙厚 B |
| 7 | `Tag` |
| 8 | `ID` |
| 9 | `RvtID` |
| 10 | `BottomFloor` |
| 11 | `WEConn` |

末尾 5 列：

| 下标 | 字段 | 含义 |
|---:|---|---|
| 12 | `WGroupID` | 逻辑 PEC 墙编号 |
| 13 | `WLegID` | 直墙肢编号 |
| 14 | `WLegRole` | `MAIN` 或 `SECONDARY` |
| 15 | `WShape` | 平面组合 `I` 或 `L` |
| 16 | `WInfo` | UTF-8 JSON，当前版本 4 |

`WShape` 是平面组合；钢构截面形式读取
`WInfo.steel_configuration.cross_section_form`。

数据库同时创建 `idx_tbl2_id`、`idx_tbl4_group` 和 `idx_tbl4_leg`，便于
Revit 按 H 引用、逻辑墙组和墙肢快速查询。JSON 中的引用值始终指向
同一输出数据库的 `tbl2.ID`，不是 YDB 内部 ID。

### I 墙

- 一条 Main、一行 `tbl4`。
- `WShape=I`、`WLegRole=MAIN`。
- `WLegID={WGroupID}-L1`。

### L 墙

- Main 和 Secondary 各一行。
- 共同 `WGroupID`，均为 `WShape=L`。
- Main：`...-L1`、`WLegRole=MAIN`。
- Secondary：`...-L2`、`WLegRole=SECONDARY`。
- 两条输出定位线均从公共角点指向各自外端，因此两行 `WStart` 相同。

墙肢长度只按节点坐标计算：

```text
L = |WEnd - WStart|
```

不得使用 `Dis1` 或其他截面字段作为墙长。

## 5. WInfo v4

`WInfo` 分为三部分：

- `tbl2_column_refs`：关联独立 H Column。
- `steel_configuration`：不可从通用布置字段推导的钢构输入/计算参数。
- `source_parameters`：原始业务字段的信息透传，不驱动定位。

### 5.1 Main 示例

```json
{
  "version": 4,
  "tbl2_column_refs": {
    "start": 3,
    "end": 4
  },
  "steel_configuration": {
    "cross_section_form": "I",
    "web_thickness_mm": 10,
    "partition_count": 4,
    "internal_stiffener": {
      "count": 1,
      "width_mm": 250,
      "thickness_mm": 8
    }
  },
  "source_parameters": {
    "section": {
      "Kind": 211,
      "B": 300,
      "H": 10,
      "T2": 1,
      "Dis": 250,
      "Dis1": 8
    },
    "segment": {}
  }
}
```

`tbl2_column_refs.start/end` 分别对应本行 `WStart/WEnd` 处的 H Column，
数值引用 `tbl2.ID`。两端 H 可以采用不同截面，不需要在墙中复制参数。

Main 参数映射：

| YDB 字段 | v4 字段 | 说明 |
|---|---|---|
| `H` | `web_thickness_mm` | Main 中间腹板厚度 |
| `T2+3` | `partition_count` | 3、4、5 区隔 |
| `T2` | `internal_stiffener.count` | 计算后直接写出 |
| `Dis` | `internal_stiffener.width_mm` | 加劲板宽度 |
| `Dis1` | `internal_stiffener.thickness_mm` | 加劲板厚度 |

区隔与加劲板数量：

| 区隔 | 加劲板数量 |
|---:|---:|
| 3 | 0 |
| 4 | 1 |
| 5 | 2 |

即使数量为 0，YDB 中输入的加劲板宽度和厚度仍保留为信息参数，但不得生成板。

### 5.2 Secondary 示例

```json
{
  "version": 4,
  "tbl2_column_refs": {
    "connected_main": 3
  },
  "steel_configuration": {
    "cross_section_form": "T",
    "web_thickness_mm": 12,
    "flange_thickness_mm": 14
  },
  "source_parameters": {
    "section": {
      "Kind": 212,
      "B": 300,
      "H": 12,
      "Dis": 14,
      "Dis1": 150
    },
    "segment": {}
  }
}
```

Secondary 只保存两个不可推导的核心输入：腹板厚度和翼缘厚度。

| 参数 | 获取方式 |
|---|---|
| 混凝土墙厚 | `WSection` |
| T 翼缘宽度 | 等于 `WSection` |
| T 翼缘厚度 | `flange_thickness_mm` |
| T 腹板厚度 | `web_thickness_mm` |
| 长度 | `|WEnd-WStart|` |
| 连接 H | `tbl2_column_refs.connected_main` |
| T 位置和旋转 | 根据 Secondary 节点方向计算 |

`source_parameters.section.Dis1` 仅保留原值，不作为翼缘宽度、墙长或定位参数。

### 5.3 不再保存的重复定位信息

v4 删除：

- `boundary_h`
- H 的 `ecc_x_mm/ecc_y_mm/rotation_deg`
- `tail_connection.connected_main_h`
- `tail_connection.alignment`
- `tail_connection.main_leg_id`
- Secondary 的重复 `flange.width_mm`
- `concrete_outer`、重复 layout 和 modeling 开关

## 6. H 和 T 的计算定位规则

定义：

```text
uM = (Main.WEnd - Main.WStart) / Main长度
uS = (Secondary.WEnd - Secondary.WStart) / Secondary长度
```

H 截面字符串中第一项为高度 `hH`，第三项为 H 腹板厚度 `twH`。

### 6.1 I 墙两端 H

```text
起点H中心 = Main.WStart + uM × hH_start / 2
终点H中心 = Main.WEnd   - uM × hH_end   / 2
```

H 高度方向平行 Main 轴线，翼缘宽度方向垂直 Main 轴线。

### 6.2 L 墙公共角点 H

Main 和 Secondary 已统一从公共角点向外输出，因此：

```text
角点H中心 = Main.WStart + uM × (hH - Secondary.WSection) / 2
```

当前样例：

```text
(600 - 300) / 2 = 150
```

这使 H 的一侧与 Secondary 混凝土墙面平齐。符号由 `uM` 自动确定，
数据库不再记录 `EccY=-150`。

Main 外端 H 仍按 `hH/2` 向墙内定位。

### 6.3 Main 腹板及加劲板

- Main 腹板位于墙轴线上，厚度为 `web_thickness_mm`。
- 腹板纵向范围为两端 H 的内侧边缘之间。
- 加劲板数量读取计算后的 `internal_stiffener.count`。
- 加劲板按净腹板长度等分布置；1 道位于中点，2 道位于三分点。
- 加劲板宽度和厚度读取 `internal_stiffener.width_mm/thickness_mm`。

这些参数写入数据库供 Revit 使用；当前阶段不创建钢板实体。

### 6.4 Secondary T

外端翼缘：

```text
翼缘中心 = Secondary.WEnd - uS × 翼缘厚度 / 2
翼缘宽度 = Secondary.WSection
```

腹板：

```text
方向 = uS
起点 = 公共角点 Main H 腹板朝 Secondary 一侧的外缘
终点 = Secondary.WEnd - uS × 翼缘厚度
厚度 = web_thickness_mm
```

因此不需要保存 T 的坐标、旋转或长度。

## 7. Revit API 交接要求

1. 按现有 `tbl3` 创建或匹配楼层，不重复提取层高和标准层。
2. 读取 `tbl4`，每行创建一个混凝土 `Wall`，类型厚度取 `WSection`。
3. 按 `WGroupID/WLegID/WLegRole/WShape` 识别 I/L 及 Main/Secondary。
4. 解析 `WInfo.version=4`。
5. Main 通过 `tbl2_column_refs.start/end` 查询 `tbl2.ID`，解析 `CSection`，
   按第 6 节公式创建独立 H Column。
6. Secondary 的 `connected_main` 只引用既有 Main 角点 H，禁止重复创建柱。
7. 墙端 H 不读取 `tbl2.EccX/EccY/Rotation`；普通柱和独立 PEC 柱仍按原逻辑。
8. 将 H 截面、Main 腹板、区隔、加劲板、Secondary 腹板/翼缘及后续连接板、
   配筋信息写入对应 Revit 墙参数。
9. 墙内钢板信息仅作为参数；除非后续另行要求，不创建腹板、加劲板或 T 翼缘实体。
10. 每行分别回写 `RvtID`，不得用共同 `WGroupID` 覆盖行级 ID。

### 建议完整性检查

- Main 的 `start/end` 引用均应存在于 `tbl2.ID`。
- Secondary 的 `connected_main` 应等于同组 Main 的 `start` 引用。
- 被引用 `tbl2.CSection` 必须以 `@PEC` 结尾并可解析四个 H 尺寸。
- Main 应有两个不同端点引用；Secondary 自身不得创建新的 H。
- 引用缺失时仍可创建混凝土墙，但必须记录错误，不能静默复制 H。

## 8. 当前实测 YDB 验证结果

- `tbl1`：0 行。
- `tbl2`：4 行，墙端 H 的偏心和旋转均为 0。
- `tbl3`：`1F=0`、`RF=3300`。
- `tbl4`：3 行，共 2 个逻辑墙组。

具体内容：

- `PECW0001-L1`：I-Main，墙厚 200、腹板 10、3 区隔、0 道加劲板；
  引用 H300×200×8×20 两根。
- `PECW0002-L1`：L-Main，墙厚 300、腹板 10、4 区隔、1 道 250×8
  加劲板；引用 H600×300×7×22 两根。
- `PECW0002-L2`：Secondary，节点长度 968.625419、腹板 12、
  翼缘 300×14，`connected_main` 引用 L-Main 角点 H。
- L 角点 H 计算偏移为 `(600-300)/2=150`。
- SQLite `integrity_check=ok`。

## 9. 参数核对 DXF

生成命令：

```powershell
python .\tools\export_pec_wall_check_dxf.py `
  C:\path\to\dtlmodel.ydb `
  -o .\validation_output\PEC墙参数测量核对.dxf
```

DXF 以 1:1 mm 绘制墙节点线、混凝土轮廓、引用 H、计算偏移、Main 腹板、
加劲板、Secondary 腹板和 T 翼缘。H 的位置与旋转由 v4 规则计算，
不会读取 `tbl2.EccX/EccY/Rotation`。
