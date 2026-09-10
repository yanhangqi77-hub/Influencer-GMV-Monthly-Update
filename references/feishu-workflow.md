# 飞书写入与核验流程

## 1. 目标与权限

- 默认目标为飞书电子表格 `Creator GMV Summary`：`https://my.feishu.cn/sheets/LEq1sWnjPh07MJt6yPxcniEqnrg`。
- 用户提供其他链接时，以用户链接为准。
- 先用 `lark-shared` 检查用户态认证。读取至少需要 `sheets:spreadsheet:read`；写入还需要对应表格写权限。
- 若只有标题、没有链接，使用 `lark-drive` 精确搜索标题并让用户确认歧义结果。

## 2. 必读的 lark-sheets 资料

写入前读取 `lark-sheets` 的 `SKILL.md`，并按任务需要读取：

- `references/lark-sheets-workbook.md`
- `references/lark-sheets-read-data.md`
- `references/lark-sheets-write-cells.md`
- `references/lark-sheets-styles-put.md`

命令参数以当前安装版本帮助信息为准，不臆造参数。

## 3. 写入前快照

1. 查询工作簿信息、工作表列表和当前 revision。
2. 定位以下四个目标 sheet；不存在时创建：
   - `Cleaned Orders - <月份标签>`
   - `Creator GMV Summary - <月份标签>`
   - `Cleaned Orders - 最新`
   - `Creator GMV Summary - 最新`
3. 读取已有同月汇总和“最新”汇总中的 `Creator Handle` 与人工维护列。
4. 将读取结果保存为 JSON，作为 `prepare_creator_gmv.py --manual-source` 输入。
5. 从“最新”明细的 `Paid Time` 判断其月份；如果本次处理的是更早月份，只更新月度 sheet，不覆盖“最新”，除非用户明确要求回退。
6. 记录每个目标 sheet 的旧占用范围，后续判断是否有旧尾部数据。

## 4. 写入数据

本地脚本会生成可供批量写入的 `lark-table-payload.json` 和 `lark-styles-payload.json`。优先使用单次批量表格更新，数据与样式分开提交，降低半完成状态风险。

推荐顺序：

1. 创建缺失的月度工作表。
2. 写入月度 Cleaned Orders。
3. 写入月度 Creator GMV Summary。
4. 回读并核验月度数据。
5. 创建缺失的“最新”工作表。
6. 在本次月份不早于现有“最新”月份时，用同一份数据写入“最新”两个工作表。
7. 回读并核验“最新”数据。

写入前再次检查 revision；如果 revision 已变化，重新读取人工字段，避免覆盖他人刚刚编辑的内容。

## 5. 清理旧尾部数据

正常重算可能使新数据比旧数据短。仅覆盖新范围会遗留旧行，造成重复或误读。

- 若旧占用范围不大于新范围，不需要清除。
- 若旧范围更大，先生成明确的清除范围和 dry-run 结果。
- 清除属于高风险操作，必须向用户说明 sheet 名和精确范围，并在获得明确确认后执行正式清除。
- 不删除、重命名、移动或隐藏任何非目标 sheet。

## 6. 样式约定

- Cleaned Orders：标题行冻结、筛选可用、日期格式统一、金额列使用千分位。
- Creator GMV Summary：标题和月份说明放在第 1～5 行，表头从第 7 行开始；冻结表头；GMV 使用千分位，Share 使用百分比。
- 人工维护列与计算列采用不同底色，便于识别。
- 不覆盖业务人员在目标人工列中的有效值。

## 7. 写后核验

必须从飞书回读，而不是只相信写入响应。至少核验：

- 四个 sheet 的名称与存在性；
- 表头、首两行、末两行；
- Cleaned Orders 数据行数；
- Summary 达人数、Total Orders 和 Total GMV；
- 三个渠道 GMV 合计；
- 月度与“最新”数据一致；
- 随机抽查至少 3 个 Creator Handle 的人工字段是否保留。

如果使用公式，额外执行公式扫描和公式错误验证。当前本地脚本默认写入计算值，不依赖飞书公式。

## 8. 失败处理

- 本地 manifest 有 `issues`：不写入。
- 月度表写入失败：不更新“最新”。
- 月度表成功但“最新”失败：明确报告不一致状态，并优先重试“最新”，不要重复追加月度数据。
- 回读对账失败：不得报告成功；给出工作表、范围、预期值和实际值。
- 权限缺失：使用 `lark-shared` 请求最小必要权限，不扩大到无关域。
