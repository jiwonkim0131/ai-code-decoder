"""PythonAdapter: `ast` 정적 분석과 공식 문서 조회(BE-03, BE-05).

호출 해석 규칙은 스터디 레포 `agent/analyzer.py`의 `analyze_code`를 이식했다. 코드는
`ast.parse`로 구조만 읽고 실행하지 않는다. 레포의 주변 코드(surrounding_context) 입력은
이 앱에 없어 뺐고, 정의한 함수·클래스의 줄 범위(symbols)와 호출이 나온 줄 전체(lines)를 더했다.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field

from server.docs.documentation import DocumentationEvidence, lookup_documentation
from server.languages.base import (
    CallFact,
    Capabilities,
    CodeAnalysisError,
    CodeSyntaxError,
    ImportFact,
    StaticFacts,
    SymbolFact,
)
from server.models import MAX_CODE_CHARACTERS, AudienceLevel

SYNTAX_ERROR_MESSAGE = (
    "Python 코드만 분석할 수 있어요. Python 코드가 맞다면 이 줄의 문법을 확인해 주세요."
)

_LEVEL_HINTS: dict[str, str] = {
    "basic": (
        "설명 수준은 입문이다. 용어는 처음 나올 때 한 문장으로 풀어 쓰고, "
        "한 단계에 개념 하나만 다룬다."
    ),
    "work": (
        "설명 수준은 실무다. 용어는 그대로 쓰되 운영에서 생기는 일(지연, 실패, 재시도)과 "
        "연결해 설명한다."
    ),
    "deep": (
        "설명 수준은 심화다. 이벤트 루프·스케줄링·예외 전파 같은 내부 동작과 "
        "대안 API의 차이까지 다룬다."
    ),
}

# 계산·변환만 하는 모듈. 이 호출만 있는 코드는 운영 조건이 바뀌어도 다시 설계할 곳이 없다고 본다.
# builtins의 open도 여기 든다(설정 파일 한 번 읽는 정도로는 설계 응용 제약을 만들지 않는다).
# 모르는 모듈(서드파티, 사용자 패키지)은 외부 호출일 수 있으므로 운영 호출로 본다.
_COMPUTATION_MODULES = frozenset(
    {
        "base64",
        "bisect",
        "builtins",
        "cmath",
        "collections",
        "copy",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "hashlib",
        "heapq",
        "itertools",
        "json",
        "math",
        "operator",
        "pprint",
        "random",
        "re",
        "statistics",
        "string",
        "textwrap",
        "typing",
        "uuid",
    }
)

_PYTHON_HINTS = (
    "코드는 Python 3다. async/await, asyncio(gather, TaskGroup, wait_for), "
    "컨텍스트 매니저, 제너레이터처럼 Python에서 설계 결정이 갈리는 관용구를 우선 살핀다."
)


def _attribute_parts(node: ast.AST) -> list[str]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return list(reversed(parts))


def _parse(code: str) -> ast.Module:
    if len(code) > MAX_CODE_CHARACTERS:
        raise CodeAnalysisError(
            f"코드가 너무 길어요. {MAX_CODE_CHARACTERS:,}자 이하로 입력해 주세요."
        )
    try:
        return ast.parse(code)
    except SyntaxError as exc:
        raise CodeSyntaxError(SYNTAX_ERROR_MESSAGE, exc.lineno) from exc
    except ValueError as exc:  # 널 문자 등 파서가 받지 않는 입력
        raise CodeSyntaxError(SYNTAX_ERROR_MESSAGE, None) from exc
    except (RecursionError, MemoryError) as exc:
        raise CodeAnalysisError("코드 구조가 너무 깊어서 분석할 수 없어요.") from exc


def _collect_imports(
    tree: ast.Module,
) -> tuple[list[ImportFact], dict[str, tuple[str, str | None]]]:
    imports: list[ImportFact] = []
    imported_names: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                imported_names[local_name] = (alias.name, None)
                imports.append(
                    ImportFact(
                        module=alias.name, name=None, local_name=local_name, line=node.lineno
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name != "*":
                    imported_names[local_name] = (module, alias.name)
                imports.append(
                    ImportFact(
                        module=module, name=alias.name, local_name=local_name, line=node.lineno
                    )
                )
    imports.sort(key=lambda item: item.line)
    return imports, imported_names


def _collect_symbols(tree: ast.Module) -> list[SymbolFact]:
    symbols: list[SymbolFact] = []
    # 재귀 대신 스택으로 돌아 깊게 중첩된 정의에서도 RecursionError가 나지 않게 한다.
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                kind = (
                    "class"
                    if isinstance(child, ast.ClassDef)
                    else "async_function"
                    if isinstance(child, ast.AsyncFunctionDef)
                    else "function"
                )
                symbols.append(
                    SymbolFact(
                        name=name,
                        kind=kind,
                        line=child.lineno,
                        end_line=child.end_lineno or child.lineno,
                    )
                )
                stack.append((child, f"{name}."))
            else:
                stack.append((child, prefix))
    symbols.sort(key=lambda item: (item.line, item.name))
    return symbols


@dataclass
class _CallCollector:
    """레포 analyzer의 호출 해석 규칙. 같은 (name, module, symbol)은 하나로 묶는다."""

    code: str
    tree: ast.Module
    imported_names: dict[str, tuple[str, str | None]]
    calls: dict[tuple[str, str, str], CallFact] = field(default_factory=dict)
    syntax: set[str] = field(default_factory=set)

    def run(self) -> None:
        parent_by_node_id: dict[int, ast.AST] = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                parent_by_node_id[id(child)] = parent
        self.parent_by_node_id = parent_by_node_id
        self.variable_types = self._infer_path_variables()
        self.rebound_names = self._rebound_names()

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                self._visit_call(node)
            elif isinstance(node, ast.JoinedStr):
                self.syntax.add("f-string")
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                self.syntax.add("comprehension")
            elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
                self.syntax.add("async/await")
            elif isinstance(node, ast.Starred):
                self.syntax.add("unpacking with *")

    def _infer_path_variables(self) -> dict[str, tuple[str, str]]:
        # IMAGES_DIR = Path("images") 같은 대입을 보고, 나중의 IMAGES_DIR.glob(...)을
        # 코드 실행 없이 pathlib 문서로 이어 준다.
        variable_types: dict[str, tuple[str, str]] = {}
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            parts = _attribute_parts(value.func)
            if not parts:
                continue
            imported = self.imported_names.get(parts[0])
            if not imported:
                continue
            module, imported_symbol = imported
            if imported_symbol == "Path" or (not imported_symbol and parts[-1] == "Path"):
                destinations = node.targets if isinstance(node, ast.Assign) else [node.target]
                for destination in destinations:
                    if isinstance(destination, ast.Name):
                        variable_types[destination.id] = (module, "Path")
        return variable_types

    def _rebound_names(self) -> set[str]:
        # 어느 스코프에서든 다시 묶인 이름은 새로 추론하지 않는다(스코프별 타입 추론은 범위 밖).
        names: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
        return names

    def _add(self, node: ast.Call, name: str, kind: str, module: str, symbol: str) -> None:
        line = node.lineno
        key = (name, module, symbol)
        existing = self.calls.get(key)
        if existing is not None:
            # ast.walk는 줄 순서가 아니므로, 더 앞 줄의 호출이 나오면 그 호출식으로 바꾼다.
            is_earlier = line < existing.lines[0]
            if line not in existing.lines:
                existing.lines.append(line)
                existing.lines.sort()
            if not is_earlier:
                return
        parent = self.parent_by_node_id.get(id(node))
        parent_parts = _attribute_parts(parent.func) if isinstance(parent, ast.Call) else []
        self.calls[key] = CallFact(
            name=name,
            kind=kind,
            module=module,
            symbol=symbol,
            resolved=bool(module and symbol),
            expression=ast.get_source_segment(self.code, node) or name,
            lines=existing.lines if existing else [line],
            end_line=node.end_lineno or line,
            used_as_input_of=".".join(parent_parts) or None,
        )

    def _visit_call(self, node: ast.Call) -> None:
        # 알려진 Path 생성자의 바로 다음 메서드만 해석한다. 임의의 팩토리 호출은
        # 실행 없이 받는 쪽 타입을 알 수 없다.
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
            receiver = node.func.value.func
            constructor = (
                _attribute_parts(receiver)
                if isinstance(receiver, ast.Name)
                or (isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Name))
                else []
            )
            imported = self.imported_names.get(constructor[0]) if constructor else None
            known_path = (
                imported == ("pathlib", "Path")
                and len(constructor) == 1
                or (imported == ("pathlib", None) and constructor[1:] == ["Path"])
            )
            if known_path and constructor[0] not in self.rebound_names:
                name = ".".join([*constructor, node.func.attr])
                self._add(node, name, "method", "pathlib", f"Path.{node.func.attr}")
                return

        parts = _attribute_parts(node.func)
        if not parts:
            return
        name = ".".join(parts)
        root = parts[0]
        if root in self.imported_names:
            module, imported_symbol = self.imported_names[root]
            if imported_symbol:
                symbol = ".".join([imported_symbol, *parts[1:]])
            else:
                module_parts = module.split(".")
                consumed = len(module_parts) if parts[: len(module_parts)] == module_parts else 1
                symbol = ".".join(parts[consumed:]) or module.rsplit(".", 1)[-1]
            self._add(node, name, "method" if len(parts) > 1 else "function", module, symbol)
        elif root in self.variable_types and len(parts) > 1:
            module, type_name = self.variable_types[root]
            self._add(node, name, "method", module, ".".join([type_name, *parts[1:]]))
        elif callable(getattr(builtins, root, None)):
            self._add(node, name, "function" if len(parts) == 1 else "method", "builtins", name)
        elif len(parts) > 1 and parts[0][0:1].isupper():
            # Path.glob처럼 흔한 타입 메서드 호출은 모듈 없이 심볼만 남긴다.
            self._add(node, name, "method", "", ".".join(parts[1:]))
        else:
            self._add(node, name, "function", "", name)


class PythonAdapter:
    id = "python"
    capabilities = Capabilities(static_verification=True, official_docs=True)

    def extract_facts(self, code: str) -> StaticFacts:
        tree = _parse(code)
        try:
            imports, imported_names = _collect_imports(tree)
            collector = _CallCollector(code=code, tree=tree, imported_names=imported_names)
            collector.run()
            symbols = _collect_symbols(tree)
        except RecursionError as exc:
            raise CodeAnalysisError("코드 구조가 너무 깊어서 분석할 수 없어요.") from exc
        return StaticFacts(
            language=self.id,
            line_count=len(code.splitlines()),
            imports=imports,
            symbols=symbols,
            calls=sorted(collector.calls.values(), key=lambda call: (call.lines[0], call.name)),
            syntax=sorted(collector.syntax),
        )

    def lookup_docs(self, module: str, symbol: str) -> DocumentationEvidence:
        return lookup_documentation(module, symbol)

    def prompt_hints(self, level: AudienceLevel) -> str:
        return f"{_PYTHON_HINTS}\n{_LEVEL_HINTS[level]}"

    def operational_lines(self, facts: StaticFacts) -> set[int]:
        # 해석하지 못한 호출(파일 안 함수, 변수의 메서드)은 그 안·앞의 호출이 이미 잡히므로 뺀다.
        return {
            line
            for call in facts.calls
            if call.resolved and call.module not in _COMPUTATION_MODULES
            for line in call.lines
        }
