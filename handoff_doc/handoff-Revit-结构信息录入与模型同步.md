# Revit“结构信息录入”与模型参数同步 handoff

> 状态：跨端实施契约。当前 Python 仓库已具备本地网页和 SQLite 补录能力，
> 但本仓库不包含现有 `CombineBeam` 的 Revit C# 源码；本文件用于后续
> Revit Add-in 按同一契约实现按钮、版本核对、`ExternalEvent` 更新和结果清单。

## 1. 目标与范围

在现有 Revit 功能区中，除 `CombineBeam` 生成模型功能外，新增按钮：

```text
结构信息录入
```

按钮完成以下闭环：

1. 识别当前 Revit 模型绑定的唯一 `ydb转换数据库.db`。
2. 核对模型、数据库的数据集身份、架构版本和同步版本。
3. 启动仅绑定本机的补录服务器，并提示用户打开网页。
4. 用户在网页中录入或修改允许人工补录的结构参数。
5. 网页执行“检查差异”，通过门禁后允许点击“更新模型”。
6. Revit 在主线程事务中把已配置映射的参数写入匹配元素。
7. 将未匹配、歧义、缺参或写入失败项形成可筛选清单，不得静默跳过。

本功能第一阶段只更新 Revit 参数，不创建、删除或移动几何实体。几何重建
仍由现有建模命令负责。

## 2. 总体架构与职责边界

```text
Revit Ribbon：结构信息录入
       |
       | 解析当前 Document 与绑定数据库、执行版本门禁
       v
Revit Add-in 启动本机 Web 子进程
       |                         |
       | 轮询同步任务             | SQLite 事务
       v                         v
ExternalEvent <----任务队列---- 本机网页 ----> ydb转换数据库.db
       |
       | Revit API 主线程 TransactionGroup
       v
匹配元素、更新参数、回传结果清单
```

职责必须严格分离：

- 网页服务器负责数据库补录、校验、版本快照、任务排队和结果展示。
- 网页服务器线程不得直接调用 Revit API。
- Revit Add-in 负责当前文档校验、元素匹配、参数写入和事务。
- 所有 Revit API 调用必须由 `ExternalEvent` 在 Revit 主线程执行。
- YDB 和 DWG 均不由 Revit 端读取；Revit 只读取中间数据库。

## 3. 用户操作流程

### 3.1 点击“结构信息录入”

Revit Add-in 按顺序执行：

1. 确认当前存在可修改的 Revit `Document`，排除族文档、无文档状态和只读
   文档。
2. 从模型绑定信息取得中间数据库规范化路径，不允许临时选择另一个数据库
   绕过身份校验。
3. 读取 `handoff_meta`，执行第 6 节的身份和版本门禁。
4. 检查当前文档是否已有服务器会话；已有则复用，不重复启动。
5. 启动隐藏的本机服务器进程，等待 `/api/health` 返回就绪。
6. 弹出 Revit `TaskDialog`，显示数据库名称、数据集 ID、版本状态和网页地址，
   提供“打开网页”按钮；也可在确认后自动打开默认浏览器。

任一步失败都必须在 Revit 中给出明确错误，不得退回到任意数据库或验证库。

### 3.2 网页补录

网页顶部固定展示：

- 当前 Revit 文档名称；
- 规范化数据库路径；
- `DatasetId`；
- 数据库几何版本、数据版本；
- 模型最后同步版本；
- 版本状态：绿色、黄色、橙色或红色；
- Revit 会话是否在线。

网页操作区至少包括：

```text
保存录入    检查差异    更新模型
```

- 有未保存字段时禁用“更新模型”。
- “检查差异”生成同步预览，不写 Revit。
- “更新模型”只能使用最近一次预览对应的数据库版本；数据库再次变化后，
  旧预览立即失效。
- 空白输入默认表示“不更新 Revit 参数”，不得用空字符串覆盖现有参数。
  如需清空参数，必须提供单独的“明确清空”操作。

### 3.3 点击“更新模型”

1. 网页以当前 `DatasetId + GeometryRevision + DataRevision` 创建同步任务。
2. Revit Add-in 后台轮询取得任务，确认任务的 `ModelBindingId` 与当前文档
   ExtensibleStorage 中的绑定值一致，然后调用 `ExternalEvent.Raise()`。
3. `ExternalEvent` 处理器重新读取版本；与预览版本不一致时拒绝执行。
4. 在 `TransactionGroup` 中匹配元素并更新允许的参数。
5. 版本错误属于致命错误，整体回滚；单个元素未匹配或参数不可写则记入结果
   清单，不阻止其他已匹配元素更新。
