# handoff-插件端：柱偏心 EccX/EccY 整体反号修正

> 提出方：Python 转换器侧（dev-wangxinyu）
> 日期：2026-09-07
> 优先级：高（全楼 1245/1759 根柱位置偏移 300~600mm）
> 状态：待插件端确认与修复

## 1. 现象

桌面《项目1.rvt》（2026-09-04 由当前 `ydb转换数据库.db` 导入，源
`颛桥测试版\dtlmodel.ydb`）中，同一根柱的四个层段 Revit ID
223080 / 223555 / 223867 / 224099 平面定位全部偏移。该柱在盈建科
对应：图层 11-0-0、标准层号 3、节点编号 341。

经查转换链路（ydb → tbl2 → Revit）数据全部一致，**转换器无罪**；
根因在插件端柱偏心公式：**偏移向量整体反号**。

## 2. 问题柱的定量核对

YDB（dtlmodel.ydb，标准层3 = StdFlrID 54843）：

- 节点 No_341（ID 55185）：X=−15408.606，Y=93657.167 ——与 tbl2.CStartX/CStartY **逐位一致**
- 柱 No_109（ID 58284）：SectID 5192 = H500X200X10X25@PEC，
  EccX=0，EccY=−150，Rotation=180°，HDiffB=0
- 该柱在标准层 2/3/4/5 连续（2F 段 SectID 8041），偏心相同

插件放置（`SqliteDataToRevit.cs:735-741` 现公式）：

```
θ=180°, Ecc=(0,−150)
offsetX = 0·cos180 + (−150)·sin180 = 0
offsetY = 0·sin180 − (−150)·cos180 = −150
→ Revit 柱中心 = 节点 + (0, −150) = (−15408.606, 93507.167)
```

YJK 真值（见 §3 推导）：

```
Δx = −EccX·cosθ − EccY·sinθ = 0
Δy = −EccX·sinθ + EccY·cosθ = +150
→ YJK 柱中心 = 节点 + (0, +150) = (−15408.606, 93807.167)
```

**偏差 = Y 向 300mm**（插件把柱放到了节点的镜像侧）。

## 3. 根因：插件公式是 YJK 真值公式的逐项相反数

### 3.1 YJK 真值约定（官方接口文档 + 本项目双源实证）

盈建科公开资料《常用减震单元局部坐标轴在 YJK 软件中的正确设置》
（https://www.yjk.cn/article/1194/）明确了杆件局部轴的基本约定：

- 1-2-3 轴右手系；**2 轴 = 截面 Y，3 轴 = 截面 X**；
- 偏心 EccX/EccY 按**局部（随转角旋转后的）坐标系**表达；
- **转角绕 1 轴逆时针为正**。

竖直柱：U1 = 起点→终点 = +Z；U2 = 整体 +Y（同济论文及本项目
`YJK坐标系与梁柱偏心定义.md` §3）；右手系 ⇒ U3 = U1×U2 = **−X**。

随转角 θ（逆时针）旋转后的局部轴：

```
U3(θ) = (−cosθ, −sinθ)     ← 截面X（EccX 方向）
U2(θ) = (−sinθ,  cosθ)     ← 截面Y（EccY 方向）
```

故 YJK 真值偏移：

```
Δx = −EccX·cosθ − EccY·sinθ
Δy = −EccX·sinθ + EccY·cosθ
```

### 3.2 实证锚点：PEC 墙端 H（4/4 精确吻合）

`PEC剪力墙测试\dtlmodel_PECWall.ydb`（标准层1）的墙端 H 柱在 YDB
里存有 YJK 自己的 EccX/EccY（rot 全为 0），而其真实形心可由已联调
验证的墙几何规则独立算出（`handoff-python-PEC墙提取与Revit建模.md`
§6.2：外端 H 向墙内 h/2；L 角 H = (hH−Secondary厚)/2）：

| 柱 | 几何真值偏移 | YDB ecc | 按真值公式 | 吻合 |
|---|---|---|---|---|
| No_2（墙start） | (0, +122) | (0,+122) | (0,+122) | ✓ |
| No_3（墙end） | (0, −122) | (0,−122) | (0,−122) | ✓ |
| No_4（墙start） | (0, +122) | (0,+122) | (0,+122) | ✓ |
| No_1（L角） | (0, −34.5) | (0,−35) | (0,−34.5) | ✓ |

（122 = h/2 = 244/2；34.5 = (244−175)/2。）

