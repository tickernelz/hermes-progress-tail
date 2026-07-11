"""Synthetic contracts for C12's authoritative structural rails."""

import subprocess
import sys
from pathlib import Path

import scripts.check_structure as checker

ROOT = Path(__file__).parents[1]
REQUIRED = (
    "renderer_size_violations",
    "session_context_field_violations",
    "class_mutation_violations",
)


def _rails():
    assert all(hasattr(checker, name) for name in REQUIRED), "final structure rails are missing"
    return tuple(getattr(checker, name) for name in REQUIRED)


def _repo(tmp_path: Path, files: dict[str, str | bytes]):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, bytes):
            path.write_bytes(source)
        else:
            path.write_text(source)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return checker.tracked_text_files(tmp_path)


def test_renderer_399_passes_and_400_fails(tmp_path):
    renderer, _, _ = _rails()
    path = "hermes_progress_tail/rendering/renderer.py"
    paths = _repo(tmp_path, {path: "x\n" * 399})
    assert renderer(paths, tmp_path) == ()
    (tmp_path / path).write_text("x\n" * 400)
    assert renderer(paths, tmp_path)[0].count == 400


def test_session_fields_count_only_direct_annotations(tmp_path):
    _, fields, _ = _rails()
    prefix = "class Base:\n    inherited: int\nclass SessionContext(Base):\n"
    source = (
        prefix
        + "".join(f"    f{i}: int\n" for i in range(30))
        + "    def f(self):\n        local: int\n"
    )
    paths = _repo(tmp_path, {"hermes_progress_tail/models/state.py": source})
    assert fields(paths, tmp_path) == ()
    path = tmp_path / "hermes_progress_tail/models/state.py"
    path.write_text(source.replace("    def f", "    extra: int\n    def f"))
    assert fields(paths, tmp_path)[0].count == 31


def test_post_definition_mutations_rejected_but_class_and_instance_work_pass(tmp_path):
    _, _, mutations = _rails()
    source = """ProgressRenderer.before_definition = 0
class ProgressRenderer:
    value = 1
    def f(self):
        self.settings = 1
class DelegateProgressRenderer: pass
ProgressRenderer.direct = 1
DelegateProgressRenderer.note: int = 2
setattr(ProgressRenderer, 'x', 1)
exec('DelegateProgressRenderer.y = 2')
def assemble():
    if True:
        setattr(DelegateProgressRenderer, 'z', 3)
if True:
    ProgressRenderer.conditional = 4
    DelegateProgressRenderer.total += 1
    assemble()
def outer():
    def inner():
        ProgressRenderer.nested = 5
    inner()
outer()
exec('print(ProgressRenderer.value)')
"""
    paths = _repo(tmp_path, {"hermes_progress_tail/rendering/a.py": source})
    result = mutations(paths, tmp_path)
    assert [(x.target, x.operation) for x in result] == [
        ("ProgressRenderer", "assignment"),
        ("DelegateProgressRenderer", "assignment"),
        ("ProgressRenderer", "setattr"),
        ("DelegateProgressRenderer", "exec"),
        ("ProgressRenderer", "assignment"),
        ("DelegateProgressRenderer", "assignment"),
        ("DelegateProgressRenderer", "setattr"),
        ("ProgressRenderer", "assignment"),
    ]


def test_rendering_rejects_all_runtime_import_forms_and_lazy_imports(tmp_path):
    _rails()
    source = """import hermes_progress_tail.runtime as r
from ..runtime import state as s
def lazy():
    from hermes_progress_tail.runtime.plugin import register
"""
    paths = _repo(tmp_path, {"hermes_progress_tail/rendering/a.py": source})
    result = checker.dependency_violations(paths, tmp_path)
    assert [x.line for x in result] == [1, 2, 4]


def test_existing_composition_root_dependency_boundary_remains(tmp_path):
    _rails()
    paths = _repo(
        tmp_path,
        {
            "hermes_progress_tail/hooks/a.py": "from ..runtime import plugin\n",
            "hermes_progress_tail/runtime/a.py": "from .plugin import register\n",
            "hermes_progress_tail/runtime/plugin.py": "from . import container\n",
        },
    )
    result = checker.dependency_violations(paths, tmp_path)
    assert [(item.path.relative_to(tmp_path).as_posix(), item.line) for item in result] == [
        ("hermes_progress_tail/hooks/a.py", 1),
        ("hermes_progress_tail/runtime/a.py", 1),
    ]


def test_cli_reports_all_final_violation_kinds_deterministically(tmp_path):
    _rails()
    renderer = "hermes_progress_tail/rendering/renderer.py"
    state = "hermes_progress_tail/models/state.py"
    mutation = "hermes_progress_tail/rendering/a.py"
    _repo(
        tmp_path,
        {
            "large.txt": "x\n" * 601,
            renderer: "pass\n" * 400,
            state: "class SessionContext:\n" + "".join(f"    f{i}: int\n" for i in range(31)),
            mutation: "from ..runtime import commands\n"
            "class ProgressRenderer: pass\n"
            "ProgressRenderer.x = 1\n",
        },
    )
    script = ROOT / "scripts/check_structure.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "large.txt: 601 lines (limit 600)",
        f"{renderer}: 400 lines (renderer limit 399)",
        f"{state}:1: SessionContext has 31 direct fields (limit 30)",
        f"{mutation}:3: post-definition assignment mutates ProgressRenderer",
        f"{mutation}:1: forbidden dependency on hermes_progress_tail.runtime",
    ]
    assert result.stderr == ""


def test_live_repository_measurements_are_exact():
    renderer, fields, mutations = _rails()
    paths = checker.tracked_text_files(ROOT)
    renderer_path = ROOT / "hermes_progress_tail/rendering/renderer.py"
    assert len(renderer_path.read_bytes().splitlines()) < 400
    assert renderer(paths, ROOT) == ()
    assert fields(paths, ROOT) == ()
    assert mutations(paths, ROOT) == ()
    state = ROOT / "hermes_progress_tail/models/state.py"
    import ast

    tree = ast.parse(state.read_text())
    context = next(
        x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "SessionContext"
    )
    assert sum(isinstance(x, ast.AnnAssign) for x in context.body) == 21
    assert checker.repository_violations(ROOT) == ()