6. 提交前再次核对 `DataRevision`。补录期间数据库又被修改时整体回滚。
7. Revit 将结果和模型新的同步版本回传服务器，网页展示清单。

## 4. 服务器启动与通信契约

Revit 调用 YDB 提取命令时，必须传入显式 `--mode upper` 或
`--mode foundation`，并只按标准输出的单行 JSON `mode/status` 判断结果。
不得以进程退出但没有成功 JSON 作为可覆盖正式库的依据。Python/EXE 内部
已按同目录 `.pending-*`、范围 SHA-256、完整性校验和原子替换保护统一数据库。

### 4.1 启动模式

需要给打包程序增加“只编辑现有数据库”的正式入口，建议命令契约为：

```text
ydb转换.exe --edit-db <ydb转换数据库.db> --revit-session <SessionId> --port 0
```

要求：

- `--edit-db` 不执行 YDB 提取，只打开指定的现有中间数据库。
- 服务只绑定 `127.0.0.1`；`--port 0` 由系统选择空闲端口。
- 服务器生成一次性会话令牌，并通过标准输出 JSON 握手返回实际 URL。
- Revit 使用隐藏子进程启动；一个 Revit 文档只允许一个活动会话。
- 文档关闭、切换项目或 Revit 退出时终止对应服务器。
- 服务器意外退出时网页和 Revit 均显示离线，不得继续同步。

### 4.2 建议 API

| 方法与地址 | 调用方 | 用途 |
|---|---|---|
| `GET /api/health` | Revit | 服务就绪及数据库只读健康检查 |
| `GET /api/session` | 网页/Revit | 数据集、版本和文档会话状态 |
| `GET /api/data` | 网页 | 读取允许补录的数据 |
| `PUT /api/...` | 网页 | 保存字段并增加 `DataRevision` |
| `POST /api/sync/preview` | 网页 | 请求 Revit 生成匹配预览 |
| `POST /api/sync/request` | 网页 | 创建正式更新任务 |
| `GET /api/revit/jobs/next` | Revit | 获取当前文档待处理任务 |
| `POST /api/revit/jobs/{id}/result` | Revit | 回传更新结果和未匹配清单 |
| `GET /api/sync/jobs/{id}` | 网页 | 轮询任务进度和结果 |

每个请求都必须同时携带会话令牌、`SessionId`、`ModelBindingId` 和
`DatasetId`。仅知道本机端口不足以取得写权限。

## 5. 数据库身份与版本字段

现有 `handoff_meta` 应扩展以下数据库级键：

| 键 | 规则 |
|---|---|
| `Handoff.DatasetId` | 数据集永久 UUID；同一个正式数据库首次建立后不得改变 |
| `Handoff.SchemaVersion` | 中间库总体架构版本，不等同于单个基础模块版本 |
| `Handoff.GeometryRevision` | YDB 几何指纹实际变化时递增 |
| `Handoff.DataRevision` | 任一网页补录事务成功后递增 |
| `Handoff.GeometryFingerprint` | `tbl1～tbl7` 建模字段的规范化摘要 |
| `Handoff.LastModifiedAt` | 最后一次成功事务时间 |
| `Upper.SourceSHA256` | 当前上部结构 YDB 摘要 |
| `Foundation.SourceSHA256` | 当前基础 YDB 摘要 |

版本递增必须与数据修改处于同一个 SQLite 事务。重复提取完全相同的数据时
不得无意义增加 `GeometryRevision`。

建议增加补录审计表 `handoff_change_log`，记录数据版本、表、记录键、字段、
旧值、新值、时间和会话 ID。它用于追溯，不参与 Revit 几何建模。

## 6. Revit 模型绑定与版本门禁

### 6.1 模型级绑定信息

每个 Revit 文档必须保存：

- `ModelBindingId`：模型首次绑定时生成的 UUID；
- `DatasetId`；
- 规范化数据库路径；
- 已支持的 `SchemaVersion`；
- `LastGeneratedGeometryRevision`；
- `LastGeneratedGeometryFingerprint`；
- `LastSuccessfulDataRevision`；
- `LastAttemptedDataRevision`、`LastSyncStatus` 和 `LastSyncJobId`；
- 首次绑定时间和最后同步时间。

建议使用 Revit `ExtensibleStorage` 保存同步元数据，避免依赖用户可随意修改
的普通项目参数。`ModelBindingId` 是本插件生成并维护的值，不应假定 Revit
`Document` 存在可直接用作此目的的永久唯一 ID。最终存放位置需在 Revit
端技术评审时确认。

`LastGeneratedGeometryRevision` 只能由基于该数据库的建模命令写入；单纯
参数同步不得把它推进到数据库的新几何版本。仅当本轮所有应同步记录均为
`UPDATED` 或 `NO_CHANGE` 时，才推进 `LastSuccessfulDataRevision`。存在未匹配
或写入失败时，保留原成功版本，只记录本轮尝试版本及 `PARTIAL` 状态，避免
模型被误标为“完全同步”。

