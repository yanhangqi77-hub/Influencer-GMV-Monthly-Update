"""Paid net-unit incentive screening. Never changes agreed commission fields."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import pandas as pd

START = "[GMV佣金激励]"
END = "[/GMV佣金激励]"
POLICY_PATH = Path(__file__).resolve().parents[1] / "references" / "commission-policy.json"


def text(value):
    return "" if value is None or pd.isna(value) else str(value).strip()


def key(value):
    return text(value).casefold()


def number(value, field):
    raw = text(value)
    if not raw:
        return 0.0
    # Do not turn invalid mixed strings such as 'abc12' into valid numbers.
    raw = re.sub(r"(?:JPY|円|¥|￥|,|\s)", "", raw, flags=re.I)
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        raise ValueError(f"{field}不是非负数")
    result = float(raw)
    if not math.isfinite(result):
        raise ValueError(f"{field}不是有限数")
    return result


def units(value, field):
    result = number(value, field)
    if not result.is_integer():
        raise ValueError(f"{field}不是整数件数")
    return int(result)


def rate(value):
    raw = text(value)
    if not raw:
        return None
    percentage = raw.endswith(("%", "％"))
    raw = raw.rstrip("%％").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        raise ValueError("佣金比例格式不明确")
    result = float(raw)
    if not percentage and 0 < result < 1:
        result *= 100
    if not 0 <= result <= 100:
        raise ValueError("佣金比例越界")
    return round(result, 6)


def column(columns, aliases):
    lookup = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in columns}
    return next((lookup[a] for a in aliases if a in lookup), None)


PRODUCTS = ["503", "303", "509"]


def sku_products(sku_group):
    """Map a SKU group to the commission products it counts toward.

    A combined 303&503 SKU counts toward both products; Other/unknown SKUs
    do not count toward any per-product tier.
    """
    group = str(sku_group or "").strip()
    if group == "303&503 Qty":
        return ["303", "503"]
    if group == "503 Qty":
        return ["503"]
    if group == "509 Qty":
        return ["509"]
    if group == "303 Qty":
        return ["303"]
    return []


def net_units(cleaned, resolved):
    """Input is already paid-month filtered, cancellation filtered and deduped.

    Net units are tracked per product (503/303/509); a combined 303&503 SKU
    counts toward both products.
    """
    return_col = column(cleaned.columns, ["skuquantityofreturn", "returnquantity", "returnedquantity", "refundedquantity"])
    refund_col = column(cleaned.columns, ["orderrefundamount", "refundamount", "refundedamount"])
    type_col = column(cleaned.columns, ["cancelationreturntype", "cancellationreturntype", "returnrefundstatus"])
    amount_col = column(cleaned.columns, ["orderamount", "totalamount", "buyerpaidamount"])
    by_creator = {}
    for _, row in cleaned.iterrows():
        creator = row["__creator_key__"]
        record = by_creator.setdefault(creator, {"known_net_units": 0, "products": {p: 0 for p in PRODUCTS},
                                                "deducted_units": 0, "rows": 0, "uncertain_rows": []})
        record["rows"] += 1
        source_row = int(row["__source_row__"])
        try:
            quantity = units(row.get("__incentive_quantity_raw__", row[resolved["quantity"]]), "Quantity")
            if not return_col or not refund_col:
                raise ValueError("缺少退货件数或退款金额列，不能确认净件数")
            returned = units(row[return_col], "退货/退款件数")
            refund = number(row[refund_col], "退款金额")
            if returned > quantity:
                raise ValueError("退货件数大于订单件数")
            amount = number(row[amount_col], "订单实付金额") if amount_col else None
            status = key(row[resolved["status"]])
            aftersale = key(row[type_col]) if type_col else ""
            full_refund = refund > 0 and amount is not None and amount > 0 and refund >= amount
            if full_refund or status in {"refunded", "fully refunded", "returned", "已退款", "已退货"}:
                remaining = 0
            elif returned:
                # Monetary refund and returned quantity commonly describe the same units.
                remaining = quantity - returned
            elif refund:
                if quantity == 1:
                    remaining = 0
                else:
                    raise ValueError("多件订单仅有部分退款金额，缺少可核实退款件数")
            elif aftersale and aftersale not in {"none", "n/a", "no", "0", "无", "-"}:
                raise ValueError("存在售后标记但无明确扣减数量，需核实售后结果")
            else:
                remaining = quantity
            record["known_net_units"] += remaining
            record["deducted_units"] += quantity - remaining
            for product in sku_products(row.get("SKU Group")):
                record["products"][product] += remaining
        except ValueError as exc:
            record["uncertain_rows"].append({"source_row": source_row, "reason": str(exc)})
    return by_creator, {"return_quantity": return_col, "refund_amount": refund_col, "return_type": type_col}


def load_context(path):
    if path is None:
        return {}
    content = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(content.get("creators"), dict):
        raise ValueError("creator-context 必须包含 creators 对象")
    result = {}
    for handle, profile in content["creators"].items():
        if not isinstance(profile, dict) or key(handle) in result:
            raise ValueError("creator-context 存在重复达人或非法记录")
        if "eligible" in profile and not isinstance(profile["eligible"], bool):
            raise ValueError("eligible 必须是布尔值")
        result[key(handle)] = profile
    return result


def clean_generated_note(value):
    original = "" if value is None or pd.isna(value) else str(value)
    return re.sub(r"\[GMV佣金激励\].*?\[/GMV佣金激励\]", "", original, flags=re.S).strip()


def append_note(original, generated):
    preserved = clean_generated_note(original)
    return "\n".join(part for part in [preserved, f"{START} {generated} {END}" if generated else ""] if part)


def evaluate(row, net, month, policy, context):
    handle = row["Creator Handle"]
    profile = context.get(key(handle), {})
    products = net.get("products", {}) if isinstance(net, dict) else {}
    known_total = net.get("known_net_units", sum(products.values())) if isinstance(net, dict) else 0
    uncertain = net.get("uncertain_rows", []) if isinstance(net, dict) else []
    result = {"creator_handle": handle,
              "net_units": None if uncertain else {p: products.get(p, 0) for p in PRODUCTS},
              "known_net_units": known_total, "uncertain_rows": uncertain,
              "status": "no_raise", "can_raise": False, "message": ""}
    role = text(profile.get("role") or row.get("合作类型"))
    if profile.get("eligible") is False or any(word in role for word in ["不参与", "取消资格", "禁用"]):
        return {**result, "status": "excluded", "message": "不参与统一激励，保留现有佣金"}
    if any(word in role for word in ["核心", "特殊", "单独协商", "历史高佣"]) or key(role) in {"core", "special", "legacy"}:
        return {**result, "status": "protected_review", "message": "历史高佣/特殊协议另行审核，保留现有佣金"}
    if uncertain:
        return {**result, "status": "needs_review", "message": "净件数待核实，不生成涨佣建议"}
    premium = role in {"优质达人定向邀请", "优质定邀", "15%优质定邀", "优质定邀达人"} or key(role) == "premium"
    tier_list = [policy["premium_tier"]] if premium else policy["standard_tiers"]

    # Per-product tiers are decided by each product's own net units, before any
    # current-rate parsing, so creators below every tier are simply "no_raise".
    tiers = {p: next((t for t in reversed(tier_list) if products.get(p, 0) >= t["minimum_units"]), None)
             for p in PRODUCTS}
    tiers = {p: t for p, t in tiers.items() if t is not None}
    if not tiers:
        return result

    try:
        if "organic_percent" in profile:
            organic = {"自然": rate(profile["organic_percent"])}
        else:
            organic = {product: rate(row.get(product)) for product in PRODUCTS if text(row.get(product))}
        organic = {p: value for p, value in organic.items() if value is not None}
        ad_raw = profile.get("ad_percent", row.get("Ad"))
        ad = rate(ad_raw)
    except ValueError as exc:
        return {**result, "status": "needs_review", "message": f"{exc}，待核实"}
    if any(v > 18 for v in organic.values()):
        return {**result, "status": "protected_review", "message": "现有历史高佣高于通用最高档，保留原条件并单独审核"}
    standard_role = role in {"普通／开放合作", "普通/开放合作", "普通", "开放合作", "TYMO店铺定向邀请", "中部达人定向邀请"} or key(role) == "standard"
    if not premium and not standard_role and any(v == 15 for v in organic.values()):
        return {**result, "status": "needs_review", "message": "当前自然佣金15%，需确认是否属于优质定邀再判断升级规则"}
    if ("自然" not in organic and any(p not in organic for p in tiers)) or ad is None:
        return {**result, "status": "needs_review", "message": "当前佣金不全，待核实"}

    # Compare each qualifying product against its own tier target.
    changes, raised = [], []
    if "自然" in organic:
        max_tier = max(tiers.values(), key=lambda t: t["organic_percent"])
        if max_tier["organic_percent"] > organic["自然"]:
            changes.append(f"自然{max_tier['organic_percent']}%")
            raised = list(tiers.keys())
    else:
        for p in tiers:
            if tiers[p]["organic_percent"] > organic[p]:
                changes.append(f"{p}自然{tiers[p]['organic_percent']}%")
                raised.append(p)

    # Ad is a single uniform rate; its target follows the highest tier among
    # the qualifying products (each product's own units select its tier).
    if tiers and ad is not None:
        ad_target = max(t["ad_percent"] for t in tiers.values())
        if ad_target > ad:
            changes.append(f"广告{ad_target}%")

    if not changes:
        return result
    unit_products = raised or list(tiers.keys())
    unit_desc = "、".join(f"{product}件{products.get(product, 0)}" for product in unit_products)
    # Only increases are proposed. Higher existing dimensions are never lowered.
    result.update(status="raise_candidate", can_raise=True,
                  current_organic=organic, current_ad=ad, tiers=tiers,
                  message=f"涨佣金为{'／'.join(changes)}（{month}净{unit_desc}；未列项目保持原佣金；资格及预算待审核，审核后生效、不追溯）")
    return result


def apply_incentives(cleaned, resolved, metric_rows, month, context_path=None):
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if context_path is None:
        default_context = POLICY_PATH.parent / "creator-context.local.json"
        context_path = default_context if default_context.is_file() else None
    context = load_context(context_path)
    totals, fields = net_units(cleaned, resolved)
    audit = []
    for row in metric_rows:
        creator = row["__creator_key__"]
        if creator == "unattributed":
            row["备注"] = clean_generated_note(row.get("备注")) or None
            continue
        if month < "2026-08":
            evaluation = {"creator_handle": row["Creator Handle"], "status": "policy_not_applicable",
                          "can_raise": False, "message": ""}
        else:
            evaluation = evaluate(row, totals[creator], month, policy, context)
        row["备注"] = append_note(row.get("备注"), evaluation["message"]) or None
        audit.append(evaluation)
    counts = dict(Counter(item["status"] for item in audit))
    return {"policy_version": policy["version"], "quantity_basis": policy["quantity_basis"],
            "month": month, "fields": fields, "counts": counts,
            "budget_target_below_percent": policy["budget_targets"].get(month),
            "budget_assessment": "not_calculated_requires_company_cost_basis_and_review",
            "creators": audit, "applies_actual_commission_changes": False}


def conditional_formats(summary_names, last_row):
    style = json.loads(POLICY_PATH.read_text(encoding="utf-8"))["highlight"]
    # The unique managed note marker keeps this rule distinct from sample highlighting.
    formula = '=AND($B2<>"",$B2<>"Unattributed",$B2<>"Grand Total",ISNUMBER(SEARCH("[GMV佣金激励] 涨佣金为",$X2)))'
    return {"managed_key": "gmv-commission-incentive-v1", "rules": [
        {"sheet_name": name, "rule_type": "expression", "ranges": [f"A2:A{max(2, last_row)}", f"X2:X{max(2, last_row)}"],
         "properties": {"style": style, "attrs": [{"formula": [formula]}], "has_ref": True}}
        for name in summary_names]}