即 **rot=0 时 YJK 的 Δ = (−EccX, +EccY)**——EccY 正值对应 +Y，
与插件现公式 θ=0 时给出的 (EccX, −EccY) **Y 分量反号**。

### 3.3 反号的全称性

插件现公式（`SqliteDataToRevit.cs:736-741`）：

```
Δx = +EccX·cosθ + EccY·sinθ
Δy = +EccX·sinθ − EccY·cosθ
```

与 §3.1 真值逐项比较，对**任意** EccX/EccY/θ：

```
Δplugin = −ΔYJK
```

即：**每根偏心柱都被放到节点的 diametral 镜像侧，误差 = 2×|ecc|。**

旁证（设计意图自洽性）：颛桥全楼偏心幅值恰为 h/2（150/200/250/300
对应 H300~H600）——"柱边贴轴"做法。θ=90 的柱只有按"偏心在旋转后
的局部系"解释才能落在自身深度轴上（贴轴才有意义）；按全局系解释则
沿宽度方向偏移一个整宽，无物理意义。

### 3.4 为什么现在才暴露

- 地下室（1F/2F 混凝土层）柱 ecc=0（337 根全零）→ 不受影响；
- PEC 墙端 H（ floors 1-2）走"墙端点+墙方向"规则，不读 ecc → 不受影响；
- 上部钢结构层（3F~16F，大量 h/2 偏心柱）**项目1.rvt 才首次全量导入**
  → 本次集中显现。手边统计：导出 1759 根柱中 1245 根带偏心，全部偏移
  300~600mm（rot 分布：0°×434、90°×490、180°×313、270°×8）。

## 4. 修正方案（插件端，一处两行）

`SqliteDataToRevit.cs` `CreateColumnInstance` 内（现 736-741 行），
两个偏移分量**各取相反号**（等价于按 U2/U3 局部轴直接推导）：

```csharp
// 修正前
double offsetX =
    columnRecord.EccX * Math.Cos(rotationRadians) +
    columnRecord.EccY * Math.Sin(rotationRadians);
double offsetY =
    columnRecord.EccX * Math.Sin(rotationRadians) -
    columnRecord.EccY * Math.Cos(rotationRadians);

// 修正后
double offsetX =
    -(columnRecord.EccX * Math.Cos(rotationRadians) +
      columnRecord.EccY * Math.Sin(rotationRadians));
double offsetY =
    -(columnRecord.EccX * Math.Sin(rotationRadians) -
      columnRecord.EccY * Math.Cos(rotationRadians));
```

旋转本身不用动：`RotateColumn`（:938-950）绕 +Z 逆时针，与 YJK
"转角绕 1 轴逆时针为正"一致，截面朝向是对的；错的只是平移向量。

墙端 H 分支（`pecWallPlan.TryGetPlacement` 命中者）不读 ecc，不受
本次修改影响。

## 5. 验收基准

1. **问题柱**：重导后节点341 柱（tbl2.ID=549 等 5 段）中心应为
   (−15408.606, 93807.167)，即节点 +Y 侧 150mm（Revit 中柱边距
   节点沿 Y 为 −100/+400，因 h=500）。
2. **YJK 对照（1 分钟）**：在盈建科标准层3 点选节点341 的柱，
   确认柱中心在节点 +Y 侧 150mm（与上式一致即互证）。
3. **全楼统计**：1245 根偏心柱全部翻到正确侧；抽 3 类各 1 核对：
   - θ=0、EccY=+200（H400，应为节点 +Y 侧 200）；
   - θ=90、EccY=−250（H500，应为节点 **+X** 侧 250；
     真值 Δ=(−EccY·sin90,…)=(+250, 0)）；
   - EccX=100 且 EccY=100 的 2 根（rot=0，应为节点 (−100, +100)）。
4. **回归项**：ecc=0 柱（含全部地下室柱）位置不变；PEC 墙端 H
   位置不变；柱顶标高/HDiff 逻辑不受影响。

## 6. 遗留讨论项（不阻塞本次修正）

- 非对称截面（如 L 形柱）在 θ=0 时截面 X 轴沿 −X（镜像）——对称
  截面无感，非对称截面若发现贴边方向反了，需在族放置时补一个局部
  X 镜像，届时另立 handoff。
- 转角方向（逆时针为正）还需用第 5 节 θ=90 样本与 YJK 屏显做最终确认；
  若验收不符（应为 +X 却见 −X），则只调整 sin 项方向，不能撤销已由
  θ=0/180 样本确认的整体反号。
