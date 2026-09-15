# Revit 交接：筏板方向修复与合并验收

日期：2026-09-15。交付分支：`main`。
合并来源：Gitea `dev-wangxinyu`，代码 `7b72be0`、分支末端 `4c5c412`；合并前主线 `35c3f30`。

## 实际变化

`foundation_handoff._clean_polygon` 在去掉相邻重复点、末尾闭合点并检查点数及面积后，
对负有向面积的轮廓反转顶点顺序。`tbl8.PolygonJson` 按从 +Z 向 XY 平面俯视的方向逆时针输出，
鞋带和为正。输出无重复末尾点，Revit 负责首尾闭合。

本次不改变 SQLite 列定义、单位或契约版本：

- `Foundation.Scope=RAFT_FREE_PILES`
- `Foundation.ContractVersion=RAFT_FREE_PILES_V1`
- `Foundation.ContractTables=tbl5,tbl8,tbl9`
- 桩原始标高仍写 `tbl9.Z`，`Foundation.PileZSemantics=RAFT_BOTTOM_CANDIDATE`；标高语义仍需既有联调确认。

主线此前的上部结构和 Kind=209 解析代码保持原样；本次没有更改 HN400×200 的厚度规则。

## Revit 端重点

1. 重新转换源 YDB 后再导入，旧中间库的顶点顺序不会自动更新。可保留插件侧反转和日志兜底。
2. **不能把 RegionKey 变化直接视为新增楼板。** 它是包含顶点顺序的内容哈希；方向归一化会改变原顺时针区域的键。
   重导前保留旧库及 Revit 元素对应关系，确认同一源模型后按 SourceLID、标高、厚度及轮廓几何核对迁移。
   当前源表 `RaftSlab.lID → RaftCornerPoint.RaftID` 用于几何关联；输出 `SourceRaftID`、`SourceLID` 均取该 lID，
   不要误认为 `SourceRaftID` 保存的是 `RaftSlab.ID`。
3. 转换会重建 tbl8/tbl9 并清空 RvtID，Python 不自动迁移旧元素关联。创建成功后逐行回写，楼板与桩分别处理；
   正式插件须验证重复导入不会多建模型，本仓库没有完成该 Revit 实机验收。
4. 方向归一化不能证明边界有效。Python 当前只检查去重后的点数及非零面积；
   非相邻重复点、非零面积自交仍需 Revit 建模前校验。原回复中“均已硬拒绝”的表述已修正。
5. 自由桩不因区域方向变化而移动、复制或删除；HostRegionID 为空仍按原约定保留并提示。

## 本地实模核验

本次用本地 `基础测试模型/颛桥整体桩基-Jccad_0.ydb` 与合并前主线转换结果逐表比较，
另执行新 EXE 转换，与 Python 输出核对。此文件与原分支 handoff 所列文件名不同，不混称同一份源文件。

| 项目 | 结果 |
|---|---|
| 全量回归 | 62 passed |
| 筏板区域 | 14，全部逆时针；顶点集合、厚度、标高不变 |
| 自由桩 | 801，tbl9 全行与合并前一致 |
| Python / EXE | tbl5–tbl9 全行一致 |
| 旧 jccad 承台模型 | 5 种承台、88 个布置；tbl5/tbl6/tbl7 全行不变 |
| 同一目标库先基础后上部、再基础 | 各自保留另一模式的全部表数据 |
| 数据库检查 | EXE 输出 integrity_check=ok，foreign_key_check 无错误 |
| 源文件 | 转换前后 SHA-256 不变 |

本样本 RegionKey 对照（ID 与 SourceLID 均为下列值）：

| ID / SourceLID | 旧键 | 新键 |
|---|---|---|
| 4 | RAFT-A02F8CCE492D5373 | RAFT-558AA7C1D568CD8E |
| 7 | RAFT-3C9A3D7CAF1C5580 | RAFT-16CA6CD005C28311 |
| 8 | RAFT-8671C8218A22BAF1 | RAFT-A75D3682A50A1E5C |

完整摘要见 [验收结果 JSON](../validation_output/merge_release_20260915.json)。

## EXE 交付

本次按合并后的源码与现有 spec 重新构建，Python 3.12.0 / PyInstaller 6.21.0。
文件为 `dist/ydb转换.exe`，9,588,594 字节，SHA-256：

```text
66BE2E9A324473E8D6A45E18515B686C70E90DB64A14D63CD8D2468F61B0D007
```

原分支文档中的 `25B964...` 属于旧部署记录，以本次摘要为准。本次发布到仓库，
未替换本机或其他机器 Revit Addins 目录中的 EXE，也未编译或部署 Revit 插件 DLL。
