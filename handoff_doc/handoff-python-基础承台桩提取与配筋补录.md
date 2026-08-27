# YDB 基础承台、桩与 Revit 中间库 handoff

Revit 侧“结构信息录入”按钮、数据库版本门禁、网页任务队列、参数回写及
未匹配清单的跨端契约，统一见
[Revit“结构信息录入”与模型参数同步 handoff](./handoff-Revit-结构信息录入与模型同步.md)。

## 1. 强制边界

1. `jccad.ydb` 是承台和桩几何、型号、定位及标高的唯一数据源。
2. 生产提取程序、网页端和 Revit 端均不得读取、解析或依赖 DWG。
3. DWG 只能用于人工验证，不得向中间数据库反向补齐任何字段。
4. 承台只支持单阶矩形或单阶任意闭合多边形，不考虑多阶和圆形承台。
5. 桩只支持竖直圆桩，桩型号仅由直径和长度组成。
6. 不从 YDB 提取承台或桩配筋；配筋由本机网页按型号补录。

## 2. 统一中间数据库架构

上部结构 YDB 与基础 YDB 可以是两个文件，但必须写入同一个
`ydb转换数据库.db`：

```text
上部结构 YDB -> tbl1～tbl4 ─┐
                            ├-> 同一个 ydb转换数据库.db -> Revit API
基础 jccad.ydb -> tbl5～tbl7 ┘                 ↑
                                      网页补录 tbl5/tbl6
```

基础 handoff 固定为三层：

| 表 | 内容 | 粒度 |
|---|---|---|
| `tbl5` | 桩型号：直径、长度及桩配筋 | 一行一种桩型号 |
| `tbl6` | 承台型号：多边形、厚度、桩位及承台配筋 | 一行一种承台型号 |
| `tbl7` | 承台布置：坐标、标高、旋转及所选承台型号 | 一行一个承台布置 |

不单独输出逐根桩实例表。每个布置中的实际桩由 `tbl6.PileLayoutJson`、
`tbl7` 的变换和 `tbl5` 的桩长确定，避免重复存储可计算的数据。

基础提取只重建 `tbl5～tbl7`，不得删除或修改已有 `tbl1～tbl4`；上部结构
提取也不得删除基础表。Revit 端只连接这一份中间数据库。

### 2.1 正式库安全更新协议

`-o` 指向已有统一数据库时，Python/EXE 不得直接修改或删除正式文件：

1. 在正式库同目录创建唯一的 `<数据库名>.pending-*` 暂存库。
2. 用 SQLite 一致性快照复制正式库，所有提取只写暂存库。
3. 上部模式只允许重建 `tbl1～tbl4`；基础模式只允许重建 `tbl5～tbl7`，
   并更新 `handoff_meta` 中的 `Foundation.*`。
4. 上部模式对 `tbl5～tbl7` 的全部字段、全部记录及 `Foundation.*` 计算
   SHA-256；另对全部非目标对象计算保护指纹。任何变化都拒绝替换。
5. 基础模式同样保护 `tbl1～tbl4`、非 `Foundation.*` 元数据及所有其他
   非目标对象。
6. 通过 SQLite `integrity_check`、范围指纹及并发修改复核后，才在同目录
   原子替换正式库。
7. 提取失败、取消、模式误选、正式库并发变化或存在活动 WAL 文件时，正式
   库保持不变；程序只清理 `.pending-*` 文件。

供 Revit 或其他程序调用时必须显式传入预期模式，不能依赖自动判断：

```powershell
ydb转换.exe ".\上部结构.ydb" -o ".\ydb转换数据库.db" --mode upper
ydb转换.exe ".\基础\jccad.ydb" -o ".\ydb转换数据库.db" --mode foundation
```

标准输出为单行 JSON。成功包含 `mode/status/protected_sha256`，上部模式另含
`foundation_sha256`；错误包含 `mode/status/error_type/error`；用户取消返回
`status=cancelled`。约定退出码为成功 `0`、错误 `1`、取消 `2`。

## 3. 型号归并规则

### 3.1 桩型号 `tbl5`

桩型号键由以下内容计算：

- `Diameter`：圆桩直径；
- `Length`：桩长。

坐标、标高和所属承台不参与桩型号归并。稳定键格式为
`PILE-<16位SHA256>`。

### 3.2 承台型号 `tbl6`

承台型号键必须同时包含：

- 单阶多边形外轮廓；
- `Thickness`：承台厚度；
- 完整局部桩位；
- 每个桩位选择的 `tbl5` 桩型号；
- 每个桩位相对承台底的桩顶偏移。

因此，相同外轮廓和厚度但桩位、桩数或桩型号不同的承台，必须是不同
承台型号。实例坐标、底标高和整体旋转不参与型号归并。

