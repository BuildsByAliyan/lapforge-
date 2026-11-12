"""HTML export: one self-contained, dark-theme report page.

No scripts, no external fonts or images, no network requests: the file opens
offline and can be printed. Every value is HTML-escaped.
"""

from __future__ import annotations

from html import escape
from typing import Any

from .markdown_export import LEVEL_ICON, LEVEL_WORD, cell_level, cell_text, snapshot_sections

_CSS = """
:root{color-scheme:dark;--bg:#0d1117;--card:#161b22;--card2:#1c2230;--line:#30363d;--text:#e6edf3;--muted:#8b949e;
--brand:#00d7af;--accent:#5fafff;--good:#5fd75f;--warn:#ffd75f;--bad:#ff5f5f}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 "Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 16px 48px}
header.top{display:flex;flex-wrap:wrap;gap:16px;align-items:center;justify-content:space-between;
border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:22px}
.logo{font:700 30px/1 Consolas,"Cascadia Mono",monospace;letter-spacing:.14em;color:var(--brand)}
.tag{color:var(--muted);margin-top:6px;font-size:13px}
.meta{color:var(--muted);font-size:13px;text-align:right}
.notice{border:1px solid var(--line);border-left:4px solid var(--accent);background:var(--card);border-radius:8px;
padding:10px 14px;margin-bottom:22px;color:var(--muted);font-size:14px}
.notice.warn{border-left-color:var(--warn);color:var(--text)}
.hero{display:flex;flex-wrap:wrap;gap:24px;align-items:center;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:22px;margin-bottom:22px}
.ring{--p:0;--c:var(--good);width:132px;height:132px;border-radius:50%;flex:none;display:grid;place-items:center;
background:conic-gradient(var(--c) calc(var(--p)*1%),var(--card2) 0)}
.ring>div{width:104px;height:104px;border-radius:50%;background:var(--card);display:grid;place-items:center;text-align:center}
.ring b{font-size:34px;line-height:1}.ring span{display:block;font-size:12px;color:var(--muted)}
.ring.good{--c:var(--good)}.ring.warn{--c:var(--warn)}.ring.bad{--c:var(--bad)}.ring.muted{--c:var(--muted)}
.hero h2{margin:0 0 4px;font-size:22px}
.counts{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
section.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:18px}
section.card h2{margin:0 0 12px;font-size:17px;color:var(--brand)}
dl.kv{display:grid;grid-template-columns:minmax(120px,220px) 1fr;gap:0;margin:0}
dl.kv dt,dl.kv dd{margin:0;padding:7px 0;border-top:1px solid var(--line)}
dl.kv dt:first-of-type,dl.kv dd:first-of-type{border-top:0}
dl.kv dt{color:var(--muted)}dl.kv dd{overflow-wrap:anywhere}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{color:var(--accent);text-align:left;font-weight:600;padding:8px 10px;border-bottom:2px solid var(--line);white-space:nowrap}
td{padding:7px 10px;border-top:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}
tbody tr:nth-child(even){background:var(--card2)}
.badge{display:inline-block;border-radius:999px;padding:1px 10px;font-size:12.5px;font-weight:600;border:1px solid currentColor;white-space:nowrap}
.badge.good{color:var(--good)}.badge.warn{color:var(--warn)}.badge.bad{color:var(--bad)}.badge.muted{color:var(--muted)}
.note{color:var(--muted);font-size:13px;margin:10px 0 0}
footer{color:var(--muted);font-size:13px;text-align:center;margin-top:30px;border-top:1px solid var(--line);padding-top:16px}
@media(max-width:560px){dl.kv{grid-template-columns:1fr}dl.kv dt{border-bottom:0;padding-bottom:0}dl.kv dd{border-top:0;padding-top:0}
.meta{text-align:left}.hero{padding:16px}}
@media print{:root{color-scheme:light}html,body{background:#fff}body{color:#111}
section.card,.hero,.notice{background:#fff;border-color:#bbb;break-inside:avoid}th,section.card h2,.logo{color:#0a5}
.badge.good{color:#0a7a0a}.badge.warn{color:#8a6d00}.badge.bad{color:#b00020}dl.kv dt,.note,.tag,.meta,footer{color:#555}}
"""


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _cell_html(cell: Any) -> str:
    level = cell_level(cell)
    text = _e(cell_text(cell))
    if level:
        return f'<span class="badge {level}">{LEVEL_ICON[level]} {text}</span>'
    return text


