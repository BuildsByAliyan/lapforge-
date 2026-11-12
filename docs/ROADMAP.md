# LapForge Roadmap

Status of the project and ideas for what could come next.

## Done

- **Core**: argparse CLI, dark theme with green / yellow / red status colours, ASCII banner and side-by-side dashboard.
- **Hardware and software info**: CPU, RAM, storage, GPU, OS, BIOS and motherboard, battery wear, drive health, temperatures.
- **Network**: saved WiFi (passwords only with `--reveal`), ping and latency (`speed`).
- **Security, apps, startup and drivers** commands.
- **Doctor engine**: health checks, 0-100 score and a fix hint for each problem; `lapforge health`.
- **Cleaner**: Quick and Full clean, browser cache, duplicate finder, SFC + DISM repair, before/after report and logs.
- **Reports**: HTML (dark theme), JSON and Markdown; private details hidden by default.
- **Config file** and a read-only update check that can be turned off.
- **Live monitor** and bottleneck finder.
- **Startup optimizer** with backup, restore point and Undo.
- **Upgrade advisor** (RAM slots / type / maximum, SSD slots).
- **Tests** (pytest, no Windows needed), packaging (`pip install .`), GitHub Actions CI and a single `.exe` build.

## Next

- Replace the sample SVG previews in the README with real screenshots, and publish the first GitHub Release with the `.exe`.
- More languages (the `language` setting already exists; only English today).
- Report comparison: show what changed between two saved reports.
- More cleaner locations (other browsers, popular app caches), always with preview and Undo.
- Optional light theme.

## Ideas (not promised)

- Scheduled health check with a summary file.
- Export of the live monitor to a CSV file.
- Winget-based "outdated apps" list (read-only).
- Signed `.exe` releases.

## Principles that will not change

- Only for your own system; nothing is sent over the internet except the optional, read-only update check.
- Preview first, confirm, and every tweak has an Undo.
- Personal files are never touched.
- Anything unavailable is shown as `N/A`, never a crash.

Have an idea? Open an issue on the [GitHub repository](https://github.com/BuildsByAliyan/lapforge/issues).
