---
name: lark-creator-gmv-monthly
description: "清洗 TikTok Shop 原始订单表，更新飞书指定月份或滚动最新达人 GMV；按已付款扣除取消、退款/退货的净件数筛选涨佣候选、生成高亮和涨佣备注。当用户要求更新某月达人 GMV、更新最新 GMV 或维护该工作流时使用。"
metadata:
  requires:
    bins: ["lark-cli"]
---

# 达人 GMV 更新

将原始订单清洗、汇总为达人 GMV 数据，并同步到飞书电子表格 **Creator GMV Summary**。

## 指令路由与固定输出

严格按用户用语选择写入范围，不得顺带更新另一组：

- 用户说“更新达人 X 月 GMV”时，只更新：
  - `Cleaned Orders - YYYY-MM`
  - `Creator GMV Summary - YYYY-MM`
- 用户说“更新达人最新 GMV”或“更新最新GMV”时，只更新一组持续滚动的工作表。工作表名称使用本次清洗后有效订单的 `Paid Time` 最早日和最晚日，格式为：
  - `Cleaned Orders - YYMMDD~YYMMDD`
  - `Creator GMV Summary - YYMMDD~YYMMDD`
  - 例如数据覆盖 2026-09-01 至 2026-09-10，则名称为 `Cleaned Orders - 260901~260910`、`Creator GMV Summary - 260901~260910`。
- “最新”是指令模式，不再是工作表名称后缀。每次更新必须按稳定 `sheet_id` 找到上一轮的滚动工作表，先根据本次日期范围改名，再覆盖原工作表；禁止为每个日期范围新建一组 sheet。
- 只有用户明确要求同时更新月度与最新 GMV 时，才更新四个工作表。
- 用户只说“更新达人 GMV”且无法判断模式时，先询问要更新指定月份还是最新数据。

月度归档表默认使用 `YYYY-MM`，避免跨年份重名。若用户明确指定其他月份标签，可用该标签替代。最新滚动表固定使用 `YYMMDD~YYMMDD`，不使用 `--month-label` 覆盖。

默认目标表：`https://my.feishu.cn/sheets/LEq1sWnjPh07MJt6yPxcniEqnrg`。若用户提供新的表格链接，以用户提供的链接为准。

默认目标表中最新滚动工作表的稳定标识：

- Cleaned Orders：`RtysJg`
- Creator GMV Summary：`vHRbFA`

其他工作簿不得复用这两个 ID；应从该工作簿中定位原 `- 最新` 工作表，或唯一一对同日期标签、名称符合 `Cleaned Orders - \d{6}~\d{6}` 与 `Creator GMV Summary - \d{6}~\d{6}` 的工作表并记录其 `sheet_id`。无法唯一定位时停止，不得新建或猜测。

## 使用前必读

1. 读取 `references/business-rules.md`，严格执行数据口径。
2. 读取 `references/feishu-workflow.md`，遵循飞书写入、人工字段保护和核验步骤。
3. 使用 `spreadsheets` skill 读取 Excel/CSV；使用 `lark-sheets` skill 更新飞书表格。
4. 只有按标题查找目标表时才使用 `lark-drive`；认证或权限问题使用 `lark-shared`。
5. 读取 `references/commission-incentives.md`，按内部2026年8月机制及用户确认的净件数口径筛选涨佣候选。

## 默认边界

- 输入默认是截至导出时点的完整订单快照；当月尚未结束也可作为月累计快照更新。同月重复执行采用重算覆盖，不追加。
- 如果用户说明文件只包含新增订单，或文件显然是增量而不是完整导出，先确认合并来源，不能直接覆盖月表。
- 月份由 `Paid Time` 判定；用户未指定月份时，选择文件中最新的有效付款月份。
- GMV 默认取 `Order Amount`，即订单实付金额。
- 涨佣独立按当月已付款扣除取消、退款/退货后的净件数；不改变原有GMV口径。佣金或售后信息不明确时标待核实，不猜测、不自动改佣。
- `Creator Handle` 为空或值为 `62` 时归为 `Unattributed`。
- 未识别渠道会中止；未识别 SKU 归到 `Other Qty` 并写入审计信息。
- 飞书清洗表默认排除姓名、电话、地址、邮编、买家留言、税号等敏感履约字段。
- 人工维护列必须按规范化后的 `Creator Handle` 继承，禁止按行号继承。
- “佣金情况汇总”仅联动503、303、509、Ad至少一项非空的达人；只有合作类型、日期或备注（包括自动涨佣备注）的不纳入。此筛选不删除GMV源表达人。维护规则见 `references/feishu-workflow.md` 第10节。

