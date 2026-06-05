#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import os
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from xml.sax.saxutils import escape

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent / "w1_results_trungnt"
SCENARIOS = ["default", "low", "medium", "high"]
PROTOCOLS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1f77b4", "ssh3": "#d62728", "mosh": "#2ca02c"}
HATCH_FILLS = {"ssh": "#d4f4ff", "ssh3": "#ffd6d8", "mosh": "#d7f5d1"}
HATCHES = {"ssh": "////", "ssh3": "////", "mosh": "\\\\\\\\"}

SUMMARY_COLUMNS = [
    "Scenario",
    "Protocol",
    "Successful Samples (N)",
    "Failed Samples",
    "Success %",
    "Latency Min (ms)",
    "Latency Mean (ms)",
    "Latency Median (ms)",
    "Latency P95 (ms)",
    "Latency P99 (ms)",
    "Latency Max (ms)",
    "Latency CI95 +/- (ms)",
    "Mean Output (B)",
    "Mean Throughput (KiB/s)",
    "Mean Residual Bytes",
    "Recv Avg %",
    "Recv Min %",
    "ContentOK %",
    "BadHash",
    "Notes",
]

WORKLOAD_COLUMNS = [
    "Scenario",
    "Workload",
    "Command",
    "Protocol",
    "Successful Samples (N)",
    "Failed Samples",
    "Success %",
    "Latency Min (ms)",
    "Latency Mean (ms)",
    "Latency Median (ms)",
    "Latency P95 (ms)",
    "Latency P99 (ms)",
    "Latency Max (ms)",
    "Latency CI95 +/- (ms)",
    "Mean Output (B)",
    "Mean Throughput (KiB/s)",
    "Mean Residual Bytes",
    "Recv Avg %",
    "Recv Min %",
    "ContentOK %",
    "BadHash",
    "Notes",
]

LOWER_IS_BETTER = {
    "Failed Samples",
    "Latency Min (ms)",
    "Latency Mean (ms)",
    "Latency Median (ms)",
    "Latency P95 (ms)",
    "Latency P99 (ms)",
    "Latency Max (ms)",
    "Latency CI95 +/- (ms)",
    "Mean Residual Bytes",
    "BadHash",
}

HIGHER_IS_BETTER = {
    "Successful Samples (N)",
    "Success %",
    "Mean Throughput (KiB/s)",
    "Recv Avg %",
    "Recv Min %",
    "ContentOK %",
}


def as_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def is_warmup(row: Dict[str, str]) -> bool:
    return str(row.get("warmup", "")).strip() in {"1", "true", "True"}


