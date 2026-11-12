# Security Policy

LapForge reads information about the computer it runs on and can, with your confirmation,
clean junk files and change startup entries. Because it touches system settings, we take
safety and privacy seriously.

## Intended use

LapForge is for **your own system only**. Do not use it to read WiFi passwords, serial
numbers or other details of a computer that you do not own or have permission to inspect.

## Privacy promises

- **No telemetry.** LapForge does not send any information about you or your PC anywhere.
- **One optional network request.** The update check makes a single read-only request to
  GitHub's public releases page and sends nothing about you. Disable it with
  `lapforge config set check_updates off`.
- **The speed test** only pings public servers (and times a DNS lookup) when you run
  `lapforge speed`.
- **WiFi passwords** are requested from Windows only with `--reveal`, only for the PC
  LapForge runs on, and are never saved to disk or written to logs.
- **Serial number, MAC and IP addresses** are hidden by default on screen and in reports.
  Reports created with `--show-private` contain them, so share those files with care.
- **Reports and logs stay local.** They are saved on your computer. The `reports/` folder
  is excluded from Git by default.

## Safety rules for changes

- Cleaning and tweaks show a preview first and ask for confirmation. Dry-run is available.
- Personal folders (Documents, Pictures, Videos, Desktop) are never cleaned.
- Browser cleaning never touches cookies, passwords or history.
- Duplicate files are reported, never deleted automatically.
- Startup changes save a backup first, can create a System Restore point, and can be
  reversed with `lapforge startup --undo`.
- Anything LapForge cannot read is shown as `N/A` instead of guessing.

## Reporting a vulnerability

If you find a security problem, please **do not open a public issue**.

1. Use GitHub's private reporting: open the repository's **Security** tab and choose
   **Report a vulnerability**.
2. Include the LapForge version (`lapforge --version`), your Windows version, the steps to
   reproduce, and what you expected to happen.

You can expect a first reply within about 7 days. Please allow reasonable time for a fix
before sharing the details publicly.

## Supported versions

Only the latest release receives security fixes.

## Running as Administrator

Some features (battery wear, WiFi passwords, restore points, SFC/DISM) need an
Administrator terminal. Only run LapForge as Administrator from a copy you trust: download
it from the official repository or build it yourself with `build_exe.bat`.
