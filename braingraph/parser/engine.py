from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from braingraph.parser.types import ParsedFile, ParsedRelation, ParsedSymbol
from braingraph.parser.utils import estimate_tokens, read_text_lossy, sha256_text

try:
    from tree_sitter_language_pack import get_parser
except Exception:  # pragma: no cover - optional parser pack can vary by platform
    get_parser = None  # type: ignore[assignment]


class CodeParser:
    """Extracts symbols and relationships from supported code files."""

    def parse(self, path: Path, root: Path, language: str) -> ParsedFile:
        content = read_text_lossy(path)
        rel = path.relative_to(root).as_posix()
        parsed = ParsedFile(
            relative_path=rel,
            absolute_path=str(path),
            language=language,
            content=content,
            content_hash=sha256_text(content),
            size_bytes=path.stat().st_size,
            token_estimate=estimate_tokens(content),
        )
        if language == "python":
            self._parse_python(parsed)
        elif language in {"javascript", "javascriptreact", "typescript", "typescriptreact"}:
            self._parse_js_ts(parsed)
        elif language == "html":
            self._parse_html(parsed)
        elif language == "css":
            self._parse_css(parsed)
        elif language == "json":
            self._parse_json(parsed)
        self._touch_tree_sitter(language, content)
        return parsed

    def _touch_tree_sitter(self, language: str, content: str) -> None:
        if get_parser is None or not content.strip():
            return
        lang_name = {
            "javascriptreact": "javascript",
            "typescriptreact": "typescript",
        }.get(language, language)
        try:
            parser = get_parser(lang_name)
            parser.parse(content.encode("utf-8", errors="ignore"))
        except Exception:
            return

    def _parse_python(self, parsed: ParsedFile) -> None:
        try:
            tree = ast.parse(parsed.content)
        except SyntaxError as exc:
            parsed.warnings.append(f"python_syntax_error:{exc.lineno or 1}:{exc.msg}")
            return
        file_key = parsed.relative_path
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parsed.symbols.append(
                    ParsedSymbol(
                        name=node.name,
                        kind="function",
                        line_start=node.lineno,
                        line_end=getattr(node, "end_lineno", node.lineno),
                        signature=self._python_signature(node),
                    )
                )
            elif isinstance(node, ast.ClassDef):
                parsed.symbols.append(
                    ParsedSymbol(
                        name=node.name,
                        kind="class",
                        line_start=node.lineno,
                        line_end=getattr(node, "end_lineno", node.lineno),
                        signature=f"class {node.name}",
                    )
                )
                for base in node.bases:
                    target = self._ast_name(base)
                    if target:
                        parsed.relations.append(
                            ParsedRelation("class", node.name, "class", target, "extends", 0.8)
                        )
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    modules = [node.module or ""]
                for module in modules:
                    if module:
                        parsed.imports.append(module)
                        parsed.relations.append(
                            ParsedRelation("file", file_key, "module", module, "imports", 0.9)
                        )
            elif isinstance(node, ast.Call):
                target = self._ast_name(node.func)
                if target:
                    parsed.relations.append(
                        ParsedRelation("file", file_key, "function", target, "calls", 0.45)
                    )
        self._parse_python_routes(parsed)

    def _python_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = [arg.arg for arg in node.args.args]
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({', '.join(args)})"

    def _ast_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._ast_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Call):
            return self._ast_name(node.func)
        return None

    def _parse_python_routes(self, parsed: ParsedFile) -> None:
        route_re = re.compile(r"@(?:\w+\.)?(get|post|put|patch|delete|router\.(?:get|post|put|patch|delete))\(['\"]([^'\"]+)")
        for idx, line in enumerate(parsed.content.splitlines(), start=1):
            match = route_re.search(line)
            if not match:
                continue
            method = match.group(1).split(".")[-1].upper()
            path = match.group(2)
            name = f"{method} {path}"
            parsed.symbols.append(ParsedSymbol(name=name, kind="api", line_start=idx, line_end=idx))
            parsed.relations.append(
                ParsedRelation("file", parsed.relative_path, "api", name, "defines", 1.0)
            )

    def _parse_js_ts(self, parsed: ParsedFile) -> None:
        lines = parsed.content.splitlines()
        for idx, line in enumerate(lines, start=1):
            for pattern in (
                r"import\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]",
                r"require\(['\"]([^'\"]+)['\"]\)",
            ):
                for module in re.findall(pattern, line):
                    parsed.imports.append(module)
                    parsed.relations.append(
                        ParsedRelation("file", parsed.relative_path, "module", module, "imports", 0.9)
                    )
            export_match = re.search(r"export\s+(?:default\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)", line)
            if export_match:
                parsed.exports.append(export_match.group(1))
            for kind, pattern in (
                ("function", r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("),
                ("class", r"(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"),
                ("component", r"(?:const|let|var)\s+([A-Z][A-Za-z0-9_$]*)\s*=\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),
                ("variable", r"(?:const|let|var)\s+([a-zA-Z_$][\w$]*)\s*="),
            ):
                match = re.search(pattern, line)
                if match:
                    parsed.symbols.append(
                        ParsedSymbol(match.group(1), kind, idx, idx, line.strip()[:240])
                    )
            for exported in re.findall(r"module\.exports\s*=\s*\{([^}]+)\}", line):
                for name in re.findall(r"[A-Za-z_$][\w$]*", exported):
                    parsed.symbols.append(ParsedSymbol(name, "variable", idx, idx, line.strip()[:240]))
            for method, url in re.findall(r"(?:fetch|axios\.(get|post|put|patch|delete))\(\s*['\"]([^'\"]+)['\"]", line):
                api = f"{(method or 'GET').upper()} {url}"
                parsed.symbols.append(ParsedSymbol(api, "api", idx, idx))
                parsed.relations.append(
                    ParsedRelation("file", parsed.relative_path, "api", api, "fetches", 0.8)
                )
            for route in re.findall(r"<Route[^>]+path=['\"]([^'\"]+)['\"]", line):
                parsed.symbols.append(ParsedSymbol(route, "route", idx, idx))
                parsed.relations.append(
                    ParsedRelation("file", parsed.relative_path, "route", route, "defines", 0.85)
                )
        self._parse_jsx_renders(parsed)

    def _parse_jsx_renders(self, parsed: ParsedFile) -> None:
        for component in set(re.findall(r"<([A-Z][A-Za-z0-9_]*)\b", parsed.content)):
            parsed.relations.append(
                ParsedRelation("file", parsed.relative_path, "component", component, "renders", 0.55)
            )

    def _parse_html(self, parsed: ParsedFile) -> None:
        title = re.search(r"<title>(.*?)</title>", parsed.content, flags=re.I | re.S)
        if title:
            parsed.symbols.append(ParsedSymbol(title.group(1).strip(), "component", 1, 1))
        for src in re.findall(r"(?:src|href)=['\"]([^'\"]+)['\"]", parsed.content):
            parsed.relations.append(
                ParsedRelation("file", parsed.relative_path, "asset", src, "depends_on", 0.7)
            )

    def _parse_css(self, parsed: ParsedFile) -> None:
        for idx, line in enumerate(parsed.content.splitlines(), start=1):
            for selector in re.findall(r"([.#][A-Za-z0-9_-]+)\s*\{", line):
                parsed.symbols.append(ParsedSymbol(selector, "selector", idx, idx))

    def _parse_json(self, parsed: ParsedFile) -> None:
        try:
            data = json.loads(parsed.content)
        except json.JSONDecodeError as exc:
            parsed.warnings.append(f"json_decode_error:{exc.lineno}:{exc.msg}")
            return
        if isinstance(data, dict):
            for key in ("scripts", "dependencies", "devDependencies"):
                if key in data:
                    parsed.symbols.append(ParsedSymbol(key, "config", 1, 1))