## 执行流程

### 1. 检查输入和目标

- 确认输入文件存在且可读。
- 确认目标飞书表可访问，并只读取本次目标工作表的当前状态；可只读其他汇总表作为人工字段回填来源。
- 最新模式按滚动汇总表的稳定 `sheet_id` 读取人工维护列，保存为后续合并源；不得依赖上一轮显示名称。
- 必须读全当前达人和人工字段，不能使用截断、漏页或局部范围快照；脚本接受完整columns/data或annotated_csv结果，不支持的格式会阻止写入。按需读取已核实的合作身份及资格记录。

### 2. 生成待写入数据

通过工作区依赖提供的 Python 运行：

```powershell
& <bundled-python> scripts/prepare_creator_gmv.py `
  --input <订单文件> `
  --month <YYYY-MM|latest> `
  --target <monthly|latest> `
  --output-dir <临时输出目录> `
  --manual-source <飞书汇总表读取结果.json>
```

可选参数：

- `--month-label <标签>`：覆盖工作表名称中的月份标签。
- `--target monthly`：只生成两个指定月份工作表的写入包。
- `--target latest`：只生成两个最新滚动工作表的写入包，名称自动采用清洗后有效订单的 `Paid Time` 日期范围。
- `--target both`：仅在用户明确要求两组都更新时使用。
- `--gmv-column <列名>`：用户明确要求改变 GMV 口径时使用。
- `--include-sensitive-columns`：只有用户明确要求写入履约敏感字段时使用。
- 可重复传入 `--manual-source`，后传入的同名达人字段优先。
- `--creator-context <json>`：已核实的身份、资格及当前统一佣金；未指定时自动读取本机 `references/creator-context.local.json`（如存在）。格式见佣金参考，不能用未经核实资料覆盖当前真实佣金。

脚本只读取源文件并生成本地 JSON，不直接修改飞书。生成：

- `lark-table-payload.json`
- `lark-styles-payload.json`
- `manifest.json`
- `commission-incentive-audit.json`：净件数、档位、候选及待核实原因。
- `lark-conditional-formats-payload.json`：涨佣候选的自有浅蓝条件格式，仅A与X列。

### 3. 审核清洗结果

读取 `manifest.json`，至少确认：

- 选择的月份与用户要求一致；
- 原始行数、月份内行数、状态排除数、精确重复行数、清洗后订单数合理；
- 汇总 GMV 等于清洗明细 GMV；
- 各渠道 GMV 之和等于总 GMV；
- 没有重复订单号、未知渠道或订单号精度风险；
- 未识别 SKU 已列出并确认可接受。
- 检查涨佣审计数量和备注一致；真实佣金未变，人工备注保留；未知身份、复杂佣金或不明退款没有被误报为已获批涨佣。

出现 `issues` 时停止，不得写入飞书。

### 4. 更新飞书

严格按 `references/feishu-workflow.md` 执行：

1. 月度模式只写并核验 `Cleaned Orders - YYYY-MM` 与 `Creator GMV Summary - YYYY-MM`。
2. 最新模式根据 `manifest.latest_range_label` 生成新名称；先按稳定 `sheet_id` 重命名上一轮的两个滚动工作表，再覆盖并核验这两个工作表。不得创建新的日期范围工作表。
3. 用户明确要求两组都更新时，先写月度并回读核验，再写最新并回读核验。
4. 按佣金参考更新自有条件格式，保留达人ID未寄样高亮及其他规则。涨佣建议仅写入备注，不修改已约定佣金。

若旧表占用范围大于新数据范围，需要清除旧尾部数据；这属于高风险清除操作，必须先 dry-run 并取得用户明确确认后再执行。

## 完成标准

只有同时满足以下条件才能报告成功：

- 本次指令对应的两个目标工作表均存在且更新完成；若明确要求两组，则核验四个工作表；
- 同一目标组内的明细行数、汇总 GMV、渠道 GMV 对账一致；
- 汇总 GMV 与清洗订单 GMV 对账一致；
- 人工维护列按达人正确保留；
- 本轮涨佣备注已重算、不重复或残留；自有浅蓝规则范围正确，候选/待核实数量与审计一致，未改真实佣金；
- 已从飞书回读关键区域并完成核验；
- 向用户说明月份、订单数、达人 GMV、未归因 GMV及任何异常 SKU。
- 简述涨佣候选数、待核实数及需资格/预算审核，不把候选或本地预览称为已改佣或已写入。
