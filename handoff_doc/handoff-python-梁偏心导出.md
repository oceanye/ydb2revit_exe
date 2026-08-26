# Handoff：ydb 转换器增加「梁偏心 Ecc」输出

**接收方**：Python 团队（维护 `ydb转换.exe` 的人）
**目标文件**：`C:\Users\bimpub5\PycharmProjects\ydb2revit_exe\ydb转换.py`（单文件）→ 改后需重新 PyInstaller 打包 `dist\ydb转换.exe`
**下游**：C# Revit 插件（`CreateNewExtern`）读 `ydb转换数据库.db` 的 `tbl1` 生成梁，需要梁偏心来在 Revit 中施加"平面内 Y 偏心"。

---

## 1. 一句话目标
转换器把普通柱和独立 PEC 柱偏心导出（`tblColSeg.EccX/EccY/Rotation` →
`tbl2`），但**梁**偏心被丢弃。PEC Main 墙端 H 是例外：其偏心和旋转
按 `WInfo v4` 的墙端逻辑计算，兼容列写 0。
请把 `tblBeamSeg` 里本就存在的 **`Ecc`（梁偏心）** 读出来，**追加**到 `tbl1` 末尾，供下游使用。**只加列、不改动现有列顺序。**

## 2. 已实测的数据事实（无需再调研）
- `tblBeamSeg` 真实列：`ID, No_, StdFlrID, SectID, GridID, Ecc, HDiff1, HDiff2, Rotation, JYDef, …, Ecc2`
- `Ecc` / `Ecc2`：**带符号整数，单位 mm**（实测样例 `+25`、`−23`）。语义 = 梁截面中线在水平面内、垂直梁轴方向的偏移（YJK"梁中线偏心"，正值偏向 YJK 红箭头外侧）。
- 现有模型中 `Ecc == Ecc2` 恒成立（未发现不等的样本）。
- 梁 `Rotation`：度，样例为 0。
- 现状根因：`ydb转换.py` 第 93 行 `SELECT GridID,SectID,StdFlrID,ID,HDiff1,HDiff2 from tblBeamSeg`——没取 `Ecc/Ecc2/Rotation`。

## 3. 输出契约（⚠️ 与 C# 端的接口，必须严格遵守）

改后 `tbl1` 列布局：**前 12 列(col0–col11)保持不变**，新列**只能追加在末尾**：

| 列号 | 列名 | 类型 | 来源 / 含义 |
|---|---|---|---|
| 0–5 | BStartX..BEndZ | REAL | 不变 |
| 6 | BSection | TEXT | 不变 |
| 7 | Tag | INTEGER | 不变 |
| 8 | ID | INTEGER | 不变 |
| 9 | RvtID | TEXT | 不变 |
| 10 | BSConn | REAL | 不变 |
| 11 | BEConn | REAL | 不变 |
| **12** | **Ecc** | **REAL** | **新增**：`tblBeamSeg.Ecc`，mm，带符号（起点端） |
| **13** | **Ecc2** | **REAL** | **新增**：`tblBeamSeg.Ecc2`，mm，带符号（终点端，待确认） |
| **14** | **BRotation** | **REAL** | **新增**：`tblBeamSeg.Rotation`，度 |

**硬性约束**：
1. **新列必须在末尾（≥col12）**。`CombineBeam.cs`、`ReplaceRevitType.cs` 按固定下标读 `tbl1[10]=BSConn`、`tbl1[11]=BEConn`；任何插在前面的改动都会让 C# 全部错位。
2. **原样输出**：mm 不要换算成英尺，符号不要取反。单位换算与符号对齐由 C# 端负责。
3. 装配顺序里**梁和斜撑共用 `b[]`**；斜撑无 Ecc，回填时默认 `0`。

## 4. 具体改动（定位到行，建议 diff）

