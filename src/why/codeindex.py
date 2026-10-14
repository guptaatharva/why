"""A small static index of a repo: where functions live and who calls them.

Python is parsed with `ast`. JavaScript/TypeScript is indexed with a lightweight scanner
(declarations + brace matching), which is enough to resolve the functions a decision names
and to find their callers; it is not a full parser.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", ".why", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".tox", "site-packages",
             ".next", "coverage", "out"}


@dataclass
class Func:
    name: str
    qualname: str
    module: str  # dotted import path
    file: str  # repo-relative
    lineno: int
    end_lineno: int
    params: list[str]
    calls: set[str] = field(default_factory=set)  # bare names of functions called inside
    lang: str = "python"  # python | js
    exported: bool = True

    @property
    def ref(self) -> str:
        return f"{self.module}:{self.qualname}"


def _split_params(params: str) -> list[str]:
    """Split a parameter list on top-level commas (TypeScript types nest <>, {}, [], ())."""
    out, depth, cur = [], 0, ""
    for ch in params:
        if ch in "<{[(":
            depth += 1
        elif ch in ">}])":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out if x.strip()]


class CodeIndex:
    def __init__(self, root: Path, python_path: list[str] | None = None, exclude: list[str] | None = None,
                 language: str = "python"):
        self.root = root
        self.language = language
        self.python_path = [root / p for p in (python_path or ["."])]
        self.exclude = set(exclude or [])
        self.funcs: list[Func] = []
        self.identifiers: set[str] = set()  # function/class/const names and file stems, any language
        self._build()
        self._build_js()
        self._scan_js()

    def _module_name(self, path: Path) -> str | None:
        for base in sorted(self.python_path, key=lambda p: len(p.parts), reverse=True):
            try:
                rel = path.relative_to(base)
            except ValueError:
                continue
            parts = list(rel.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts and all(p.isidentifier() for p in parts):
                return ".".join(parts)
        return None

    def python_files(self) -> list[Path]:
        out = []
        for p in self.root.rglob("*.py"):
            rel = p.relative_to(self.root)
            if any(part in SKIP_DIRS or part in self.exclude for part in rel.parts):
                continue
            out.append(p)
        return sorted(out)

    def _build(self) -> None:
        for path in self.python_files():
            mod = self._module_name(path)
            if mod is None:
                continue
            try:
                tree = ast.parse(path.read_text(errors="replace"))
            except SyntaxError:
                continue
            rel = path.relative_to(self.root).as_posix()
            self._walk(tree, mod, rel, prefix="")

    def _walk(self, node: ast.AST, mod: str, rel: str, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}{child.name}"
                f = Func(
                    name=child.name,
                    qualname=q,
                    module=mod,
                    file=rel,
                    lineno=child.lineno,
                    end_lineno=getattr(child, "end_lineno", child.lineno),
                    params=[a.arg for a in child.args.args if a.arg not in ("self", "cls")],
                )
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Call):
                        fn = sub.func
                        if isinstance(fn, ast.Name):
                            f.calls.add(fn.id)
                        elif isinstance(fn, ast.Attribute):
                            f.calls.add(fn.attr)
                self.funcs.append(f)
            elif isinstance(child, ast.ClassDef):
                self._walk(child, mod, rel, prefix=f"{prefix}{child.name}.")

    JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
    JS_FUNC = re.compile(
        r"^[ \t]*(export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^>(]*>)?\s*\(([^)]*)\)"
        r"|^[ \t]*(export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?"
        r"(?:function\s*\*?\s*[\w$]*\s*\(([^)]*)\)|\(([^)]*)\)\s*(?::[^=]+?)?=>|([A-Za-z_$][\w$]*)\s*=>)",
        re.M,
    )
    JS_CALL = re.compile(r"(?<![\w$.])(?:await\s+)?([A-Za-z_$][\w$]*)\s*(?:<[^>()]*>)?\(|\.([A-Za-z_$][\w$]*)\s*\(")
    JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "typeof", "new", "await", "import",
                   "super", "constructor"}

    def js_files(self) -> list[Path]:
        out = []
        for p in self.root.rglob("*"):
            if p.suffix not in self.JS_EXT or not p.is_file() or p.name.endswith(".d.ts"):
                continue
            rel = p.relative_to(self.root)
            if any(part in SKIP_DIRS or part in self.exclude or part.startswith(".") for part in rel.parts):
                continue
            if re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", p.name):
                continue
            out.append(p)
        return sorted(out)

    @staticmethod
    def _body_end(text: str, start: int) -> int:
        """Index just past the function body starting at or after `start` (brace matching, strings skipped)."""
        i, n = start, len(text)
        # arrow function with an expression body: ends at the end of the statement
        brace = text.find("{", start)
        newline = text.find("\n", start)
        arrow = text.find("=>", start)
        if arrow != -1 and (brace == -1 or arrow < brace) and newline != -1:
            after = text[arrow + 2:].lstrip()
            if not after.startswith("{"):
                end = text.find(";\n", arrow)
                return n if end == -1 else end + 1
        if brace == -1:
            return n
        depth, i, quote = 0, brace, ""
        while i < n:
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
            elif ch in "\"'`":
                quote = ch
            elif text.startswith("//", i):
                nl = text.find("\n", i)
                i = n if nl == -1 else nl
                continue
            elif text.startswith("/*", i):
                close = text.find("*/", i + 2)
                i = n if close == -1 else close + 2
                continue
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return n

    def _build_js(self) -> None:
        for path in self.js_files():
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            rel = path.relative_to(self.root).as_posix()
            module = rel.rsplit(".", 1)[0]
            if module.endswith("/index"):
                module = module[: -len("/index")]
            # CommonJS: module.exports = { a, b: c }, exports.a = …, module.exports.a = …
            cjs: set[str] = set()
            for block in re.findall(r"module\.exports\s*=\s*\{([^}]*)\}", text):
                cjs |= {re.split(r"\s*:\s*", x.strip())[0] for x in block.split(",") if x.strip()}
            cjs |= set(re.findall(r"(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=", text))
            for m in self.JS_FUNC.finditer(text):
                if m.group(2):
                    name, exported, params = m.group(2), bool(m.group(1)), m.group(3) or ""
                else:
                    name, exported = m.group(5), bool(m.group(4))
                    params = m.group(6) or m.group(7) or m.group(8) or ""
                end = self._body_end(text, m.end())
                body = text[m.end():end]
                calls = {a or b for a, b in self.JS_CALL.findall(body)} - self.JS_KEYWORDS - {""}
                names = [re.split(r"[?:=\s]", x.strip().lstrip("."))[0] for x in _split_params(params)]
                self.funcs.append(Func(
                    name=name, qualname=name, module=module, file=rel,
                    lineno=text.count("\n", 0, m.start()) + 1, end_lineno=text.count("\n", 0, end) + 1,
                    params=[x for x in names if x and not x.startswith("{")] or names,
                    calls=calls, lang="js", exported=exported or name in cjs,
                ))

    JS_DEF = re.compile(r"\b(?:function|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)")

    def _scan_js(self) -> None:
        self.identifiers |= {f.name for f in self.funcs}
        for ext in ("*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs", "*.py"):
            for p in self.root.rglob(ext):
                rel = p.relative_to(self.root)
                if any(part in SKIP_DIRS or part in self.exclude or part.startswith(".") for part in rel.parts):
                    continue
                self.identifiers.add(p.stem)
                self.identifiers |= {part for part in rel.parts[:-1]}
                if p.suffix != ".py":
                    try:
                        self.identifiers |= set(self.JS_DEF.findall(p.read_text(errors="replace")))
                    except OSError:
                        continue

    def mentions(self, text: str) -> bool:
        """Does the text name something that exists in this repo (a function, type, file or folder)?"""
        tokens = set(re.findall(r"`([^`]+)`", text))
        tokens |= {t for t in re.findall(r"[A-Za-z_$][\w$./-]*", text)
                   if re.search(r"[A-Z].*[a-z]|[a-z][A-Z]|_|\.|/", t)}
        for t in tokens:
            for part in re.split(r"[./()\s:-]+", t):
                if len(part) >= 3 and part in self.identifiers:
                    return True
        return False

    # ---- queries -----------------------------------------------------------

    def find(self, symbol: str) -> list[Func]:
        """Resolve `charge`, `gateway.charge`, `Gateway.charge` or `pkg.mod:charge`."""
        s = symbol.strip().rstrip("()").replace(":", ".")
        last = s.split(".")[-1]
        hits = [f for f in self.funcs if f.name == last]
        if "." in s:
            exact = [f for f in hits if f"{f.module}.{f.qualname}".endswith(s) or f.qualname.endswith(s)]
            if exact:
                return exact
        return [f for f in hits if not f.name.startswith("test_")]

    def callers(self, func: Func) -> list[Func]:
        out = [f for f in self.funcs if func.name in f.calls and f is not func and not f.name.startswith("test_")
               and f.lang == func.lang]
        # Prefer importable entry points in other modules (JS mocks only intercept cross-module calls).
        return sorted(out, key=lambda f: (not f.exported, f.module == func.module, f.file))

    def at(self, file: str, line: int) -> Func | None:
        best = None
        for f in self.funcs:
            if f.file == file and f.lineno <= line <= f.end_lineno:
                if best is None or f.lineno >= best.lineno:
                    best = f
        return best

    def summary(self, limit: int = 300) -> str:
        return "\n".join(f"{f.ref}({', '.join(f.params)})  [{f.file}:{f.lineno}]" for f in self.funcs[:limit])