### 6.2 门禁状态

| 状态 | 条件 | 行为 |
|---|---|---|
| 绿色：一致 | Dataset、Schema、GeometryRevision 均与模型最后建模版本一致 | 可直接检查参数差异 |
| 黄色：数据库参数较新 | 几何一致，`DataRevision` 高于模型 | 允许预览和参数更新 |
| 橙色：同数据集但几何有差异 | Dataset 一致，几何版本或指纹不同 | 必须先生成全量差异清单 |
| 红色：禁止 | Dataset 不同、Schema 不兼容、数据库版本回退或绑定缺失 | 禁用“更新模型” |

数据库路径变化但 `DatasetId` 相同也不得自动接受。应提示“数据库已移动或为
副本”，在核对源文件摘要、版本和几何指纹后执行显式重新绑定。

### 6.3 过大差异门禁

预览必须按梁、柱、墙、桩型号、承台型号、承台布置分别统计：

- 数据库记录数；
- Revit 候选数；
- 唯一匹配数；
- 数据库有而模型无；
- 模型有而数据库无；
- 多重匹配；
- 几何或类型冲突。

定义：

```text
UnmatchedRatio = (数据库有模型无 + 模型有数据库无 + 多重匹配)
                 / max(数据库候选数, Revit候选数, 1)
```

项目配置应提供 `MaxUnmatchedCount` 和 `MaxUnmatchedRatio`。超过任一阈值时
禁用自动“更新模型”，只允许查看和导出清单。具体阈值应由项目负责人确认，
不得散落硬编码在网页或 Revit 命令中。

无论阈值如何，`DatasetId` 不一致永远是硬阻断条件。

## 7. 元素稳定匹配

仅依赖 `RvtID` 或当前连续 `ID` 不足以核对版本：ElementId 会因复制、重建
或模型分离而变化，连续 ID 也可能因重新提取而移动。

在实施模型同步前，应在不改变既有列顺序的前提下，在实例表末尾追加稳定
同步键：

- `tbl1.SyncKey`：梁/支撑实例；
- `tbl2.SyncKey`：柱实例；
- `tbl4.SyncKey`：墙肢实例；
- `tbl7.SyncKey`：承台布置实例；
- `tbl5.TypeKey`、`tbl6.TypeKey` 已作为型号稳定键。

`SyncKey` 是输出 handoff 的派生摘要，不是 YDB 内部 `SectID/JtID/FloorID`
等原始编号。Revit 元素应保存 `DatasetId + SourceTable + SyncKey + TypeKey`。

匹配顺序固定为：

1. `DatasetId + SourceTable + SyncKey` 唯一匹配；
2. `RvtID` 只作为加速索引，并必须反向验证 SyncKey；
3. 几何近似匹配只用于生成“候选建议”，不得自动更新；
4. 无唯一结果即进入未匹配清单。

对于 `CombineBeam` 生成的一根 Revit 梁对应多条 `tbl1` 的情况，Revit 元素
必须保存全部源 `SyncKey` 或一个可追溯的组合键。各源记录的待写参数不一致
时标记 `COMBINE_PARAMETER_CONFLICT`，不得任意取第一条。

## 8. 参数映射与更新规则

Revit 参数“具体位置待定”不能由同步代码猜测。实施前必须形成参数映射表，
每项至少明确：

- 数据库表和字段；
- Revit 类别；
- 写入类型参数还是实例参数；
- 参数名称或 Shared Parameter GUID；
- 数据类型和单位；
- 是否必填；
- 空值策略；
- 同一 Revit 类型出现多种数据库值时的冲突策略。

第一阶段建议范围：

- `tbl5` 的桩配筋补录字段写入桩类型参数；
- `tbl6` 的承台配筋补录字段写入承台类型参数；
- 后续 `tbl1～tbl4` 需要补录的梁、柱、墙字段沿用同一白名单机制扩展；
- 未配置映射的字段只保存在数据库，不写 Revit，并列为
  `PARAMETER_MAPPING_MISSING`。

执行规则：

1. 只更新白名单映射字段，不遍历并覆盖所有同名参数。
2. `NULL` 或空白默认跳过；明确清空必须由独立操作表达。
3. 写入前完成字符串、整数、长度和角度的显式类型及单位转换。
4. 参数不存在、只读、公式驱动或组约束时记录失败原因。
5. 类型参数冲突时不得拆类型或改族名，除非另有明确流程。
6. 同步任务开始后锁定其数据库版本；版本变化立即回滚。