多边形和桩位会一起归一化，忽略多边形起始顶点、顺逆时针记录顺序、
局部整体平移和局部整体旋转；镜像不自动归并。稳定键格式为
`CAP-<16位SHA256>`。

`PileLayoutJson` 的每一项固定为：

```json
{
  "x": 1000.0,
  "y": -4000.0,
  "top_offset_z": 0.0,
  "pile_type_id": 1
}
```

其中 `x/y` 是 `tbl6.PolygonJson` 同一局部坐标系中的桩心，
`pile_type_id` 引用 `tbl5.ID`。

## 4. YDB 提取关系与单位

```text
app_dais.kind -> DEF_dais.lID / DEF_dais.DaisFlag
DEF_dais.DaisFlag -> dais_pt.DaisFlag
DEF_dais.DaisFlag -> dais_stepH.DaisFlag
DEF_dais.DaisFlag -> app_Pile.DaisFlag
app_Pile.kind -> DEF_Pile.ID
node.ID = app_dais.nj + 1
```

`app_dais.nj` 是零基索引，`node.ID` 是一基编号，必须执行 `+1`。

YDB 原始承台放置参考点：

```text
SourceX = node.X + app_dais.ex
SourceY = node.Y + app_dais.ey
SourceAngle = app_dais.ang       # 弧度
BottomZ = app_dais.dBotElevat * 1000
```

为保证同型号获得完全相同的局部多边形和桩位，Python 会同时归一化模型并
相应调整布置参考点。输出后，`tbl7.X/Y` 表示 `tbl6` 局部原点 `(0,0)` 的
世界坐标，不保证等于多边形几何中心或原 YDB 节点坐标。

中间数据库长度统一为 mm，`tbl7.Rotation` 统一为角度制。桩长必须取所属
承台实例的 `app_dais.idaispilelen * 1000`；不得使用桩位模板中可能残留的
同名字段。

## 5. 表字段契约

列顺序是 handoff 的组成部分。后续只能在表尾追加双方确认的新列，不得
插入、删除或调整现有列。

### 5.1 `tbl5`：桩型号

| 下标 | 字段 | 来源与含义 |
|---:|---|---|
| 0 | `ID` | 当前数据库内的桩型号编号 |
| 1 | `TypeKey` | 稳定型号键 |
| 2 | `Diameter` | YDB：桩直径，mm |
| 3 | `Length` | YDB：桩长，mm |
| 4 | `UserTypeName` | 网页：Revit 桩类型名称 |
| 5 | `LongitudinalRebar` | 网页：纵筋 |
| 6 | `StirrupRebar` | 网页：一般段箍筋或螺旋筋 |
| 7 | `DenseStirrupRebar` | 网页：加密段箍筋或螺旋筋 |
| 8 | `DenseZoneLength` | 网页：加密区长度，mm |
| 9 | `Cover` | 网页：保护层，mm |
| 10 | `Notes` | 网页：备注 |
| 11 | `ExtraJson` | 网页：扩展结构化参数 |
| 12 | `UpdatedAt` | 网页：最后保存时间 |

### 5.2 `tbl6`：承台型号

| 下标 | 字段 | 来源与含义 |
|---:|---|---|
| 0 | `ID` | 当前数据库内的承台型号编号 |
| 1 | `TypeKey` | 稳定型号键 |
| 2 | `PolygonJson` | 计算：归一化逆时针局部多边形，不重复首点 |
| 3 | `Thickness` | YDB：承台厚度，mm |
| 4 | `PileLayoutJson` | 计算：局部桩位及 `tbl5.ID` 引用 |
| 5 | `UserTypeName` | 网页：Revit 承台类型名称 |
| 6～9 | `BottomX,BottomY,TopX,TopY` | 网页：底筋和顶筋表达 |
| 10 | `SideRebar` | 网页：侧面钢筋 |
| 11 | `Cover` | 网页：保护层，mm |
| 12 | `Notes` | 网页：备注 |
| 13 | `ExtraJson` | 网页：扩展结构化参数 |
| 14 | `UpdatedAt` | 网页：最后保存时间 |

顶点数、每台桩数、型号布置数和展开桩数均可由三张表计算，不作为数据库
冗余列；网页 API 展示时会动态计算这些统计值。

### 5.3 `tbl7`：承台布置

| 下标 | 字段 | 含义 |
|---:|---|---|
| 0 | `X` | `tbl6` 局部原点的世界 X，mm |
| 1 | `Y` | `tbl6` 局部原点的世界 Y，mm |
| 2 | `BottomZ` | 承台底绝对标高，mm |
| 3 | `Rotation` | 型号局部坐标到世界坐标的旋转角，度 |
| 4 | `CapTypeID` | 选择的承台型号，引用 `tbl6.ID` |
| 5 | `Tag` | 状态预留，初始为 0 |
| 6 | `ID` | 当前提取内连续布置编号 |
| 7 | `RvtID` | Revit 创建承台后回写的 ElementId |

