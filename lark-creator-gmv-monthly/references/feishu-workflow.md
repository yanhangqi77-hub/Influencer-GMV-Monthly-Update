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
2. 按指令模式只定位对应的一组目标 sheet；不存在时创建：
   - 月度模式：`Cleaned Orders - <月份标签>`、`Creator GMV Summary - <月份标签>`
   - 最新模式：按稳定 `sheet_id` 定位持续滚动的两张工作表；名称可能是旧的 `- 最新`，也可能是上一轮的 `- YYMMDD~YYMMDD`。
   - 仅在用户明确要求两组都更新时定位四个 sheet。
3. 读取本次目标汇总中的 `Creator Handle` 与人工维护列。最新模式必须按滚动汇总表的 `sheet_id` 读取，不得依赖显示名称。必要时可只读其他汇总表作为人工字段回填来源，但不得因此写入非目标 sheet。
4. 将读取结果保存为 JSON，作为 `prepare_creator_gmv.py --manual-source` 输入。
5. 记录本次两个目标 sheet 的旧占用范围，后续判断是否有旧尾部数据。

## 4. 写入数据

本地脚本会根据 `--target monthly|latest|both` 生成可供批量写入的 `lark-table-payload.json` 和 `lark-styles-payload.json`。写入前检查 payload 中只包含用户要求的目标 sheet。优先使用单次批量表格更新，数据与样式分开提交，降低半完成状态风险。

按模式执行：

- 月度模式：创建缺失的月度工作表，写入月度 Cleaned Orders 与 Creator GMV Summary，然后回读核验；不更新“最新”。
- 最新模式：从 `manifest.paid_time_range` 取清洗后有效订单的最早和最晚付款时间，使用 `manifest.latest_range_label`（`YYMMDD~YYMMDD`）生成两张目标表名。先确认新名称未被其他 sheet 占用，再按稳定 `sheet_id` 将上一轮滚动表重命名，随后覆盖写入；禁止按新名称创建额外工作表，也不更新月度归档表。
- 两组模式：仅在用户明确要求时使用；先完成并核验月度组，再写入并核验最新组。

写入前再次检查 revision；如果 revision 已变化，重新读取人工字段，避免覆盖他人刚刚编辑的内容。

默认工作簿中的滚动表 ID 为 `RtysJg`（Cleaned Orders）和 `vHRbFA`（Creator GMV Summary）。用户换了工作簿时不得复用，应从旧 `- 最新` 或唯一匹配的同日期范围工作表对中重新定位。无法唯一定位或目标名称与其他 sheet 冲突时停止。

## 5. 清理旧尾部数据

正常重算可能使新数据比旧数据短。仅覆盖新范围会遗留旧行，造成重复或误读。

- 若旧占用范围不大于新范围，不需要清除。
- 若旧范围更大，先生成明确的清除范围和 dry-run 结果。
- 清除属于高风险操作，必须向用户说明 sheet 名和精确范围，并在获得明确确认后执行正式清除。
- 不删除、重命名、移动或隐藏任何非目标 sheet。

## 6. 样式约定

- Cleaned Orders：标题行冻结、筛选可用、日期格式统一、金额列使用千分位。
- Creator GMV Summary：不保留表头前空行，字段表头直接从第 1 行开始；冻结首行以及 `Rank`、`Creator Handle` 两列；GMV 使用千分位，Share 使用百分比。
- 佣金人工维护列 `合作类型` 至 `备注`：字段表头使用柔和浅紫色 `#EBD8EF` 和深色文字，数据区保持白底；不得整块使用高饱和黄色。
- 不覆盖业务人员在目标人工列中的有效值。

## 7. 写后核验

必须从飞书回读，而不是只相信写入响应。至少核验：

- 本次两个目标 sheet 的名称与存在性；明确要求两组时核验四个 sheet；
- 最新模式确认两个滚动 sheet 的 `sheet_id` 未变，名称已与 `manifest.latest_range_label` 一致，且工作簿中没有遗留或新建另一组“最新”sheet；
- 表头、首两行、末两行；
- Cleaned Orders 数据行数；
- Summary 达人数、Total Orders 和 Total GMV；
- 三个渠道 GMV 合计；
- 同一目标组内的明细与汇总对账一致；明确要求两组时再核验月度与最新数据一致；
- 随机抽查至少 3 个 Creator Handle 的人工字段是否保留。

如果使用公式，额外执行公式扫描和公式错误验证。当前本地脚本默认写入计算值，不依赖飞书公式。

