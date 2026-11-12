# Contributing to LapForge

Thanks for helping! LapForge is a small, safety-first tool, so a few simple rules keep it that way.

## Set up

```
git clone https://github.com/BuildsByAliyan/lapforge.git
cd lapforge
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
python -m pytest
```

The tests do not need Windows, so they also run on Linux and macOS.

## Rules

- **Safety first.** Anything that deletes or changes something must show a preview first, ask before acting, and (where possible) be undoable. Cleaning code is dry-run by default.
- **Never touch personal files** (Documents, Pictures, Videos, Desktop).
- **No telemetry.** Nothing about the user or their PC leaves the computer. The only network call is the optional read-only update check.
- **Never crash on missing data.** Show `N/A` instead of raising an error.
- **Add a test** for every bug you fix and every feature you add.
- Keep the code in the same style as the files around it.

## Pull requests

1. Fork the repository and create a branch.
2. Make your change and run `python -m pytest`.
3. Open a pull request and describe what changed and why.

## Reporting a security problem

Please do not open a public issue. See [SECURITY.md](SECURITY.md).