def percentile(values: List[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def round_value(value: float | None, digits: int = 2) -> float | str:
    if value is None:
        return "N/A"
    return round(value, digits)


def fixture_name(command: str) -> str:
    name = command.replace("cat /tmp/w1_fixture_", "").replace(".txt", "")
    return f"fixture {name}" if name != command else command


def load_rows() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for scenario in SCENARIOS:
        path = ROOT / scenario / "w1_line_log.csv"
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["scenario"] = row.get("scenario") or scenario
                rows.append(row)
    return rows


def summarize_group(rows: List[Dict[str, str]], scenario: str, protocol: str) -> Dict[str, Any]:
    measured = [r for r in rows if not is_warmup(r)]
    ok = [r for r in measured if r.get("status") == "ok"]
    fail = [r for r in measured if r.get("status") != "ok"]
    lat = [float(r["latency_ms"]) for r in ok if as_float(r.get("latency_ms")) is not None]
    out_bytes = [float(r["output_bytes"]) for r in ok if as_float(r.get("output_bytes")) is not None]
    residual = [
        float(r["residual_bytes"])
        for r in ok
        if as_float(r.get("residual_bytes")) is not None
    ]
    recv = [float(r["received_pct"]) for r in ok if as_float(r.get("received_pct")) is not None]
    throughput = [
        (float(r["output_bytes"]) / 1024.0) / (float(r["latency_ms"]) / 1000.0)
        for r in ok
        if as_float(r.get("output_bytes")) is not None
        and as_float(r.get("latency_ms")) not in (None, 0.0)
    ]
    content_values = [str(r.get("content_match", "")).lower() for r in ok]
    bad_hash = sum(1 for v in content_values if v == "false")
    content_ok_pct = (
        100.0 * sum(1 for v in content_values if v == "true") / len(content_values)
        if content_values
        else None
    )
    total = len(ok) + len(fail)
    ci95 = 1.96 * statistics.stdev(lat) / math.sqrt(len(lat)) if len(lat) > 1 else (0.0 if lat else None)

    notes: List[str] = []
    if fail:
        notes.append(f"{len(fail)} non-warmup failures")
    if bad_hash:
        notes.append(f"{bad_hash} successful samples had content mismatch")

    return {
        "Scenario": scenario,
        "Protocol": protocol,
        "Successful Samples (N)": len(ok),
        "Failed Samples": len(fail),
        "Success %": round_value((100.0 * len(ok) / total) if total else None, 1),
        "Latency Min (ms)": round_value(min(lat) if lat else None),
        "Latency Mean (ms)": round_value(statistics.mean(lat) if lat else None),
        "Latency Median (ms)": round_value(statistics.median(lat) if lat else None),
        "Latency P95 (ms)": round_value(percentile(lat, 95)),
        "Latency P99 (ms)": round_value(percentile(lat, 99)),
        "Latency Max (ms)": round_value(max(lat) if lat else None),
        "Latency CI95 +/- (ms)": round_value(ci95),
        "Mean Output (B)": round_value(statistics.mean(out_bytes) if out_bytes else None),
        "Mean Throughput (KiB/s)": round_value(statistics.mean(throughput) if throughput else None),
        "Mean Residual Bytes": round_value(statistics.mean(residual) if residual else None),
        "Recv Avg %": round_value(statistics.mean(recv) if recv else None),
        "Recv Min %": round_value(min(recv) if recv else None),
        "ContentOK %": round_value(content_ok_pct),
        "BadHash": bad_hash if content_values else "N/A",
        "Notes": "; ".join(notes),
    }


def build_tables(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    summary: List[Dict[str, Any]] = []
    detail: List[Dict[str, Any]] = []

    for scenario in SCENARIOS:
        for protocol in PROTOCOLS:
            group = [
                r for r in rows
                if r.get("scenario") == scenario and r.get("protocol") == protocol
            ]
            summary.append(summarize_group(group, scenario, protocol))

    by_workload: Dict[Tuple[str, str, str, str], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("scenario", ""),
            fixture_name(row.get("command", "")),
            row.get("command", ""),
            row.get("protocol", ""),
        )
        by_workload[key].append(row)

    for scenario in SCENARIOS:
        commands = sorted(
            {
                (fixture_name(r.get("command", "")), r.get("command", ""))
                for r in rows
                if r.get("scenario") == scenario
            }
        )
        for workload, command in commands:
            for protocol in PROTOCOLS:
                group = by_workload.get((scenario, workload, command, protocol), [])
                row = summarize_group(group, scenario, protocol)
                row["Workload"] = workload
                row["Command"] = command
                detail.append({col: row.get(col, "") for col in WORKLOAD_COLUMNS})

    return summary, detail


def write_csv(path: Path, columns: List[str], rows: List[Dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


def col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def style_map(
    columns: List[str],
    rows: List[Dict[str, Any]],
    group_cols: Iterable[str],
) -> Dict[Tuple[int, int], int]:
    styles: Dict[Tuple[int, int], int] = {}
    groups: Dict[Tuple[Any, ...], List[int]] = defaultdict(list)
    for idx, row in enumerate(rows, start=2):
        groups[tuple(row.get(col) for col in group_cols)].append(idx)

    for indices in groups.values():
        for col_idx, col in enumerate(columns, start=1):
            if col not in LOWER_IS_BETTER and col not in HIGHER_IS_BETTER:
                continue
            values: List[Tuple[int, float]] = []
            for row_idx in indices:
                value = rows[row_idx - 2].get(col)
                if isinstance(value, (int, float)):
                    values.append((row_idx, float(value)))
            if not values:
                continue
            nums = [value for _, value in values]
            best = min(nums) if col in LOWER_IS_BETTER else max(nums)
            worst = max(nums) if col in LOWER_IS_BETTER else min(nums)
            for row_idx, value in values:
                if value == best:
                    styles[(row_idx, col_idx)] = 2
                if worst != best and value == worst:
                    styles[(row_idx, col_idx)] = 3

    for row_idx, row in enumerate(rows, start=2):
        for col_idx, col in enumerate(columns, start=1):
            if row.get(col) == "N/A":
                styles[(row_idx, col_idx)] = 4
    return styles


def sheet_xml(columns: List[str], rows: List[Dict[str, Any]], styles: Dict[Tuple[int, int], int]) -> str:
    sheet_rows = []
    header_cells = []
    for col_idx, header in enumerate(columns, start=1):
        ref = f"{col_name(col_idx)}1"
        header_cells.append(f'<c r="{ref}" s="1" t="inlineStr"><is><t>{escape(header)}</t></is></c>')
    sheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')

    for row_idx, row in enumerate(rows, start=2):
        cells = []
        for col_idx, col in enumerate(columns, start=1):
            ref = f"{col_name(col_idx)}{row_idx}"
            value = row.get(col, "")
            style = styles.get((row_idx, col_idx), 0)
            style_attr = f' s="{style}"' if style else ""
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"{style_attr}><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}"{style_attr} t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        sheet_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    last_col = col_name(len(columns))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <dimension ref="A1:{last_col}{len(rows) + 1}"/>
 <sheetViews><sheetView workbookViewId="0"/></sheetViews>
 <sheetFormatPr defaultRowHeight="15"/>
 <cols>{''.join(f'<col min="{i}" max="{i}" width="{max(12, min(42, len(c) + 4))}" customWidth="1"/>' for i, c in enumerate(columns, start=1))}</cols>
 <sheetData>{''.join(sheet_rows)}</sheetData>
 <autoFilter ref="A1:{last_col}{len(rows) + 1}"/>
</worksheet>'''


def styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <fonts count="2">
  <font><sz val="11"/><name val="Calibri"/></font>
  <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
 </fonts>
 <fills count="7">
  <fill><patternFill patternType="none"/></fill>
  <fill><patternFill patternType="gray125"/></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FFC6EFCE"/><bgColor indexed="64"/></patternFill></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FFFFC7CE"/><bgColor indexed="64"/></patternFill></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FFD9EAD3"/><bgColor indexed="64"/></patternFill></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FFE7E6E6"/><bgColor indexed="64"/></patternFill></fill>
 </fills>
 <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
 <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
 <cellXfs count="5">
  <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
  <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
  <xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>
  <xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1"/>
  <xf numFmtId="0" fontId="0" fillId="6" borderId="0" xfId="0" applyFill="1"/>
 </cellXfs>
 <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def workbook_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets>
  <sheet name="Scenario Summary" sheetId="1" r:id="rId1"/>
  <sheet name="By Workload" sheetId="2" r:id="rId2"/>
  <sheet name="Notes" sheetId="3" r:id="rId3"/>
 </sheets>
</workbook>'''


def notes_rows() -> List[Dict[str, Any]]:
    return [
        {"Note": "Green = best value in the scenario group; red = worst value in the scenario group."},
        {"Note": "Warmup samples are excluded from all summary metrics."},
        {"Note": "For latency, failures, residual bytes, and BadHash, lower is better."},
        {"Note": "For success rate, throughput, recv%, and ContentOK%, higher is better."},
        {"Note": "ContentOK% and BadHash are computed directly from content_match in w1_line_log.csv."},
    ]


def write_xlsx(path: Path, summary: List[Dict[str, Any]], detail: List[Dict[str, Any]]) -> None:
    sheets = [
        (SUMMARY_COLUMNS, summary, style_map(SUMMARY_COLUMNS, summary, ["Scenario"])),
        (WORKLOAD_COLUMNS, detail, style_map(WORKLOAD_COLUMNS, detail, ["Scenario", "Workload"])),
        (["Note"], notes_rows(), {}),
    ]
    sheet_xmls = [sheet_xml(cols, rows, styles) for cols, rows, styles in sheets]

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
 <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
 <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
 <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
 <Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>''')
        zf.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        zf.writestr("xl/workbook.xml", workbook_xml())
        zf.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
 <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/>
 <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>''')
        zf.writestr("xl/styles.xml", styles_xml())
        for idx, xml in enumerate(sheet_xmls, start=1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", xml)


def numeric(row: Dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def draw_grouped_bar(
    rows: List[Dict[str, Any]],
    metric: str,
    ylabel: str,
    title: str,
    output_stem: str,
    error_metric: str | None = None,
    ylim: Tuple[float, float] | None = None,
) -> None:
    x = list(range(len(SCENARIOS)))
    width = 0.24
    offsets = {"ssh": -width, "ssh3": 0.0, "mosh": width}

    fig, ax = plt.subplots(figsize=(9.5, 5.2), dpi=160)
    max_top = 0.0
    for protocol in PROTOCOLS:
        vals = []
        errs = []
        for scenario in SCENARIOS:
            row = next(r for r in rows if r["Scenario"] == scenario and r["Protocol"] == protocol)
            vals.append(numeric(row, metric) or 0.0)
            errs.append(numeric(row, error_metric) or 0.0 if error_metric else 0.0)
        xpos = [i + offsets[protocol] for i in x]
        bars = ax.bar(
            xpos,
            vals,
            width,
            label=PROTO_LABELS[protocol],
            color=HATCH_FILLS[protocol],
            edgecolor=COLORS[protocol],
            linewidth=1.2,
            hatch=HATCHES[protocol],
            yerr=errs if error_metric else None,
            error_kw={"elinewidth": 1.0, "capsize": 3, "ecolor": "#333333"},
        )
        for bar, val, err in zip(bars, vals, errs):
            top = val + err
            max_top = max(max_top, top)
            label = f"{val:.0f}" if abs(val) >= 10 else f"{val:.1f}"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                top + max(0.4, max_top * 0.012),
                label,
                ha="center",
                va="bottom",
                fontsize=9,
                color=COLORS[protocol],
            )

    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.set_xticks(x, ["VPN", "Low", "Medium", "High"])
    ax.set_xlabel("Network scenario")
    ax.grid(axis="y", linestyle="--", alpha=0.45)
    ax.grid(axis="x", linestyle="--", alpha=0.25)
    ax.legend(ncol=3, frameon=False, loc="upper left")
    if ylim is not None:
        ax.set_ylim(*ylim)
    else:
        ax.set_ylim(0, max_top * 1.18 if max_top else 1)
    fig.tight_layout()
    png = ROOT / f"{output_stem}.png"
    pdf = ROOT / f"{output_stem}.pdf"
    fig.savefig(png, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)


def write_plots(summary: List[Dict[str, Any]]) -> None:
    draw_grouped_bar(
        summary,
        "Latency Mean (ms)",
        "Mean latency (ms)",
        "W1: Mean Command Latency by Scenario",
        "w1_latency_by_scenario",
        error_metric="Latency CI95 +/- (ms)",
    )
    draw_grouped_bar(
        summary,
        "Success %",
        "Success rate (%)",
        "W1: Success Rate by Scenario",
        "w1_success_rate_by_scenario",
        ylim=(0, 108),
    )
    draw_grouped_bar(
        summary,
        "Recv Avg %",
        "Bytes received (%)",
        "W1: Mean Bytes Received by Scenario",
        "w1_recv_pct_by_scenario",
        ylim=(0, 108),
    )
    draw_grouped_bar(
        summary,
        "ContentOK %",
        "Content match (%)",
        "W1: Content Match by Scenario",
        "w1_content_ok_by_scenario",
        ylim=(0, 108),
    )


def main() -> int:
    rows = load_rows()
    summary, detail = build_tables(rows)
    write_csv(ROOT / "w1_google_sheet_scenario_summary.csv", SUMMARY_COLUMNS, summary)
    write_csv(ROOT / "w1_google_sheet_by_workload.csv", WORKLOAD_COLUMNS, detail)
    write_xlsx(ROOT / "w1_google_sheet_summary.xlsx", summary, detail)
    write_plots(summary)
    print(ROOT / "w1_google_sheet_summary.xlsx")
    print(ROOT / "w1_google_sheet_scenario_summary.csv")
    print(ROOT / "w1_google_sheet_by_workload.csv")
    print(ROOT / "w1_latency_by_scenario.png")
    print(ROOT / "w1_success_rate_by_scenario.png")
    print(ROOT / "w1_recv_pct_by_scenario.png")
    print(ROOT / "w1_content_ok_by_scenario.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