若 `佣金情况汇总` 等其他 sheet 有公式引用滚动汇总表，重命名后必须回读该公式并运行公式错误验证，确认引用已自动更新；若未更新，按新的工作表名称修复后再交付。公式源范围还必须覆盖本轮汇总表的实际末行；若达人数量超过原引用范围，应同步扩展公式范围。

## 8. 失败处理

- 本地 manifest 有 `issues`：不写入。
- 任一目标 sheet 写入失败：不得报告该组更新成功，并明确报告已完成和未完成的范围。
- 两组模式下月度表写入失败：不更新“最新”。月度成功但“最新”失败时，明确报告不一致状态并只重试“最新”，不要重复写月度数据。
- 回读对账失败：不得报告成功；给出工作表、范围、预期值和实际值。
- 权限缺失：使用 `lark-shared` 请求最小必要权限，不扩大到无关域。

## 9. 涨佣备注与条件格式

每轮生成前读取稳定sheet_id下完整B:X（或全表）作为人工字段来源，核对实际读取范围覆盖所有达人且无截断/漏页。禁止直接使用局部显示截图代替当前佣金快照。脚本支持columns/data、完整annotated_csv；对不支持格式应转换或重新读取，不能忽略来源报错。

先读取 `commission-incentives.md` 及 lark-sheets 条件格式参考，再按 `lark-conditional-formats-payload.json` 将sheet_name映射至真实sheet_id。使用已注册CLI命令的实际schema，先dry-run。managed_key和sheet_name仅为本地标识，不能原样作为API字段发送。

列出既有条件格式，按自有备注标记公式定位涨佣规则；存在则按规则ID更新范围/样式，不存在才新建。只管理A与X的浅蓝涨佣规则，不删除或覆盖B列未寄样高亮、佣金紫色表头或其他人工样式。数据减少时同步收缩自有范围；不满足条件的旧自有备注由本轮脚本清理。没有候选时规则可保留，但不能显示旧高亮。

写后回读备注及条件格式定义，核对候选数量、范围、颜色和相对行公式；有可用界面时抽查可见效果。不能把条件格式仅在本地生成称为已在线高亮。核验R:W未被改佣、人工备注未丢失，且“佣金情况汇总”引用仍有效。若引用X，新的涨佣备注将随引用更新；实际佣金只有后续获得明确改佣授权才可改变。

## 10. 佣金情况汇总仅纳入有佣金的达人

用户2026-09-15确认：503、303、509、Ad四列至少一项有数据才同步。空单元格、公式空字符串和仅空格不算数据；明确填写的0/0%仍算数据。合作类型的“机构／AI”、链接、持续时间和备注均不能单独触发纳入；自动生成的涨佣/待核实备注也不能触发。判断“是否有佣金数据”与“是否可涨佣”是不同条件，不能用候选名单代替佣金汇总名单。

默认工作簿“佣金情况汇总”ID为 `BqRWCb`，A3为联动数组公式；先回读确认，不在其他工作簿照搬ID。来源为滚动GMV汇总的B达人列及R:X佣金字段，其中S:V对应四个佣金判断列。仅修改派生汇总公式，不删除源表达人、佣金或备注，保留现有表头、样式和联动。

公式使用 FILTER + HSTACK，四列条件相加表示“或”；同时排除空达人、Unattributed、Grand Total。使用 `LEN(TRIM(范围&""))>0` 判断非空，不能用 `>0` 排除真实零佣金。来源标题和范围末行按本次实际滚动表调整，保持每段范围行数一致并覆盖全部达人；禁止按旧日期写死来源。

保留空白且处理无结果的已验证结构（SHEET、N为占位符，替换后使用）：

```excel
=ARRAYFORMULA(IFERROR(FILTER(HSTACK('SHEET'!B2:BN,IF('SHEET'!R2:XN="","",'SHEET'!R2:XN)),('SHEET'!B2:BN<>"")*('SHEET'!B2:BN<>"Unattributed")*('SHEET'!B2:BN<>"Grand Total")*((LEN(TRIM('SHEET'!S2:SN&""))>0)+(LEN(TRIM('SHEET'!T2:TN&""))>0)+(LEN(TRIM('SHEET'!U2:UN&""))>0)+(LEN(TRIM('SHEET'!V2:VN&""))>0))),""))
```

本机CLI实测：IFERROR包裹FILTER时需显式ARRAYFORMULA，否则可能仅显示左上角；ARRAYFORMULA下原空格可能显示0，故R:X须用IF保留空字符串。写后不要只验证“无公式错误”，还要回读确认完整名单、7个人工字段、顺序和旧溢出尾部均符合预期。用本地读取快照独立筛选对账，并运行formula-verify至success。源表后续填入佣金自动纳入，清空四项佣金自动移出。
