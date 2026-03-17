from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from tree_sitter import Language, Parser, Node
from tree_sitter_languages import get_language

from .repo_scanner import SupportedLanguage


@dataclass
class SymbolTable:
    functions: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    vars: List[str] = field(default_factory=list)


def _make_parser(language_name: str) -> Parser:
    try:
        lang: Language = get_language(language_name)
    except TypeError as exc:
        raise RuntimeError(
            "tree_sitter / tree_sitter_languages version mismatch. "
            "Reinstall pinned deps: `pip install -r requirements.txt`."
        ) from exc
    parser = Parser()
    parser.set_language(lang)
    return parser


_PARSERS: Dict[str, Parser] = {}


def _get_parser(language_name: str) -> Parser:
    parser = _PARSERS.get(language_name)
    if parser is None:
        parser = _make_parser(language_name)
        _PARSERS[language_name] = parser
    return parser


def _parse_with_parser(parser: Parser, code: str) -> Node:
    tree = parser.parse(code.encode("utf8"))
    return tree.root_node


def parse_file(path: Path, language: SupportedLanguage) -> Node:
    code = path.read_text(encoding="utf8")
    if language == "javascript":
        try:
            return _parse_with_parser(_get_parser("javascript"), code)
        except ValueError:
            # Many JS repos use JSX in .js files; fall back to TSX grammar.
            return _parse_with_parser(_get_parser("tsx"), code)
    if language == "typescript":
        try:
            return _parse_with_parser(_get_parser("typescript"), code)
        except ValueError:
            return _parse_with_parser(_get_parser("tsx"), code)
    if language == "python":
        return _parse_with_parser(_get_parser("python"), code)
    raise ValueError(f"Unsupported language: {language}")


def _collect_imports_js(root: Node, code: bytes) -> List[str]:
    modules: List[str] = []
    for child in root.children:
        if child.type == "import_statement":
            # import ... from 'module'
            for grand in child.children:
                if grand.type == "string":
                    text = code[grand.start_byte + 1 : grand.end_byte - 1].decode("utf8")
                    modules.append(text)
        elif child.type == "lexical_declaration":
            # const x = require('module')
            for grand in child.named_children:
                if grand.type == "variable_declarator":
                    init = next((c for c in grand.named_children if c is not None and c.type == "call_expression"), None)
                    if init is None:
                        continue
                    callee = next((c for c in init.named_children if c.type == "identifier"), None)
                    if callee is None:
                        continue
                    name = code[callee.start_byte:callee.end_byte].decode("utf8")
                    if name != "require":
                        continue
                    args = [c for c in init.named_children if c.type == "arguments"]
                    if not args:
                        continue
                    for arg_child in args[0].children:
                        if arg_child.type == "string":
                            text = code[arg_child.start_byte + 1 : arg_child.end_byte - 1].decode("utf8")
                            modules.append(text)
    return modules


def _collect_symbols_js(root: Node, code: bytes) -> SymbolTable:
    symbols = SymbolTable()

    for child in root.children:
        if child.type == "function_declaration":
            # function foo() {}
            name_node = next((c for c in child.children if c.type == "identifier"), None)
            if name_node is not None:
                name = code[name_node.start_byte:name_node.end_byte].decode("utf8")
                symbols.functions.append(name)
                if name and name[0].isupper():
                    symbols.components.append(name)
        elif child.type == "class_declaration":
            name_node = next((c for c in child.children if c.type == "type_identifier"), None)
            if name_node is None:
                name_node = next((c for c in child.children if c.type == "identifier"), None)
            if name_node is not None:
                name = code[name_node.start_byte:name_node.end_byte].decode("utf8")
                symbols.classes.append(name)
        elif child.type in ("lexical_declaration", "variable_declaration"):
            # const Header = () => <button>...</button>
            for grand in child.named_children:
                if grand.type != "variable_declarator":
                    continue
                id_node = next((c for c in grand.children if c.type == "identifier"), None)
                if id_node is None:
                    continue
                name = code[id_node.start_byte:id_node.end_byte].decode("utf8")
                symbols.vars.append(name)
                if name and name[0].isupper():
                    symbols.components.append(name)

    return symbols


def _collect_imports_py(root: Node, code: bytes) -> List[str]:
    modules: List[str] = []
    for child in root.children:
        if child.type == "import_statement":
            # import os, sys
            names = [
                code[n.start_byte:n.end_byte].decode("utf8")
                for n in child.named_children
                if n.type == "dotted_name"
            ]
            modules.extend(names)
        elif child.type == "import_from_statement":
            # from x import y
            module_node = next((c for c in child.named_children if c.type == "dotted_name"), None)
            if module_node is not None:
                module_name = code[module_node.start_byte:module_node.end_byte].decode("utf8")
                modules.append(module_name)
    return modules


def _collect_symbols_py(root: Node, code: bytes) -> SymbolTable:
    symbols = SymbolTable()

    for child in root.children:
        if child.type == "function_definition":
            name_node = next((c for c in child.children if c.type == "identifier"), None)
            if name_node is not None:
                name = code[name_node.start_byte:name_node.end_byte].decode("utf8")
                symbols.functions.append(name)
        elif child.type == "class_definition":
            name_node = next((c for c in child.children if c.type == "identifier"), None)
            if name_node is not None:
                name = code[name_node.start_byte:name_node.end_byte].decode("utf8")
                symbols.classes.append(name)
        elif child.type == "expression_statement":
            # top-level assignments: x = ...
            assign = next((c for c in child.named_children if c.type == "assignment"), None)
            if assign is None:
                continue
            target = next((c for c in assign.named_children if c.type == "identifier"), None)
            if target is not None:
                name = code[target.start_byte:target.end_byte].decode("utf8")
                symbols.vars.append(name)

    return symbols


def extract_symbols(path: Path, language: SupportedLanguage) -> Dict[str, object]:
    """
    Parse a file and return its symbol table and imports.
    """
    code_text = path.read_text(encoding="utf8")
    code_bytes = code_text.encode("utf8")

    if language in ("javascript", "typescript"):
        root = parse_file(path, language)
        imports = _collect_imports_js(root, code_bytes)
        symbols = _collect_symbols_js(root, code_bytes)
    elif language == "python":
        root = parse_file(path, language)
        imports = _collect_imports_py(root, code_bytes)
        symbols = _collect_symbols_py(root, code_bytes)
    else:
        raise ValueError(f"Unsupported language: {language}")

    return {
        "symbols": {
            "functions": symbols.functions,
            "classes": symbols.classes,
            "components": symbols.components or None,
            "vars": symbols.vars or None,
        },
        "imports": imports or None,
    }

