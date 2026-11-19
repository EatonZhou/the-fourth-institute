"""Bounded diagnostics parsed from TeX output, independent of report storage."""
from __future__ import annotations
import re
from collections import deque
from collections.abc import Iterator
from itertools import islice
from typing import Any

def _diagnostic_severity(message: str) -> str:
    lowered = message.casefold()
    if any(
        token in lowered
        for token in (
            "error",
            "emergency stop",
            "fatal",
            "misplaced",
            "undefined control sequence",
            "runaway argument",
            "missing $",
            "extra alignment tab",
            "file ended while scanning",
        )
    ):
        return "error"
    if any(
        token in lowered
        for token in ("warning", "overfull", "underfull", "undefined")
    ):
        return "warning"
    return "info"

def _diagnostic_category(message: str) -> str:
    lowered = message.casefold()
    if any(token in lowered for token in ("not found", "missing input file", ".sty", ".cls")):
        return "dependency"
    if any(token in lowered for token in ("font", "fontspec", "typeface")):
        return "font"
    if any(token in lowered for token in ("citation", "reference", "multiply defined", "rerun")):
        return "reference"
    if any(token in lowered for token in ("overfull", "underfull", "badness")):
        return "layout"
    if any(token in lowered for token in ("shell escape", "write18", "restricted")):
        return "security"
    if any(token in lowered for token in ("latexmk", "compiler", "command not found")):
        return "environment"
    return "source"

def _latex_log_lines(log: str) -> Iterator[str]:
    """Yield splitlines-compatible text without materializing the whole log."""
    start = 0
    for boundary in re.finditer(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]", log):
        yield log[start:boundary.start()]
        start = boundary.end()
    if start < len(log):
        yield log[start:]

def parse_latex_diagnostics(log: str, *, limit: int = 240) -> list[dict[str, Any]]:
    lines = _latex_log_lines(str(log or ""))
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()

    def add(message: str, severity: str = "", file_name: str = "", line: int = 0) -> None:
        clean_message = re.sub(r"\s+", " ", str(message or "")).strip()
        if not clean_message:
            return
        clean_file = str(file_name or "").strip().removeprefix("./")
        clean_severity = severity or _diagnostic_severity(clean_message)
        key = (clean_severity, clean_file, int(line or 0), clean_message)
        if key in seen:
            return
        seen.add(key)
        diagnostics.append(
            {
                "severity": clean_severity,
                "category": _diagnostic_category(clean_message),
                "message": clean_message[:1200],
                "file": clean_file,
                "line": int(line or 0),
            }
        )

    location_pattern = re.compile(
        r"^(?P<file>(?:\./)?[^:\r\n]+):(?P<line>[0-9]+):\s*(?P<message>.+)$"
    )
    warning_pattern = re.compile(
        r"^(?:LaTeX|Package\s+\S+|Class\s+\S+|pdfTeX)\s+Warning:\s*(.+)$",
        re.IGNORECASE,
    )
    window = deque(islice(lines, 7))
    while window and len(diagnostics) < limit:
        raw_line = window.popleft()
        nearby_lines = tuple(window)
        following = next(lines, None)
        if following is not None:
            window.append(following)
        line = raw_line.strip()
        if not line:
            continue
        location = location_pattern.match(line)
        if location:
            message = location.group("message").strip()
            severity = _diagnostic_severity(message)
            if severity != "info" or any(
                token in message.casefold()
                for token in ("missing", "not found", "undefined", "multiply defined")
            ):
                add(
                    message,
                    severity,
                    location.group("file"),
                    int(location.group("line")),
                )
            continue
        if line.startswith("!"):
            file_name = ""
            line_number = 0
            for nearby in nearby_lines:
                located = location_pattern.match(nearby.strip())
                if located:
                    file_name = located.group("file")
                    line_number = int(located.group("line"))
                    break
                latex_line = re.search(r"\bl\.([0-9]+)\b", nearby)
                if latex_line:
                    line_number = int(latex_line.group(1))
            add(line.lstrip("! ").strip(), "error", file_name, line_number)
        else:
            warning = warning_pattern.match(line)
            if warning:
                add(warning.group(1), "warning")
            elif line.startswith(("Overfull ", "Underfull ")):
                add(line, "warning")
            elif "Missing input file" in line or re.search(
                r"(?:LaTeX Error:\s*)?File\s+[`'][^`']+['`]\s+not found", line, re.IGNORECASE
            ):
                add(line, "error")
    return diagnostics

