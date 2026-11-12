"""Markdown export, and the section layout shared with the HTML export.

`snapshot_sections()` turns a snapshot into an ordered list of sections. Each is
    {"id", "title", "kind": "kv" | "table" | "note", ...}
  kv    -> "rows": [(label, value), ...]
  table -> "columns": [...], "table": [[cell, ...], ...]
  note  -> "text"
A cell or value is either text or a (text, level) pair, level being
'good' / 'warn' / 'bad' / 'muted'. Markdown shows the level as an icon and the
HTML export shows it as a coloured badge.
"""

from __future__ import annotations

from typing import Any

from ..utils import NA, format_bytes

LEVEL_WORD = {"good": "OK", "warn": "Warning", "bad": "Problem", "muted": "Unknown"}
LEVEL_ICON = {"good": "✅", "warn": "⚠️", "bad": "❌", "muted": "⚪"}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _s(value: Any) -> str:
    """Text for a value; empty or missing becomes N/A."""
    if value is None:
        return NA
    text = str(value).strip()
    return text or NA


def _pct(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return NA


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _level(value: Any) -> str:
    return value if value in LEVEL_WORD else "muted"


def _used_level(percent: Any) -> str:
    try:
        p = float(percent)
    except (TypeError, ValueError):
        return "muted"
    return "bad" if p >= 95 else "warn" if p >= 85 else "good"


def _health_level(text: str) -> str:
    low = text.lower()
    if low in ("", "n/a", "unknown"):
        return "muted"
    return "good" if low in ("healthy", "ok", "good", "passed") else "warn"


def cell_text(cell: Any) -> str:
    """Plain text of a cell (drops the level)."""
    return str(cell[0]) if isinstance(cell, tuple) else str(cell)


def cell_level(cell: Any) -> str:
    return _level(cell[1]) if isinstance(cell, tuple) and len(cell) > 1 else ""


def _status(level: str, text: str = "") -> tuple[str, str]:
    level = _level(level)
    return (text or LEVEL_WORD[level], level)


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
def snapshot_sections(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordered sections describing everything in the snapshot (missing data shows N/A)."""
    sections: list[dict[str, Any]] = []

    def kv(sid: str, title: str, rows: list[tuple[str, Any]]) -> None:
        sections.append({"id": sid, "title": title, "kind": "kv", "rows": rows})

    def table(sid: str, title: str, columns: list[str], rows: list[list[Any]], note: str = "") -> None:
        sections.append({"id": sid, "title": title, "kind": "table", "columns": columns, "table": rows, "note": note})

    def note(sid: str, title: str, text: str) -> None:
        sections.append({"id": sid, "title": title, "kind": "note", "text": text})

    # ---- health -----------------------------------------------------------
    health = _d(snapshot.get("health"))
    score = _d(health.get("score"))
    counts = _d(score.get("counts"))
    kv(
        "health_summary",
        "Health summary",
        [
            ("Score", (f"{_s(score.get('score'))} / 100", _level(score.get("level")))),
            ("Grade", (_s(score.get("grade")), _level(score.get("level")))),
            (
                "Checks",
                f"{counts.get('good', 0)} OK, {counts.get('warn', 0)} warning, "
                f"{counts.get('bad', 0)} problem, {counts.get('unknown', 0)} unknown",
            ),
        ]
        + ([("Note", "A serious problem keeps the score from reaching the top range.")] if score.get("capped") else []),
    )
    checks = _l(health.get("checks"))
    if checks:
        table(
            "health_checks",
            "Health checks",
            ["Category", "Check", "Status", "Details", "How to fix"],
            [
                [_s(c.get("category")), _s(c.get("label")), _status(c.get("level", "muted")), _s(c.get("value")), c.get("hint") or "-"]
                for c in checks
                if isinstance(c, dict)
            ],
        )

    # ---- system -----------------------------------------------------------
    system, os_info = _d(snapshot.get("system")), _d(snapshot.get("os"))
    kv(
        "system",
        "System",
        [
            ("Brand", _s(system.get("brand"))),
            ("Model", _s(system.get("model"))),
            ("Serial number", _s(system.get("serial"))),
            ("Host name", _s(system.get("hostname"))),
            ("Operating system", _s(os_info.get("name"))),
            ("OS version", f"{_s(os_info.get('version'))} (build {_s(os_info.get('build'))})"),
            ("Architecture", _s(os_info.get("architecture"))),
            ("Uptime", _s(os_info.get("uptime"))),
        ],
    )

    bios = _d(snapshot.get("bios"))
    board = " ".join(p for p in (_s(bios.get("board_vendor")), _s(bios.get("board_model"))) if p != NA) or NA
    kv(
        "bios",
        "BIOS & Motherboard",
        [
            ("BIOS vendor", _s(bios.get("vendor"))),
            ("BIOS version", _s(bios.get("version"))),
            ("BIOS date", _s(bios.get("release_date"))),
            ("SMBIOS", _s(bios.get("smbios"))),
            ("Boot mode", _s(bios.get("mode"))),
            ("Secure Boot", _s(bios.get("secure_boot"))),
            ("Motherboard", board),
            ("Board serial", _s(bios.get("board_serial"))),
        ],
    )

    cpu = _d(snapshot.get("cpu"))
    kv(
        "cpu",
        "Processor",
        [
            ("Name", _s(cpu.get("name"))),
            ("Generation", _s(cpu.get("generation"))),
            ("Cores / Threads", f"{_s(cpu.get('cores'))} / {_s(cpu.get('threads'))}"),
            ("Rated speed", _s(cpu.get("max_speed"))),
            ("Load when scanned", _pct(cpu.get("usage_percent"))),
        ],
    )

    mem = _d(snapshot.get("memory"))
    modules = [m for m in _l(mem.get("modules")) if isinstance(m, dict)]
    mem_rows: list[tuple[str, Any]] = [
        ("Total", _s(mem.get("total_text"))),
        ("Type / Speed", f"{_s(mem.get('type'))} / {_s(mem.get('speed'))}"),
        ("In use when scanned", (_pct(mem.get("percent")), _used_level(mem.get("percent")))),
    ]
    if modules:
        mem_rows.append((f"Modules ({len(modules)})", ", ".join(f"{_s(m.get('capacity'))} {_s(m.get('type'))}" for m in modules)))
    kv("memory", "Memory", mem_rows)

    # ---- storage ----------------------------------------------------------
    storage = _d(snapshot.get("storage"))
    disk = _d(snapshot.get("disk_health"))
    if disk.get("available") and _l(disk.get("disks")):
        rows = []
        for d in _l(disk.get("disks")):
            d = _d(d)
            temp, wear, hours = d.get("temperature_c"), d.get("wear_percent"), d.get("power_on_hours")
            rows.append(
                [
                    _s(d.get("model")),
                    _s(d.get("type")),
                    _s(d.get("size")),
                    _status(d.get("level", "muted"), _s(d.get("status"))),
                    f"{temp:.0f} C" if isinstance(temp, (int, float)) else NA,
                    f"{wear:.0f}%" if isinstance(wear, (int, float)) else NA,
                    f"{hours:,} h" if isinstance(hours, int) else NA,
                ]
            )
        notes = [f"{_s(_d(d).get('model'))}: {r}" for d in _l(disk.get("disks")) for r in _l(_d(d).get("reasons"))]
        pf = disk.get("predict_failure")
        notes.append(
            "SMART failure prediction: not available on this PC"
            if pf is None
            else "SMART failure prediction: failure predicted" if pf else "SMART failure prediction: no failure predicted"
        )
        table("drives", "Physical drives", ["Model", "Type", "Size", "Health", "Temp", "Life used", "Power-on"], rows, "; ".join(notes))
    elif _l(storage.get("drives")):
        table(
            "drives",
            "Physical drives",
            ["Model", "Type", "Bus", "Size", "Health"],
            [
                [_s(d.get("model")), _s(d.get("type")), _s(d.get("bus")), _s(d.get("size")), _status(_health_level(_s(d.get("health"))), _s(d.get("health")))]
                for d in map(_d, _l(storage.get("drives")))
            ],
        )
    volumes = [_d(v) for v in _l(storage.get("volumes"))]
    if volumes:
        table(
            "volumes",
            "Volumes",
            ["Mount", "File system", "Total", "Free", "Used"],
            [[_s(v.get("mount")), _s(v.get("fstype")), _s(v.get("total_text")), _s(v.get("free_text")), (_pct(v.get("percent")), _used_level(v.get("percent")))] for v in volumes],
        )

    gpus = [_d(g) for g in _l(snapshot.get("gpu"))]
    if gpus:
        table("gpu", "Graphics", ["Adapter", "Driver", "Video memory"], [[_s(g.get("name")), _s(g.get("driver")), _s(g.get("vram"))] for g in gpus])
    else:
        kv("gpu", "Graphics", [("Adapter", NA)])

    # ---- battery / thermals ----------------------------------------------
    batt = _d(snapshot.get("battery"))
    charge, bh = _d(batt.get("charge")), _d(batt.get("health"))
    if charge.get("present"):
        percent = charge.get("percent")
        charge_text = f"{percent:.0f}% " if isinstance(percent, (int, float)) else "N/A "
        charge_text += "(charging / plugged in)" if charge.get("plugged") else "(on battery)"
        brows: list[tuple[str, Any]] = [("Charge when scanned", charge_text), ("Time left", _s(charge.get("time_left")))]
        if bh.get("present"):
            wear = bh.get("wear_percent")
            health_pct = bh.get("health_percent")
            wear_level = "muted" if wear is None else "bad" if wear >= 40 else "warn" if wear >= 20 else "good"
            brows += [
                ("Design capacity", f"{bh['design_mwh']:,} mWh" if bh.get("design_mwh") else NA),
                ("Full-charge capacity", f"{bh['full_mwh']:,} mWh" if bh.get("full_mwh") else NA),
                ("Battery wear", (_pct(wear), wear_level)),
                ("Battery health", _pct(health_pct)),
                ("Charge cycles", _s(bh.get("cycles"))),
                ("Chemistry", _s(bh.get("chemistry"))),
            ]
        kv("battery", "Battery", brows)
    else:
        kv("battery", "Battery", [("Status", "No battery detected")])

    temps = _d(snapshot.get("temperatures"))
    sensors = [_d(s) for s in _l(temps.get("sensors"))]
    if sensors:
        kv(
            "temperatures",
            "Temperatures",
            [(_s(s.get("label")), (f"{s['current_c']:.1f} C", _level(s.get("level")))) for s in sensors if isinstance(s.get("current_c"), (int, float))],
        )
    else:
        kv("temperatures", "Temperatures", [("Sensors", _s(temps.get("note")) if temps.get("note") else "Not available")])

    # ---- network / security / startup ------------------------------------
    adapters = [_d(i) for i in _l(_d(snapshot.get("network")).get("interfaces"))]
    if adapters:
        table(
            "network",
            "Network adapters",
            ["Adapter", "Status", "Speed", "IPv4", "MAC"],
            [
                [
                    _s(a.get("name")),
                    ("Up", "good") if a.get("up") else ("Down", "muted"),
                    f"{a['speed_mbps']} Mbps" if a.get("speed_mbps") else NA,
                    _s(a.get("ipv4")),
                    _s(a.get("mac")),
                ]
                for a in adapters
            ],
        )

    sec = _d(snapshot.get("security"))
    if sec.get("available") and _l(sec.get("checks")):
        table(
            "security",
            "Security",
            ["Check", "Status", "Details", "How to fix"],
            [[_s(c.get("label")), _status(c.get("level", "muted")), _s(c.get("value")), c.get("hint") or "-"] for c in map(_d, _l(sec.get("checks")))],
        )
    else:
        kv("security", "Security", [("Status", _s(sec.get("note")) if sec.get("note") else "Not available")])

    startup = _d(snapshot.get("startup"))
    if startup.get("available") and _l(startup.get("items")):
        table(
            "startup",
            "Startup programs",
            ["Program", "Source", "Enabled", "Memory now", "Impact"],
            [
                [_s(i.get("name")), _s(i.get("source")), "Yes" if i.get("enabled") else "No", _s(i.get("memory_text")), _status(i.get("level", "muted"), _s(i.get("impact")))]
                for i in map(_d, _l(startup.get("items")))
            ],
        )
    else:
        kv("startup", "Startup programs", [("Status", _s(startup.get("note")) if startup.get("note") else "None found")])

    # ---- optional lists ---------------------------------------------------
    drivers = _d(snapshot.get("drivers"))
    if drivers:
        if drivers.get("available") and _l(drivers.get("drivers")):
            labels = {"good": "Recent", "warn": "Aging", "bad": "Old", "muted": NA}
            table(
                "drivers",
                "Drivers (oldest first)",
                ["Device", "Class", "Vendor", "Version", "Date", "Age", "Status"],
                [
                    [_s(d.get("name")), _s(d.get("cls")), _s(d.get("vendor")), _s(d.get("version")), _s(d.get("date")), _s(d.get("age_text")), _status(d.get("level", "muted"), labels.get(d.get("level"), NA))]
                    for d in map(_d, _l(drivers.get("drivers")))
                ],
            )
        else:
            kv("drivers", "Drivers", [("Status", _s(drivers.get("note")) if drivers.get("note") else "Not available")])

    apps = _d(snapshot.get("apps"))
    if apps:
        if apps.get("available") and _l(apps.get("apps")):
            total = format_bytes(apps["total_bytes"]) if apps.get("total_bytes") else NA
            table(
                "apps",
                f"Installed apps ({len(_l(apps.get('apps')))})",
                ["App", "Version", "Publisher", "Installed", "Size"],
                [[_s(a.get("name")), _s(a.get("version")), _s(a.get("publisher")), _s(a.get("install_date")), _s(a.get("size"))] for a in map(_d, _l(apps.get("apps")))],
                f"Size reported by apps: {total}.",
            )
        else:
            kv("apps", "Installed apps", [("Status", _s(apps.get("note")) if apps.get("note") else "Not available")])

    return sections


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------
def _md(text: str) -> str:
    """Escape a cell so it cannot break a Markdown table."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip() or "-"


def _md_cell(cell: Any) -> str:
    level = cell_level(cell)
    text = _md(cell_text(cell))
    return f"{LEVEL_ICON[level]} {text}" if level else text


def to_markdown(snapshot: dict[str, Any]) -> str:
    """The snapshot as a GitHub-flavoured Markdown report."""
    tool = _d(snapshot.get("tool"))
    privacy = _d(snapshot.get("privacy"))
    out = [
        f"# {_s(tool.get('name'))} system report",
        "",
        f"_Generated {_s(snapshot.get('generated_at'))} by {_s(tool.get('name'))} {_s(tool.get('version'))}. "
        "Created on this computer; nothing was sent anywhere._",
        "",
    ]
    if privacy.get("private_details_hidden"):
        out += ["> Serial numbers, MAC addresses and IP addresses are hidden in this report.", ""]
    else:
        out += ["> **Private details are shown** (serial numbers, MAC and IP addresses). Do not share this file publicly.", ""]

    for section in snapshot_sections(snapshot):
        out += [f"## {section['title']}", ""]
        if section["kind"] == "kv":
            out += ["| Field | Value |", "| --- | --- |"]
            out += [f"| {_md(label)} | {_md_cell(value)} |" for label, value in section["rows"]]
        elif section["kind"] == "table":
            out += ["| " + " | ".join(_md(c) for c in section["columns"]) + " |", "| " + " | ".join("---" for _ in section["columns"]) + " |"]
            out += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in section["table"]]
            if section.get("note"):
                out += ["", f"_{section['note']}_"]
        else:
            out.append(section["text"])
        out.append("")
    return "\n".join(out).rstrip() + "\n"