## 9. 未匹配与异常清单

同步结果至少分为：

| 状态码 | 含义 |
|---|---|
| `UPDATED` | 已匹配并成功更新 |
| `NO_CHANGE` | 已匹配但值相同 |
| `DB_ONLY` | 数据库有记录，Revit 无元素 |
| `MODEL_ONLY` | Revit 有绑定元素，数据库无记录 |
| `AMBIGUOUS` | 一个键匹配多个候选 |
| `TYPE_CONFLICT` | 类型共享或组合梁参数冲突 |
| `PARAMETER_MISSING` | Revit 参数不存在 |
| `PARAMETER_READ_ONLY` | 参数不可写 |
| `INVALID_VALUE` | 值或单位转换失败 |
| `VERSION_BLOCKED` | 身份或版本门禁失败 |

网页结果表应支持按以下字段筛选：

- 状态；
- 梁/柱/墙/桩/承台类别；
- 楼层；
- 数据库表、ID、SyncKey；
- Revit ElementId；
- 类型名称；
- 原因；
- 建议检查动作。

清单支持导出 CSV 和 JSON。未匹配项本轮只供筛选、定位和检查，不允许网页
直接删除数据库记录或 Revit 元素。

## 10. Revit 事务和并发

- 一个同步任务对应一个 `TransactionGroup`。
- 身份、版本、数据库快照或活动文档错误属于致命错误，整体回滚。
- 已识别的单元素匹配/参数错误记录到清单，可继续处理其他元素。
- 提交前重新读取 `DatasetId/GeometryRevision/DataRevision`；任何变化都回滚。
- 同一数据库同一时刻只允许一个 Revit 更新任务。
- 多开网页只能共享一个服务器会话，不得产生并发写事务。
- 当前活动文档改变时立即暂停任务并将网页切换为只读状态。

## 11. 实施分期

### 阶段 A：身份和稳定键

1. 扩展 `handoff_meta` 的全局 Dataset/Revision/Fingerprint 字段。
2. 给需要同步的实例表在末尾追加 `SyncKey`。
3. 规定 Revit 模型级和元素级绑定存储方式。
4. 完成旧数据库、旧模型的显式绑定/迁移工具。

### 阶段 B：按钮和服务器生命周期

1. 在 `CombineBeam` 同一 Ribbon 区域增加“结构信息录入”。
2. 实现数据库解析、门禁、隐藏启动、健康检查、打开网页和关闭清理。
3. 给打包程序增加 `--edit-db` 会话模式。

### 阶段 C：预览和参数更新

1. 建立参数映射白名单。
2. 实现 Revit 后台任务轮询和 `ExternalEvent`。
3. 实现匹配预览、阈值门禁、事务写入和版本回写。

### 阶段 D：清单和验收

1. 实现筛选、定位、CSV/JSON 导出。
2. 覆盖模型复制、数据库移动、版本回退、组合梁冲突和并发修改测试。
3. 完成用户验收后，才允许在正式模型启用“更新模型”。

## 12. 验收标准

- Dataset 不一致时无法通过任何 UI 路径执行更新。
- 网页服务器仅监听本机，且会话与当前 Revit 文档一一绑定。
- 网页线程不直接调用 Revit API；全部更新经 `ExternalEvent`。
- 预览与正式更新使用相同的数据库版本快照。
- `tbl1～tbl7` 现有列顺序不被破坏，新键只追加在末尾。
- 已匹配参数正确更新；空白值不会误清空模型参数。
- 未匹配和失败项全部进入清单，并可筛选、定位和导出。
- 数据库在更新中改变时 Revit 事务整体回滚。
- 上部结构和基础仍使用同一个 `ydb转换数据库.db`。
- 整个生产流程不读取 DWG。

## 13. 当前实现边界

当前仓库已经具备：

- YDB 向同一 SQLite 中间库写入上部结构及基础数据；
- `tbl5/tbl6` 的本机网页补录；
- 网页请求令牌和仅本机监听能力。

以下内容仍是本 handoff 规定的待实施项，不能因文档已完成而视为现有功能：

- `handoff_meta` 的全局 Dataset、Revision、Fingerprint 及审计记录；
- `tbl1/tbl2/tbl4/tbl7` 的稳定 `SyncKey`；
- 打包程序的 `--edit-db`、会话握手和 Revit 同步任务 API；
- Revit C# 端 Ribbon 按钮、ExtensibleStorage 绑定、`ExternalEvent`、参数映射、
  结果回传和筛选界面。

在阶段 A 的身份与稳定键落地前，不得先开放“更新模型”按钮；否则无法证明
当前模型与数据库属于同一套数据，也无法可靠区分未匹配和错误匹配。
