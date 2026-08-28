"""Static regression guard for the GFH branding modules.

The frozen exe once crashed at startup with::

    File "header_manager.py", line 158, in add_copyright
    NameError: name 'tk' is not defined

because ``add_copyright`` used ``tk`` but missed the module's own lazy
``import tkinter as tk`` convention (the original monolith never called that
method, so the bug stayed hidden for years).  These tests parse every
branding module and fail if any function references ``tk`` / ``ttk`` that it
never binds and that is not bound at true module level.
"""
import ast
import builtins
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

BRANDING_MODULES = ["header_manager.py", "theme_manager.py", "logo_handler.py"]

_SCOPE_JUMPERS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _module_level_binds(tree: ast.Module) -> set:
    binds = set()
    for node in tree.body:  # top level ONLY — lazy imports inside methods are local
        if isinstance(node, ast.Import):
            binds |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            binds |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    binds.add(target.id)
    return binds


def _function_scope(fn: ast.FunctionDef) -> tuple:
    """Return (binds, refs) for the function's own scope, excluding nested scopes."""
    binds, refs = set(), set()
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPE_JUMPERS):
            continue  # nested scope — its imports/refs don't apply here
        if isinstance(node, ast.Import):
            binds |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            binds |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                binds.add(node.id)
            else:
                refs.add(node.id)
        stack.extend(ast.iter_child_nodes(node))
    args = fn.args
    params = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
    if args.vararg:
        params.add(args.vararg.arg)
    if args.kwarg:
        params.add(args.kwarg.arg)
    return binds | params, refs


def test_branding_functions_bind_tk_and_ttk():
    problems = []
    for mod_name in BRANDING_MODULES:
        path = REPO_ROOT / mod_name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=mod_name)
        module_binds = _module_level_binds(tree)
        for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)] + [
            n for cls in [c for c in tree.body if isinstance(c, ast.ClassDef)]
            for n in cls.body if isinstance(n, ast.FunctionDef)
        ]:
            binds, refs = _function_scope(fn)
            missing = refs - binds - module_binds - set(dir(builtins))
            bad = {"tk", "ttk"} & missing
            if bad:
                problems.append(f"{mod_name}:{fn.lineno} {fn.name}() uses unbound {sorted(bad)}")
    assert not problems, "Unbound tkinter names (startup crash risk):\n" + "\n".join(problems)


def test_add_copyright_binds_tk():
    """Direct check on the exact method that crashed the frozen exe."""
    tree = ast.parse((REPO_ROOT / "header_manager.py").read_text(encoding="utf-8"))
    add_copyright = [
        n
        for cls in [c for c in tree.body if isinstance(c, ast.ClassDef)]
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "add_copyright"
    ][0]
    local_imports = [
        alias.name
        for node in ast.walk(add_copyright)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert "tkinter" in local_imports, "add_copyright must lazy-import tkinter as tk"
