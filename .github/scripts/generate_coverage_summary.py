#!/usr/bin/env python3
"""Aggregate coverage XML reports and post a markdown summary.
Each coverage file is named <package>_<type>.xml (or just <package>.xml).
The script parses the XML, extracts line-rate and branch-rate, and builds a
markdown table with ✅/❌ based on an 80% threshold.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

COVERAGE_DIR = Path('coverage-reports')
OUTPUT = Path('coverage-comment.md')
THRESHOLD = 80.0

rows = []
overall_pass = True
for xml_path in COVERAGE_DIR.glob('*.xml'):
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        line = float(root.attrib.get('line-rate', 0)) * 100
        branch = float(root.attrib.get('branch-rate', 0)) * 100
        name = xml_path.stem  # e.g., sms-processor_unit
        line_status = '✅' if line >= THRESHOLD else '❌'
        branch_status = '✅' if branch >= THRESHOLD else '❌'
        if line < THRESHOLD or branch < THRESHOLD:
            overall_pass = False
        rows.append((name, f"{line:.1f}% {line_status}", f"{branch:.1f}% {branch_status}"))
    except Exception:
        # skip malformed files
        continue

markdown = "# Coverage Summary by Package/Test Type\n\n"
markdown += "| Package/Test Type | Line Coverage | Branch Coverage |\n"
markdown += "|-------------------|---------------|-----------------|\n"
for name, line, branch in rows:
    markdown += f"| {name} | {line} | {branch} |\n"
markdown += f"\n**Overall coverage meets 80% threshold:** {'✅ PASS' if overall_pass else '❌ FAIL'}\n"
OUTPUT.write_text(markdown)
print(markdown)