**(a) 第 93 行 SELECT 增列 + 收集**
```python
# 原:
cursor1 = c.execute("SELECT GridID,SectID,StdFlrID,ID,HDiff1,HDiff2 from tblBeamSeg")
# 改为:
cursor1 = c.execute("SELECT GridID,SectID,StdFlrID,ID,HDiff1,HDiff2,Ecc,Ecc2,Rotation from tblBeamSeg")
becc_raw, becc2_raw, brot_raw = [], [], []
for row in cursor1:
    grid.append(row[0]); bsect.append(row[1]); bstdflr.append(row[2])
    bid.append(row[3]); hd1.append(row[4]); hd2.append(row[5])
    becc_raw.append(row[6]); becc2_raw.append(row[7]); brot_raw.append(row[8])   # 新增
```

**(b) 第 531 行之后，按 `bid` 回填到装配顺序**（仿照现有 z 偏移回填 527–531，也仿照柱 `ceccx1` 444–452）
```python
becc  = [0]*len(bstartx)
becc2 = [0]*len(bstartx)
brot  = [0]*len(bstartx)
for i in range(len(bstartx)):
    for j in range(len(bid)):
        if b[i] == bid[j]:
            becc[i]  = becc_raw[j]
            becc2[i] = becc2_raw[j]
            brot[i]  = brot_raw[j]
            break
```

**(c) tbl1 list build（588–604）增 3 列**
```python
tbl1 = []
for tt in range(15):          # 原 range(12) → 15
    tbl1.append([])
for i in range(0, len(bstartx)):
    ...                        # 原 0..11 不动
    tbl1[12].append(becc[i])   # 新增
    tbl1[13].append(becc2[i])  # 新增
    tbl1[14].append(brot[i])   # 新增
```

**(d) CREATE TABLE tbl1（622–636）末尾增 3 列**
```sql
    BEConn            REAL,
    Ecc               REAL,
    Ecc2              REAL,
    BRotation         REAL);
```

**(e) INSERT tbl1（638）列名同步**
```python
sql_insert = "INSERT INTO tbl1(BStartX,BStartY,BStartZ,BEndX,BEndY,BEndZ,BSection,Tag,ID,RvtID,BSConn,BEConn,Ecc,Ecc2,BRotation) VALUES"
```

**(f) CombineBeam 建表（798–809）加一列 `Ecc`（仅 schema，数据由 C# 写）**
```sql
    ShapeValue    TEXT,
    Info          TEXT,
    Ecc           TEXT);
```
> 说明：`CombineBeam` 表由 Python 建空表、由 C# 端（`CombineBeam.cs` 的 `WriteDb`）写数据。Python 只需把列建出来即可，**不要**在 Python 里给它插梁偏心。

## 5. 待 Python 团队确认的问题
1. **Ecc vs Ecc2**：是否分别 = 起点端 / 终点端偏心？现有模型恒等，无法区分。请对照 YJK 字段文档，或做一个"两端偏心不同"的测试梁验证。若确为两端值，C# 端会分别施加到梁起/终点。
2. **符号正向**：`Ecc>0` 是否对应 YJK 屏显红箭头外侧？请确认 Python 不做任何取反（C# 端会用测试梁最终核对方向）。
3. **Rotation**：梁 Rotation 是否在异形/斜梁里非零、是否需要参与平面内偏移？默认先一并透传（col14）。

## 6. 验收标准
- 转换后 `ydb转换数据库.db` 的 `tbl1` 应含 `Ecc, Ecc2, BRotation` 三列且在末尾。
- 用 `5#宿舍楼-钢框架 test.ydb` 转换后：`SELECT * FROM tbl1 WHERE Ecc<>0` 应返回 **2 行**，`Ecc` 分别为 `25` 和 `-23`。
- `CombineBeam` 表应含 `Ecc` 列（空表即可）。
- 前 12 列布局不变（回归：C# 端 `CombineBeam.cs` 仍能正常合并梁）。

## 7. 参考（背景定义）
详见同目录 `YJK坐标系与梁柱偏心定义.md`（YJK 杆件局部坐标系 U1/U2/U3、梁偏心红箭头约定、Revit 端实现路径）。