辅助表 `handoff_meta` 使用 `Foundation.` 前缀保存 YDB 唯一数据源标识、
源文件 SHA256、提取时间、架构版本等。Revit 创建基础几何时不得用该表
替代 `tbl5～tbl7`，但执行“结构信息录入”及参数同步前必须读取其中的全局
数据集、版本和指纹字段完成门禁。

## 6. 局部模型到 Revit 世界坐标

令：

```text
ox = tbl7.X
oy = tbl7.Y
z0 = tbl7.BottomZ
theta = radians(tbl7.Rotation)
```

`tbl6.PolygonJson` 和 `PileLayoutJson` 中任一点 `(lx,ly)` 的世界坐标：

```text
WorldX = ox + lx*cos(theta) - ly*sin(theta)
WorldY = oy + lx*sin(theta) + ly*cos(theta)
```

承台竖向范围：

```text
CapBottomZ = z0
CapTopZ = z0 + tbl6.Thickness
```

每个局部桩位的竖向范围：

```text
PileTopZ = z0 + pile.top_offset_z
PileBottomZ = PileTopZ - tbl5[pile.pile_type_id].Length
```

## 7. 网页补录规则

网页无 CDN、无外网请求、无文件上传，并且只允许更新 `tbl5` 和 `tbl6` 中
标注为“网页”的字段，不得修改型号几何、桩位或 `tbl7` 布置。命令行独立
启动时默认绑定 `http://127.0.0.1:8765/`；由 Revit“结构信息录入”按钮启动
时应使用仅限本机的动态空闲端口、一次性令牌和文档会话绑定，不固定占用
`8765`。

提取基础并打开补录页时，输出目标必须是现有中间数据库：

```powershell
python .\ydb转换.py ".\基础部分模型\jccad.ydb" `
  -o ".\ydb转换数据库.db" --web
```

只打开已有中间数据库：

```powershell
python .\foundation_web.py ".\ydb转换数据库.db"
```

重新提取时会重建型号几何和布置，但按未变化的 `TypeKey` 恢复网页补录
字段。钢筋可先使用 `C20@150`、`20C22` 等设计表达；新增结构化参数放入
`ExtraJson`。

## 8. Revit API 读取与建模顺序

1. 读取 `tbl5`，按 `ID` 建立桩型号字典并创建或匹配桩族类型。
2. 读取 `tbl6`，解析多边形、厚度和桩位；检查每个 `pile_type_id` 均能在
   `tbl5.ID` 中找到。
3. 读取 `tbl7`，通过 `CapTypeID` 取得承台型号。
4. 按第 6 节公式变换多边形并创建单阶承台；长度从 mm 统一换算为 Revit
   内部英尺。
5. 遍历该型号的 `PileLayoutJson`，计算每根竖直桩的世界 XY、桩顶和桩底，
   再按对应 `tbl5` 型号创建桩。
6. 将 `tbl5/tbl6` 的网页补录字段写入相应 Revit 类型或实例参数；是否创建
   真实钢筋实体由 Revit 端后续单独决定。
7. 承台创建成功后可回写 `tbl7.RvtID`。当前没有逐桩数据库行，因此不要
   假设存在逐桩 `RvtID`；如后续确需回写，应在 handoff 中另行追加字段。
8. `CapTypeID`、`pile_type_id` 缺失，轮廓无效或标高方向错误时必须整体
   停止并回滚，不得猜测或静默跳过。

Revit 端不得读取 YDB 或 DWG，也不得重新计算型号归并键。

本节只规定首次建模所需的基础几何读取顺序。网页补录后的参数同步不得由
Web 线程直接调用 Revit API，须按跨端同步 handoff 经 `ExternalEvent`、版本
快照和事务门禁执行。

## 9. 当前样本结果

从 `基础部分模型/jccad.ydb` 仅通过 YDB 提取：

- `tbl5` 桩型号：1，D1000、L31000 mm，展开后 173 根；
- `tbl6` 承台型号：5；
- `tbl7` 承台布置：88；
- 数据源标识：`handoff_meta['Foundation.DataSource'] = YDB_ONLY`。

当前 5 种承台型号的每台桩数分别为 1、1、2、2、3；承台布置展开后的
桩总数为 173。

## 10. 明确拒绝的输入

以下情况程序必须报错，不能静默猜测：

- 多阶或圆形承台；
- 相对承台底标高；
- 斜桩或矩形桩；
- 当前未经验证的独立单桩；
- 缺失节点、轮廓、厚度、桩长或桩直径；
- 声明桩数与桩位模板数量不一致。
