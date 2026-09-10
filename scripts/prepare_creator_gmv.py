#!/usr/bin/env python3
"""Prepare deterministic Creator GMV payloads without modifying Feishu."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import pandas as pd


MANUAL_COLUMNS = ["合作类型", "503", "303", "509", "Ad", "持续时间", "备注"]
SUMMARY_COLUMNS = [
    "Rank", "Creator Handle", "503 Qty", "509 Qty", "303 Qty", "303&503 Qty",
    "Other Qty", "Videos GMV", "LIVE GMV", "Product cards GMV", "Total GMV",
    "Total Orders", "Video Orders", "LIVE Orders", "Product card Orders",
    "GMV Share", "Cumulative Share", *MANUAL_COLUMNS,
]

ALIASES = {
    "order_id": ["Order ID", "Order Id", "Order No.", "Order Number"],
    "paid_time": ["Paid Time", "Payment Time", "Paid Date"],
    "status": ["Order Status", "Status"],
    "creator": ["Creator Username", "Creator Handle", "Creator"],
    "channel": ["Content Type", "Order Channel", "Channel", "Sales Channel"],
    "sku": ["Seller SKU", "SKU", "Seller Sku"],
    "quantity": ["Quantity", "Qty", "SKU Quantity"],
    "gmv": ["Order Amount", "Total Amount", "Buyer Paid Amount"],
}

SENSITIVE_PATTERNS = [
    r"recipient", r"first\s*name", r"last\s*name", r"phone", r"mobile", r"email",
    r"address", r"shipping\s*information", r"zipcode", r"zip\s*code", r"postal",
    r"buyer\s*message", r"buyer\s*username", r"buyer\s*nickname", r"tax\s*id",
    r"identity", r"id\s*card", r"passport",
]

XML_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XML_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class NumericCellText(str):
    """Text read from a numeric XLSX cell; used to detect identifier precision risk."""


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def normalized_creator(value: Any) -> tuple[str, str]:
    text = "" if value is None or pd.isna(value) else str(value).strip()
    if not text or text == "62":
        return "unattributed", "Unattributed"
    return text.casefold(), text


def parse_number(value: Any, *, field: str, row_number: int, issues: list[str]) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]+", "", text)
    if cleaned in {"", "-", ".", "-."}:
        issues.append(f"Row {row_number}: {field} has non-numeric value {text!r}")
        return 0.0
    try:
        result = float(cleaned)
        return -result if negative and result > 0 else result
    except ValueError:
        issues.append(f"Row {row_number}: {field} has non-numeric value {text!r}")
        return 0.0


def parse_paid_time(value: Any) -> pd.Timestamp | pd.NaT:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return pd.NaT
    if isinstance(value, NumericCellText):
        try:
            serial = float(str(value))
            if 1 <= serial <= 100000:
                return pd.Timestamp("1899-12-30") + pd.to_timedelta(serial, unit="D")
        except ValueError:
            pass
    try:
        return pd.to_datetime(value, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        return pd.NaT


def column_number(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref.upper())
    if not letters:
        raise ValueError(f"Invalid cell reference: {cell_ref}")
    result = 0
    for char in letters.group(0):
        result = result * 26 + ord(char) - 64
    return result


def column_letters(number: int) -> str:
    result = ""
    while number:
        number, rem = divmod(number - 1, 26)
        result = chr(65 + rem) + result
    return result


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(XML_MAIN + "t")) for item in root]


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall(PKG_REL + "Relationship")
    }
    targets: list[tuple[str, str]] = []
    for sheet in workbook.findall(".//" + XML_MAIN + "sheet"):
        name = sheet.attrib.get("name", "Sheet")
        target = rel_map.get(sheet.attrib.get(XML_REL + "id", ""), "")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        targets.append((name, target.replace("\\", "/")))
    return targets


def _cell_value(cell: ET.Element, shared: list[str]) -> Any:
    cell_type = cell.attrib.get("t", "n")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(XML_MAIN + "t"))
    value_node = cell.find(XML_MAIN + "v")
    raw = "" if value_node is None or value_node.text is None else value_node.text
    if cell_type == "s" and raw:
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell_type in {"str", "e"}:
        return raw
    if cell_type == "b":
        return raw == "1"
    return NumericCellText(raw) if raw else None


def _xlsx_rows(
    archive: zipfile.ZipFile,
    target: str,
    shared: list[str],
    allowed_rows: set[int] | None = None,
) -> Iterable[tuple[int, dict[int, Any]]]:
    current_row: int | None = None
    current: dict[int, Any] = {}
    with archive.open(target) as stream:
        for _, cell in ET.iterparse(stream, events=("end",)):
            if cell.tag != XML_MAIN + "c":
                continue
            ref = cell.attrib.get("r", "")
            row_match = re.search(r"(\d+)$", ref)
            if not row_match:
                cell.clear()
                continue
            row_num = int(row_match.group(1))
            if allowed_rows is not None and row_num not in allowed_rows:
                cell.clear()
                continue
            if current_row is not None and row_num != current_row:
                yield current_row, current
                current = {}
            current_row = row_num
            current[column_number(ref)] = _cell_value(cell, shared)
            cell.clear()
    if current_row is not None:
        yield current_row, current


def resolve_columns(columns: Iterable[Any], gmv_column: str | None = None) -> dict[str, str]:
    lookup = {norm(col): str(col).strip() for col in columns if str(col).strip()}
    aliases = dict(ALIASES)
    if gmv_column:
        aliases["gmv"] = [gmv_column, *ALIASES["gmv"]]
    resolved: dict[str, str] = {}
    for key, options in aliases.items():
        match = next((lookup[norm(option)] for option in options if norm(option) in lookup), None)
        if match:
            resolved[key] = match
    return resolved


def read_xlsx(path: Path, gmv_column: str | None) -> tuple[pd.DataFrame, str, int]:
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        for sheet_name, target in _sheet_targets(archive):
            if target not in archive.namelist():
                continue
            preview: dict[int, dict[int, Any]] = dict(
                _xlsx_rows(archive, target, shared, allowed_rows=set(range(1, 51)))
            )
            header_row = None
            headers: dict[int, str] = {}
            for row_num, row in preview.items():
                candidate = {idx: str(value).strip() for idx, value in row.items() if value is not None}
                resolved = resolve_columns(candidate.values(), gmv_column)
                if len(resolved) == len(ALIASES):
                    header_row, headers = row_num, candidate
                    break
            if header_row is None:
                continue
            records: list[dict[str, Any]] = []
            for row_num, row in _xlsx_rows(archive, target, shared):
                if row_num <= header_row:
                    continue
                record = {headers[idx]: row.get(idx) for idx in sorted(headers)}
                if any(value not in (None, "") for value in record.values()):
                    record["__source_row__"] = row_num
                    records.append(record)
            return pd.DataFrame(records), sheet_name, header_row
    raise ValueError("No worksheet contains all required order columns")


def detect_tabular_header(preview: pd.DataFrame, gmv_column: str | None) -> int | None:
    for idx, row in preview.iterrows():
        values = [value for value in row.tolist() if value is not None and not pd.isna(value)]
        if len(resolve_columns(values, gmv_column)) == len(ALIASES):
            return int(idx)
    return None


def read_source(path: Path, gmv_column: str | None) -> tuple[pd.DataFrame, str, int]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return read_xlsx(path, gmv_column)
    if suffix in {".csv", ".tsv"}:
        separator = "\t" if suffix == ".tsv" else ","
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "cp932", "gb18030"):
            try:
                preview = pd.read_csv(path, sep=separator, header=None, nrows=50, dtype=object, encoding=encoding)
                header_row = detect_tabular_header(preview, gmv_column)
                if header_row is None:
                    continue
                frame = pd.read_csv(path, sep=separator, header=header_row, dtype=object, encoding=encoding)
                frame["__source_row__"] = range(header_row + 2, header_row + 2 + len(frame))
                return frame, path.name, header_row + 1
            except (UnicodeDecodeError, pd.errors.ParserError) as exc:
                last_error = exc
        raise ValueError(f"Unable to read delimited file: {last_error}")
    if suffix == ".xls":
        book = pd.ExcelFile(path)
        for sheet in book.sheet_names:
            preview = pd.read_excel(path, sheet_name=sheet, header=None, nrows=50, dtype=object)
            header_row = detect_tabular_header(preview, gmv_column)
            if header_row is not None:
                frame = pd.read_excel(path, sheet_name=sheet, header=header_row, dtype=object)
                frame["__source_row__"] = range(header_row + 2, header_row + 2 + len(frame))
                return frame, sheet, header_row + 1
    raise ValueError("Supported input formats are .xlsx, .xls, .csv, and .tsv")


def channel_name(value: Any) -> str | None:
    key = norm(value)
    if key in {"video", "videos", "shortvideo", "shortvideos"}:
        return "Videos"
    if key in {"live", "lives", "livestream", "livestreams"}:
        return "LIVE"
    if key in {"productcard", "productcards", "product", "showcase"}:
        return "Product cards"
    return None


def sku_group(value: Any) -> str:
    text = str(value or "").upper()
    if "303" in text and "503" in text:
        return "303&503 Qty"
    if "503" in text:
        return "503 Qty"
    if "509" in text:
        return "509 Qty"
    if "303" in text:
        return "303 Qty"
    return "Other Qty"


def is_sensitive(column: str) -> bool:
    lowered = column.lower()
    return any(re.search(pattern, lowered) for pattern in SENSITIVE_PATTERNS)


def iter_table_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("columns"), list) and isinstance(value.get("data"), list):
            yield value
        for child in value.values():
            yield from iter_table_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_table_nodes(child)


def load_manual_sources(paths: list[Path], issues: list[str]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_map: dict[str, dict[str, Any]] = {}
        for table in iter_table_nodes(payload):
            columns = [str(column) for column in table["columns"]]
            creator_index = next((i for i, column in enumerate(columns) if norm(column) in {"creatorhandle", "creatorusername", "creator"}), None)
            if creator_index is None:
                continue
            manual_indexes = {column: columns.index(column) for column in MANUAL_COLUMNS if column in columns}
            for row in table["data"]:
                if creator_index >= len(row):
                    continue
                key, display = normalized_creator(row[creator_index])
                if display in {"Unattributed", "Grand Total"}:
                    continue
                values = {
                    column: row[index] if index < len(row) else None
                    for column, index in manual_indexes.items()
                }
                if key in source_map:
                    for column, value in values.items():
                        old = source_map[key].get(column)
                        if old not in (None, "") and value not in (None, "") and str(old) != str(value):
                            issues.append(f"Manual field conflict in {path.name}: {display} / {column}")
                source_map.setdefault(key, {}).update({k: v for k, v in values.items() if v not in (None, "")})
        merged.update(source_map)
    return merged


def json_ready_frame(frame: pd.DataFrame) -> list[list[Any]]:
    clean = frame.copy()
    for column in clean.columns:
        if pd.api.types.is_datetime64_any_dtype(clean[column]):
            clean[column] = clean[column].dt.strftime("%Y-%m-%d %H:%M:%S")
    return json.loads(clean.to_json(orient="values", date_format="iso"))


def make_sheet(frame: pd.DataFrame, name: str, start_cell: str = "A1") -> dict[str, Any]:
    dtypes: dict[str, str] = {}
    formats: dict[str, str] = {}
    identifier_columns = {"Order ID", "SKU ID", "Tracking ID", "Package ID"}
    amount_columns = {"GMV Amount", "Videos GMV", "LIVE GMV", "Product cards GMV", "Total GMV"}
    count_columns = {
        "Rank", "Quantity", "503 Qty", "509 Qty", "303 Qty", "303&503 Qty", "Other Qty",
        "Total Orders", "Video Orders", "LIVE Orders", "Product card Orders",
    }
    for column in frame.columns:
        series = frame[column]
        if column in identifier_columns or norm(column).endswith("id"):
            dtypes[column] = "object"
            formats[column] = "@"
        elif pd.api.types.is_datetime64_any_dtype(series):
            dtypes[column] = "datetime64[ns]"
            formats[column] = "yyyy-mm-dd hh:mm:ss"
        elif column in count_columns:
            dtypes[column] = "Int64"
            formats[column] = "#,##0"
        elif column in amount_columns:
            dtypes[column] = "float64"
            formats[column] = '#,##0 "JPY"'
        elif column in {"GMV Share", "Cumulative Share"}:
            dtypes[column] = "float64"
            formats[column] = "0.0%"
        else:
            dtypes[column] = "object"
            formats[column] = "@"
    return {
        "name": name,
        "start_cell": start_cell,
        "mode": "overwrite",
        "header": True,
        "columns": [str(column) for column in frame.columns],
        "data": json_ready_frame(frame),
        "dtypes": dtypes,
        "formats": formats,
    }


def style_for_sheet(name: str, rows: int, columns: int, summary: bool) -> dict[str, Any]:
    start_row = 7 if summary else 1
    end_col = column_letters(columns)
    header_range = f"A{start_row}:{end_col}{start_row}"
    last_row = start_row + rows
    style: dict[str, Any] = {
        "name": name,
        "cell_styles": [{
            "range": header_range,
            "font_weight": "bold",
            "font_color": "#FFFFFF",
            "background_color": "#1F4E78",
            "horizontal_alignment": "center",
            "vertical_alignment": "center",
            "wrap_text": True,
        }],
        "row_sizes": [{"range": f"{start_row}:{start_row}", "type": "pixel", "size": 34}],
        "col_sizes": [{"range": f"A:{end_col}", "type": "pixel", "size": 120}],
        "freeze": {"rows": start_row, "cols": 1 if summary else 0},
    }
    if summary and rows:
        style["cell_styles"].extend([
            {"range": f"R{start_row}:X{last_row}", "background_color": "#FFF7D6"},
            {
                "range": f"A{last_row}:{end_col}{last_row}",
                "font_weight": "bold",
                "background_color": "#D1FAE5",
            },
        ])
    return style


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(args: argparse.Namespace) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    frame, source_sheet, header_row = read_source(args.input, args.gmv_column)
    frame.columns = [str(column).strip() for column in frame.columns]
    resolved = resolve_columns(frame.columns, args.gmv_column)
    missing = sorted(set(ALIASES) - set(resolved))
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))

    source_rows = len(frame)
    paid = frame[resolved["paid_time"]].map(parse_paid_time)
    invalid_paid_time = int(paid.isna().sum())
    valid_paid = paid.dropna()
    if valid_paid.empty:
        raise ValueError("No valid Paid Time values found")
    if args.month == "latest":
        selected_period = valid_paid.max().to_period("M")
    else:
        if not re.fullmatch(r"\d{4}-\d{2}", args.month):
            raise ValueError("--month must be latest or YYYY-MM")
        selected_period = pd.Period(args.month, freq="M")
    month_mask = paid.map(lambda value: False if pd.isna(value) else value.to_period("M") == selected_period)
    month_frame = frame.loc[month_mask].copy()
    month_frame[resolved["paid_time"]] = paid.loc[month_mask]
    rows_in_month = len(month_frame)
    if rows_in_month == 0:
        raise ValueError(f"No orders found for {selected_period}")

    before_dedup = len(month_frame)
    month_frame = month_frame.drop_duplicates(subset=[column for column in month_frame.columns if column != "__source_row__"])
    exact_duplicates = before_dedup - len(month_frame)

    status_text = month_frame[resolved["status"]].fillna("").astype(str)
    excluded_status_mask = status_text.str.contains(r"cancel|unpaid", case=False, regex=True)
    excluded_status = int(excluded_status_mask.sum())
    cleaned = month_frame.loc[~excluded_status_mask].copy()

    order_ids: list[str] = []
    for idx, value in cleaned[resolved["order_id"]].items():
        row_number = int(cleaned.at[idx, "__source_row__"])
        text = "" if value is None or pd.isna(value) else str(value).strip()
        if not text:
            issues.append(f"Row {row_number}: empty Order ID")
        if isinstance(value, NumericCellText) and re.fullmatch(r"\d{15,}", text):
            issues.append(f"Row {row_number}: Order ID {text!r} is stored as numeric and may have lost precision")
        order_ids.append(text)
    cleaned[resolved["order_id"]] = order_ids
    order_id_counts = Counter(value for value in order_ids if value)
    duplicate_ids = sorted(value for value, count in order_id_counts.items() if count > 1)
    if duplicate_ids:
        issues.append("Duplicate Order IDs after exact-row deduplication: " + ", ".join(duplicate_ids[:20]))

    creator_keys: list[str] = []
    creator_displays: list[str] = []
    channels: list[str | None] = []
    sku_groups: list[str] = []
    quantities: list[float] = []
    gmvs: list[float] = []
    unknown_channels: Counter[str] = Counter()
    unknown_skus: Counter[str] = Counter()
    for idx, row in cleaned.iterrows():
        source_row = int(row["__source_row__"])
        creator_key, creator_display = normalized_creator(row[resolved["creator"]])
        channel = channel_name(row[resolved["channel"]])
        raw_channel = "" if pd.isna(row[resolved["channel"]]) else str(row[resolved["channel"]]).strip()
        if channel is None:
            unknown_channels[raw_channel or "<blank>"] += 1
        group = sku_group(row[resolved["sku"]])
        quantity = parse_number(row[resolved["quantity"]], field=resolved["quantity"], row_number=source_row, issues=issues)
        gmv = parse_number(row[resolved["gmv"]], field=resolved["gmv"], row_number=source_row, issues=issues)
        if group == "Other Qty":
            raw_sku = "" if pd.isna(row[resolved["sku"]]) else str(row[resolved["sku"]]).strip()
            unknown_skus[raw_sku or "<blank>"] += quantity
        creator_keys.append(creator_key)
        creator_displays.append(creator_display)
        channels.append(channel)
        sku_groups.append(group)
        quantities.append(quantity)
        gmvs.append(gmv)
    if unknown_channels:
        issues.append("Unknown channels: " + ", ".join(f"{key} ({count})" for key, count in unknown_channels.items()))

    cleaned["__creator_key__"] = creator_keys
    cleaned["Creator Summary Key"] = creator_displays
    cleaned["GMV Channel"] = channels
    cleaned["SKU Group"] = sku_groups
    cleaned[resolved["quantity"]] = quantities
    cleaned[resolved["gmv"]] = gmvs
    cleaned["GMV Amount"] = gmvs

    manual = load_manual_sources(args.manual_source, issues)
    if issues:
        return {
            "issues": issues,
            "warnings": warnings,
            "source_rows": source_rows,
            "rows_in_month": rows_in_month,
            "selected_month": str(selected_period),
        }

    metric_rows: list[dict[str, Any]] = []
    for creator_key, group in cleaned.groupby("__creator_key__", sort=False):
        display = str(group["Creator Summary Key"].iloc[0])
        row: dict[str, Any] = {"Creator Handle": display}
        for sku_column in ["503 Qty", "509 Qty", "303 Qty", "303&503 Qty", "Other Qty"]:
            row[sku_column] = int(round(group.loc[group["SKU Group"] == sku_column, resolved["quantity"]].sum()))
        for channel in ["Videos", "LIVE", "Product cards"]:
            row[f"{channel} GMV"] = float(group.loc[group["GMV Channel"] == channel, "GMV Amount"].sum())
        row["Total GMV"] = float(group["GMV Amount"].sum())
        row["Total Orders"] = int(len(group))
        row["Video Orders"] = int((group["GMV Channel"] == "Videos").sum())
        row["LIVE Orders"] = int((group["GMV Channel"] == "LIVE").sum())
        row["Product card Orders"] = int((group["GMV Channel"] == "Product cards").sum())
        row.update({column: manual.get(creator_key, {}).get(column) for column in MANUAL_COLUMNS})
        row["__creator_key__"] = creator_key
        metric_rows.append(row)

    attributed = sorted(
        [row for row in metric_rows if row["__creator_key__"] != "unattributed"],
        key=lambda row: (-row["Total GMV"], row["Creator Handle"].casefold()),
    )
    unattributed = [row for row in metric_rows if row["__creator_key__"] == "unattributed"]
    attributed_total = sum(row["Total GMV"] for row in attributed)
    cumulative = 0.0
    for rank, row in enumerate(attributed, start=1):
        share = row["Total GMV"] / attributed_total if attributed_total else 0.0
        cumulative += share
        row["Rank"] = rank
        row["GMV Share"] = share
        row["Cumulative Share"] = cumulative
    for row in unattributed:
        row["Rank"] = None
        row["GMV Share"] = None
        row["Cumulative Share"] = None

    ordered = attributed + unattributed
    for row in ordered:
        row.pop("__creator_key__", None)
    total_row: dict[str, Any] = {column: None for column in SUMMARY_COLUMNS}
    total_row["Creator Handle"] = "Grand Total"
    for column in [
        "503 Qty", "509 Qty", "303 Qty", "303&503 Qty", "Other Qty", "Videos GMV", "LIVE GMV",
        "Product cards GMV", "Total GMV", "Total Orders", "Video Orders", "LIVE Orders", "Product card Orders",
    ]:
        total_row[column] = sum(row[column] for row in ordered)
    total_row["GMV Share"] = 1.0
    total_row["Cumulative Share"] = 1.0
    summary = pd.DataFrame([*ordered, total_row], columns=SUMMARY_COLUMNS)

    removed_sensitive = [column for column in cleaned.columns if column != "__source_row__" and is_sensitive(column)]
    output_columns = [
        column for column in cleaned.columns
        if column not in {"__source_row__", "__creator_key__"}
        and (args.include_sensitive_columns or column not in removed_sensitive)
    ]
    cleaned_output = cleaned[output_columns].copy()
    # Feishu's typed table protocol currently accepts date-only values for its
    # date dtype. Preserve order timestamps losslessly as sortable ISO text.
    for column in cleaned_output.columns:
        if pd.api.types.is_datetime64_any_dtype(cleaned_output[column]):
            cleaned_output[column] = cleaned_output[column].dt.strftime("%Y-%m-%d %H:%M:%S")

    label = args.month_label or str(selected_period)
    names = {
        "monthly_cleaned": f"Cleaned Orders - {label}",
        "monthly_summary": f"Creator GMV Summary - {label}",
        "latest_cleaned": "Cleaned Orders - 最新",
        "latest_summary": "Creator GMV Summary - 最新",
    }
    if args.target == "monthly":
        target_keys = ["monthly_cleaned", "monthly_summary"]
    elif args.target == "latest":
        target_keys = ["latest_cleaned", "latest_summary"]
    else:
        target_keys = ["monthly_cleaned", "monthly_summary", "latest_cleaned", "latest_summary"]

    tables_by_key = {
        "monthly_cleaned": make_sheet(cleaned_output, names["monthly_cleaned"]),
        "monthly_summary": make_sheet(summary, names["monthly_summary"], "A7"),
        "latest_cleaned": make_sheet(cleaned_output, names["latest_cleaned"]),
        "latest_summary": make_sheet(summary, names["latest_summary"], "A7"),
    }
    styles_by_key = {
        "monthly_cleaned": style_for_sheet(names["monthly_cleaned"], len(cleaned_output), len(cleaned_output.columns), False),
        "monthly_summary": style_for_sheet(names["monthly_summary"], len(summary), len(summary.columns), True),
        "latest_cleaned": style_for_sheet(names["latest_cleaned"], len(cleaned_output), len(cleaned_output.columns), False),
        "latest_summary": style_for_sheet(names["latest_summary"], len(summary), len(summary.columns), True),
    }
    table_payload = {"sheets": [tables_by_key[key] for key in target_keys]}
    styles_payload = {"styles": [styles_by_key[key] for key in target_keys]}

    total_gmv = float(cleaned_output["GMV Amount"].sum())
    summary_gmv = float(summary.loc[summary["Creator Handle"] != "Grand Total", "Total GMV"].sum())
    channel_totals = {
        channel: float(cleaned.loc[cleaned["GMV Channel"] == channel, "GMV Amount"].sum())
        for channel in ["Videos", "LIVE", "Product cards"]
    }
    if not math.isclose(total_gmv, summary_gmv, abs_tol=0.01):
        issues.append(f"GMV reconciliation failed: cleaned={total_gmv}, summary={summary_gmv}")
    if not math.isclose(total_gmv, sum(channel_totals.values()), abs_tol=0.01):
        issues.append(f"Channel GMV reconciliation failed: total={total_gmv}, channels={sum(channel_totals.values())}")

    all_expected_ranges = {
        names["monthly_cleaned"]: f"A1:{column_letters(len(cleaned_output.columns))}{len(cleaned_output) + 1}",
        names["monthly_summary"]: f"A7:{column_letters(len(summary.columns))}{len(summary) + 7}",
        names["latest_cleaned"]: f"A1:{column_letters(len(cleaned_output.columns))}{len(cleaned_output) + 1}",
        names["latest_summary"]: f"A7:{column_letters(len(summary.columns))}{len(summary) + 7}",
    }
    manifest = {
        "status": "ready" if not issues else "blocked",
        "issues": issues,
        "warnings": warnings,
        "source": {
            "path": str(args.input.resolve()),
            "sha256": hash_file(args.input),
            "sheet": source_sheet,
            "header_row": header_row,
        },
        "selected_month": str(selected_period),
        "month_label": label,
        "target_mode": args.target,
        "row_counts": {
            "source_rows": source_rows,
            "invalid_paid_time": invalid_paid_time,
            "rows_in_month": rows_in_month,
            "exact_duplicates_removed": exact_duplicates,
            "status_excluded": excluded_status,
            "cleaned_orders": len(cleaned_output),
            "attributed_creators": len(attributed),
            "unattributed_rows": int((cleaned["__creator_key__"] == "unattributed").sum()),
        },
        "totals": {
            "total_gmv": total_gmv,
            "attributed_gmv": attributed_total,
            "unattributed_gmv": float(cleaned.loc[cleaned["__creator_key__"] == "unattributed", "GMV Amount"].sum()),
            "channel_gmv": channel_totals,
            "total_orders": len(cleaned_output),
        },
        "unknown_skus": dict(sorted(unknown_skus.items())),
        "removed_sensitive_columns": [] if args.include_sensitive_columns else removed_sensitive,
        "manual_creator_records_loaded": len(manual),
        "target_sheets": {key: names[key] for key in target_keys},
        "expected_ranges": {names[key]: all_expected_ranges[names[key]] for key in target_keys},
    }
    return {"manifest": manifest, "tables": table_payload, "styles": styles_payload}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Source .xlsx/.xls/.csv/.tsv order export")
    parser.add_argument("--month", default="latest", help="Paid Time month: latest or YYYY-MM")
    parser.add_argument(
        "--target",
        required=True,
        choices=["monthly", "latest", "both"],
        help="Generate only the requested monthly pair, latest pair, or both pairs",
    )
    parser.add_argument("--month-label", help="Optional sheet-name label in place of YYYY-MM")
    parser.add_argument("--gmv-column", help="Override the default Order Amount column")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--manual-source", action="append", default=[], type=Path)
    parser.add_argument("--include-sensitive-columns", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input.is_file():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 2
    try:
        result = build(args)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        if "manifest" not in result:
            manifest = {"status": "blocked", **result}
            (args.output_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 2
        (args.output_dir / "lark-table-payload.json").write_text(
            json.dumps(result["tables"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output_dir / "lark-styles-payload.json").write_text(
            json.dumps(result["styles"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output_dir / "manifest.json").write_text(
            json.dumps(result["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
        return 0 if not result["manifest"]["issues"] else 2
    except Exception as exc:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"status": "blocked", "issues": [str(exc)]}
        (args.output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
