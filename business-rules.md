# 订单清洗与达人 GMV 统计口径

## 1. 月份与时间

- 归属月份使用 `Paid Time`，不是下单时间、发货时间或结算时间。
- 月份区间为左闭右开：`[当月 1 日 00:00:00, 次月 1 日 00:00:00)`。
- 没有有效 `Paid Time` 的记录不能进入月度数据。
- 用户未指定月份时，以文件中最大的有效 `Paid Time` 所在月份为准。
- 当月未结束时，允许以截至导出时点的完整订单导出更新月累计数据；它不是“增量文件”。
- 仅包含新增订单的增量文件不能直接覆盖月表，必须先与该月完整快照合并。

## 2. 必要字段与常见别名

| 标准字段 | 可识别别名 | 用途 |
| --- | --- | --- |
| Order ID | Order ID, Order Id, Order No., Order Number | 唯一订单与去重 |
| Paid Time | Paid Time, Payment Time, Paid Date | 月份归属 |
| Order Status | Order Status, Status | 状态清洗 |
| Creator Username | Creator Username, Creator Handle, Creator | 达人归属 |
| Content Type | Content Type, Channel, Sales Channel | 渠道分类 |
| Seller SKU | Seller SKU, SKU, Seller Sku | SKU 分类 |
| Quantity | Quantity, Qty, SKU Quantity | SKU 件数 |
| Order Amount | Order Amount, Total Amount, Buyer Paid Amount | 默认 GMV |

如果找不到必要字段，停止并明确列出缺失字段。不要凭列位置猜测。

## 3. 订单清洗

1. 先按全部字段删除完全相同的重复行，并记录删除数。
2. 精确重复行删除后，`Order ID` 仍重复时停止。默认口径是一行一个订单，继续统计会重复计算订单级 GMV。
3. 排除状态文本中包含 `cancel` 或 `unpaid` 的订单，不区分大小写。
4. `Order ID` 必须作为文本处理。若 Excel 将 15 位及以上订单号存成数值，停止并提示精度风险，不能静默修复。
5. `Order Amount` 与 `Quantity` 必须可转为数值；空值可按 0 处理，但非空非法文本必须列入问题并停止。

## 4. 达人归属

- 取 `Creator Username` 作为 `Creator Handle`。
- 清理首尾空格；比较和人工字段合并时不区分大小写。
- 空值、空字符串或 `62` 统一记为 `Unattributed`。
- `Unattributed` 的 GMV 计入总 GMV，但不参与达人排名和 GMV Share 分母。
- 除上述规则外，不自动合并看起来相似的账号名。

## 5. 渠道分类

只接受以下三个标准渠道：

- `Videos`
- `LIVE`
- `Product cards`

允许大小写、空格和常见单复数差异。无法映射的非空渠道必须停止并列出原始值，不能归到 Other。

## 6. SKU 分类

匹配顺序很重要：先匹配组合，再匹配单品。

1. 同时含 `303` 和 `503` → `303&503 Qty`
2. 含 `503` → `503 Qty`
3. 含 `509` → `509 Qty`
4. 含 `303` → `303 Qty`
5. 其他非空 SKU → `Other Qty`，并在 manifest 中列出 SKU 与数量

件数取 `Quantity`。SKU 为空时也归到 `Other Qty` 并审计。

## 7. GMV 和订单数

- 默认 GMV = `Order Amount`。
- 渠道 GMV 分别求和：`Videos GMV`、`LIVE GMV`、`Product cards GMV`。
- `Total GMV` = 三个渠道 GMV 之和。
- `Total Orders` = 清洗后订单行数；因重复订单号会被拦截，也等于唯一订单数。
- 渠道订单数分别为 `Video Orders`、`LIVE Orders`、`Product card Orders`。
- 达人按 `Total GMV` 降序排名；`Unattributed` 无排名，放在已归因达人之后。
- `GMV Share` = 达人 Total GMV / 所有已归因达人 Total GMV。
- `Cumulative Share` 按排名累加；`Unattributed` 的这两列留空。

## 8. 汇总表字段顺序

1. Rank
2. Creator Handle
3. 503 Qty
4. 509 Qty
5. 303 Qty
6. 303&503 Qty
7. Other Qty
8. Videos GMV
9. LIVE GMV
10. Product cards GMV
11. Total GMV
12. Total Orders
13. Video Orders
14. LIVE Orders
15. Product card Orders
16. GMV Share
17. Cumulative Share
18. 合作类型
19. 503
20. 303
21. 509
22. Ad
23. 持续时间
24. 备注

## 9. 人工维护字段

以下字段可能由业务人员在飞书中手工维护，刷新时必须保留：

- `合作类型`
- `503`
- `303`
- `509`
- `Ad`
- `持续时间`
- `备注`

合并键是规范化后的 `Creator Handle`，不是行号。优先级：已有同月月表 > 已有最新表 > 空值。同一来源存在同名达人且人工字段冲突时停止。

## 10. 敏感字段

写入飞书的 Cleaned Orders 默认移除名称、电话、邮箱、完整地址、邮编、买家留言、税号、身份证件等履约或个人信息字段。只有用户明确要求并确认目标表访问边界时，才可用 `--include-sensitive-columns` 保留。

## 11. 必须对账

- 清洗明细 `Order Amount` 合计 = 汇总表 `Total GMV` 合计。
- 三个渠道 GMV 合计 = 汇总表 `Total GMV` 合计。
- 各达人 `Total Orders` 合计 = 清洗订单数。
- 本次目标组中的 Cleaned Orders 与 Creator GMV Summary 行数、总 GMV、渠道 GMV 必须一致。
- 只有用户明确要求同时更新月度与最新时，才额外核对两组结果完全一致。
