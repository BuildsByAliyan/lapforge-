"""Every source file must compile without warnings (a bad escape such as "\\R" shows a SyntaxWarning to users)."""

import pathlib
import warnings

import lapforge

ROOT = pathlib.Path(lapforge.__file__).parent


def test_all_modules_compile_without_warnings():
    problems = []
    for path in sorted(ROOT.rglob("*.py")):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        problems += [f"{path.name}: {w.message}" for w in caught]
    assert not problems, problems