def _section_html(section: dict[str, Any]) -> str:
    parts = [f'<section class="card" id="{_e(section["id"])}"><h2>{_e(section["title"])}</h2>']
    if section["kind"] == "kv":
        parts.append('<dl class="kv">')
        for label, value in section["rows"]:
            parts.append(f"<dt>{_e(label)}</dt><dd>{_cell_html(value)}</dd>")
        parts.append("</dl>")
    elif section["kind"] == "table":
        parts.append('<div class="scroll"><table><thead><tr>')
        parts.append("".join(f"<th>{_e(c)}</th>" for c in section["columns"]))
        parts.append("</tr></thead><tbody>")
        for row in section["table"]:
            parts.append("<tr>" + "".join(f"<td>{_cell_html(c)}</td>" for c in row) + "</tr>")
        parts.append("</tbody></table></div>")
        if section.get("note"):
            parts.append(f'<p class="note">{_e(section["note"])}</p>')
    else:
        parts.append(f'<p class="note">{_e(section["text"])}</p>')
    parts.append("</section>")
    return "".join(parts)


def _hero_html(snapshot: dict[str, Any]) -> str:
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    score = health.get("score") if isinstance(health.get("score"), dict) else {}
    value = score.get("score")
    level = score.get("level") if score.get("level") in LEVEL_WORD else "muted"
    percent = value if isinstance(value, (int, float)) and 0 <= value <= 100 else 0
    counts = score.get("counts") if isinstance(score.get("counts"), dict) else {}
    badges = "".join(
        f'<span class="badge {lv}">{_e(counts.get(key, 0))} {_e(word)}</span>'
        for key, lv, word in (("good", "good", "OK"), ("warn", "warn", "warning"), ("bad", "bad", "problem"), ("unknown", "muted", "unknown"))
    )
    grade = _e(score.get("grade", "N/A"))
    shown = _e(value) if value is not None else "N/A"
    return (
        '<div class="hero">'
        f'<div class="ring {level}" style="--p:{float(percent):.0f}" role="img" aria-label="Health score {shown} out of 100">'
        f"<div><div><b>{shown}</b><span>out of 100</span></div></div></div>"
        f'<div><h2>Health: <span class="badge {level}">{grade}</span></h2>'
        '<div class="note" style="margin:0">Scored from storage, memory, battery, startup, security, drive health and temperature checks.</div>'
        f'<div class="counts">{badges}</div></div></div>'
    )


def to_html(snapshot: dict[str, Any]) -> str:
    """The snapshot as a complete HTML page (string)."""
    tool = snapshot.get("tool") if isinstance(snapshot.get("tool"), dict) else {}
    privacy = snapshot.get("privacy") if isinstance(snapshot.get("privacy"), dict) else {}
    name, version = _e(tool.get("name", "LapForge")), _e(tool.get("version", ""))
    system = snapshot.get("system") if isinstance(snapshot.get("system"), dict) else {}
    machine = " ".join(p for p in (str(system.get("brand", "")), str(system.get("model", ""))) if p and p != "N/A")

    if privacy.get("private_details_hidden"):
        notice = '<div class="notice">Serial numbers, MAC addresses and IP addresses are hidden in this report.</div>'
    else:
        notice = '<div class="notice warn"><b>Private details are shown</b> (serial numbers, MAC and IP addresses). Do not share this file publicly.</div>'

    body = [
        '<header class="top"><div>'
        f'<div class="logo">{name.upper()}</div>'
        f'<div class="tag">{_e(tool.get("tagline", "One command. Every detail of your laptop."))}</div></div>'
        f'<div class="meta">{_e(machine) + "<br>" if machine else ""}'
        f'Generated {_e(snapshot.get("generated_at", ""))}<br>{name} {version}</div></header>',
        notice,
        _hero_html(snapshot),
    ]
    body += [_section_html(s) for s in snapshot_sections(snapshot) if s["id"] != "health_summary"]
    body.append(
        f"<footer>Created locally by {name} {version}. Nothing was sent anywhere. "
        f"Developer: {_e(tool.get('author', ''))}</footer>"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="dark light">'
        '<meta name="robots" content="noindex">'
        f"<title>{name} report</title><style>{_CSS}</style></head>"
        f'<body><div class="wrap">{"".join(body)}</div></body></html>\n'
    )
