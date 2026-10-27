"""Effect scripts: a minimal bash subset that chains HATG ISA calls (decision_4adcc1ff2f2c).

An Effect's ``script`` is parsed into a statement tree, checked against the
instruction catalog when the Lineage is compiled, and interpreted when the
Effect is activated.  Every command is one instruction call; ``if`` and
``for`` connect the calls; ``output``, ``exit`` and ``fail`` end or shape the
result.  The interpreter never runs programs, touches files or reads clocks:
it hands each call to the ``execute_call`` callback supplied by the Effect
runtime, which owns idempotency keys, the resume journal and routing.

Values follow the Design:

* ``name=value`` expands to text; a value made of exactly one variable
  reference keeps that value's type (number, list or object).
* ``name:=JSON`` gives a JSON value; ``${…}`` placeholders outside JSON
  strings are replaced by the JSON encoding of the referenced value.
* Missing fields expand to an empty value; non-text values inside text
  expand as compact JSON (sorted keys, the same spelling ``concat`` used).
* ``${path:offset:length}`` (or ``${path:offset}``) keeps that part of the
  value's text, counted in characters, as bash does.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

MAX_SCRIPT_CHARS = 20_000
MAX_CALLS = 32
RESERVED_VARIABLES = ("arguments", "event", "packet", "endpoint")
KEYWORDS = frozenset({"if", "then", "elif", "else", "fi", "for", "in", "do", "done"})
UNSUPPORTED_WORDS = frozenset({"while", "until", "case", "esac", "function", "select", "eval", "source"})
BUILTINS = frozenset({"output", "exit", "fail"})
INSTRUCTION_RE = re.compile(r"[A-Z][A-Za-z0-9]{1,79}")
VARIABLE_RE = re.compile(r"[a-z_][a-z0-9_]{0,63}")
ARGUMENT_RE = re.compile(r"[a-z_][a-z0-9_]{0,79}")
PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-]{1,120}")
ASSIGNMENT_PREFIX_RE = re.compile(r"([a-z_][a-z0-9_]*)(:?=)")
INTEGER_OPERATORS = {"-eq", "-ne", "-lt", "-le", "-gt", "-ge"}
TEXT_OPERATORS = {"=", "==", "!="}
UNARY_OPERATORS = {"-z", "-n"}
TEMPLATE_ESCAPES = {"\\": "\\\\", '"': '\\"', "$": "\\$", "`": "\\`"}


class ScriptSyntaxError(ValueError):
    """The script cannot be parsed; ``line`` is 1-based."""

    def __init__(self, message: str, line: int, code: str = "effect.script_syntax") -> None:
        super().__init__(message)
        self.line = line
        self.code = code


class ScriptFailure(Exception):
    """The script ended as failed while running."""

    def __init__(self, code: str, message: str, *, line: int = 0, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.line = line
        self.retryable = retryable


class _Exit(Exception):
    pass


# -- syntax tree -----------------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    path: tuple[str, ...]
    # ``${path:offset:length}`` keeps that part of the value's text.
    span: tuple[int, int | None] | None = None


@dataclass(frozen=True)
class JsonValue:
    """JSON text with ``${…}`` placeholders outside strings."""

    segments: tuple[str | Ref, ...]
    line: int


@dataclass(frozen=True)
class Word:
    """One shell word: literal text, variable references, or an instruction call."""

    parts: tuple[str | Ref, ...]
    quoted: bool
    line: int
    json: JsonValue | None = None
    call: "Call | None" = None
    offset: int = 0

    @property
    def text(self) -> str | None:
        """The literal text when the word has no expansion."""
        if self.json is not None or self.call is not None:
            return None
        if all(isinstance(part, str) for part in self.parts):
            return "".join(self.parts)  # type: ignore[arg-type]
        return None


@dataclass(frozen=True)
class Argument:
    name: str
    value: Word | JsonValue | "Call"
    line: int


@dataclass(frozen=True)
class Call:
    offset: int
    instruction: str
    arguments: tuple[Argument, ...]
    line: int


@dataclass(frozen=True)
class Assign:
    name: str
    value: Word | JsonValue | Call
    line: int


@dataclass(frozen=True)
class Output:
    arguments: tuple[Argument, ...]
    line: int


@dataclass(frozen=True)
class ExitStatement:
    line: int


@dataclass(frozen=True)
class Fail:
    reason: Word | None
    line: int


@dataclass(frozen=True)
class Test:
    operator: str
    operands: tuple[Word, ...]
    negate: bool
    line: int


@dataclass(frozen=True)
class CallCondition:
    target: Call | Assign
    line: int


@dataclass(frozen=True)
class Not:
    condition: Any
    line: int


@dataclass(frozen=True)
class AndOr:
    operator: str
    left: Any
    right: Any
    line: int


@dataclass(frozen=True)
class If:
    branches: tuple[tuple[Any, tuple[Any, ...]], ...]
    otherwise: tuple[Any, ...]
    line: int


@dataclass(frozen=True)
class For:
    name: str
    items: tuple[Word, ...]
    body: tuple[Any, ...]
    line: int


@dataclass(frozen=True)
class Script:
    statements: tuple[Any, ...]
    calls: tuple[Call, ...]  # in source order

    def index_of(self, call: Call) -> int:
        """1-based position of a call in source order."""
        return self.calls.index(call) + 1


# -- lexer --------------------------------------------------------------------------


@dataclass
class _Token:
    kind: str  # word | sep | and | or
    line: int
    word: Word | None = None


_OPERATOR_CHARS = set(";&|<>()`\n")


class _Lexer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.line = 1
        self.calls: list[Call] = []

    # helpers
    def peek(self, offset: int = 0) -> str:
        index = self.pos + offset
        return self.text[index] if index < len(self.text) else ""

    def advance(self) -> str:
        char = self.text[self.pos]
        self.pos += 1
        if char == "\n":
            self.line += 1
        return char

    def error(self, message: str, code: str = "effect.script_syntax") -> ScriptSyntaxError:
        return ScriptSyntaxError(message, self.line, code)

    def tokens(self, *, until_paren: bool = False) -> list[_Token]:
        result: list[_Token] = []
        while self.pos < len(self.text):
            char = self.peek()
            if char in " \t\r":
                self.advance()
                continue
            if char == "\\" and self.peek(1) == "\n":
                self.advance()
                self.advance()
                continue
            if char == "#":
                while self.pos < len(self.text) and self.peek() != "\n":
                    self.advance()
                continue
            if char == "\n" or char == ";":
                if char == ";" and self.peek(1) == ";":
                    raise self.error("不支持 ;;", "effect.script_unsupported")
                line = self.line
                self.advance()
                result.append(_Token("sep", line))
                continue
            if char == "&":
                if self.peek(1) == "&":
                    line = self.line
                    self.advance()
                    self.advance()
                    result.append(_Token("and", line))
                    continue
                raise self.error("不支持后台执行 &", "effect.script_unsupported")
            if char == "|":
                if self.peek(1) == "|":
                    line = self.line
                    self.advance()
                    self.advance()
                    result.append(_Token("or", line))
                    continue
                raise self.error("不支持管道 |", "effect.script_unsupported")
            if char == ")":
                if until_paren:
                    self.advance()
                    return result
                raise self.error("多余的 )")
            if char in "<>":
                raise self.error("不支持重定向", "effect.script_unsupported")
            if char == "(":
                raise self.error("不支持子 shell 或分组 ( )", "effect.script_unsupported")
            if char == "`":
                raise self.error("不支持反引号命令替换", "effect.script_unsupported")
            line = self.line
            result.append(_Token("word", line, self.word()))
        if until_paren:
            raise self.error("$( 缺少对应的 )")
        return result

    def word(self) -> Word:
        line = self.line
        parts: list[str | Ref] = []
        literal: list[str] = []
        quoted = False
        start = self.pos

        def flush() -> None:
            if literal:
                parts.append("".join(literal))
                literal.clear()

        while self.pos < len(self.text):
            char = self.peek()
            if char in " \t\r" or char in _OPERATOR_CHARS:
                break
            consumed = self.text[start:self.pos]
            if char == ":" and self.peek(1) == "=" and not parts and not quoted and ARGUMENT_RE.fullmatch(consumed):
                self.advance()
                self.advance()
                value = self.json_value()
                return Word((consumed + ":=",), False, line, json=value, offset=start)
            if char == "$" and self.peek(1) == "(":
                prefix = "".join(literal)
                if parts or quoted or not ASSIGNMENT_PREFIX_RE.fullmatch(prefix) or prefix.endswith(":="):
                    raise self.error("指令调用只能写成 变量=$(指令名 …)，先保存结果再使用")
                self.advance()
                self.advance()
                call = self.substitution()
                if self.pos < len(self.text) and self.peek() not in " \t\r" and self.peek() not in _OPERATOR_CHARS:
                    raise self.error("$( … ) 之后不能紧跟其他文字")
                return Word((prefix,), False, line, call=call, offset=start)
            if char == "'":
                quoted = True
                self.advance()
                end = self.text.find("'", self.pos)
                if end < 0:
                    raise self.error("单引号没有闭合")
                while self.pos < end:
                    literal.append(self.advance())
                self.advance()
                continue
            if char == '"':
                quoted = True
                self.advance()
                self.double_quoted(parts, literal, flush)
                continue
            if char == "\\":
                self.advance()
                if self.pos < len(self.text):
                    literal.append(self.advance())
                continue
            if char == "$":
                ref = self.reference()
                if ref is None:
                    literal.append(self.advance())
                else:
                    flush()
                    parts.append(ref)
                continue
            literal.append(self.advance())
        flush()
        if quoted and not parts:
            parts.append("")
        return Word(tuple(parts), quoted, line, offset=start)

    def double_quoted(self, parts: list[str | Ref], literal: list[str], flush: Callable[[], None]) -> None:
        while True:
            if self.pos >= len(self.text):
                raise self.error("双引号没有闭合")
            char = self.peek()
            if char == '"':
                self.advance()
                if not parts and not literal:
                    literal.append("")
                return
            if char == "\\":
                following = self.peek(1)
                if following in '"\\$`':
                    self.advance()
                    literal.append(self.advance())
                    continue
                if following == "\n":
                    self.advance()
                    self.advance()
                    continue
                literal.append(self.advance())
                continue
            if char == "`":
                raise self.error("不支持反引号命令替换", "effect.script_unsupported")
            if char == "$":
                if self.peek(1) == "(":
                    raise self.error("双引号中不能调用指令；先用 变量=$(…) 保存结果", "effect.script_unsupported")
                ref = self.reference()
                if ref is None:
                    literal.append(self.advance())
                else:
                    flush()
                    parts.append(ref)
                continue
            literal.append(self.advance())

    def reference(self) -> Ref | None:
        """Parse ``$name`` or ``${path}`` at the cursor; ``None`` for a literal ``$``."""

        following = self.peek(1)
        if following == "{":
            end = self.text.find("}", self.pos + 2)
            if end < 0:
                raise self.error("${ 没有闭合")
            body = self.text[self.pos + 2:end]
            if body.startswith("(") or body.startswith("#") or any(mark in body for mark in (":-", ":=", ":?", ":+", "/", "%", "#", " ")):
                raise self.error(f"不支持的变量展开 ${{{body}}}", "effect.script_unsupported")
            span = None
            if ":" in body:
                body, _, bounds = body.partition(":")
                numbers = bounds.split(":")
                if len(numbers) > 2 or not all(item.isdigit() for item in numbers):
                    raise self.error(f"截取只接受 ${{名称:起点:长度}}，起点与长度是非负整数：${{{body}:{bounds}}}")
                span = (int(numbers[0]), int(numbers[1]) if len(numbers) == 2 else None)
            segments = body.split(".") if body else []
            if not segments or not VARIABLE_RE.fullmatch(segments[0]) or not all(PATH_SEGMENT_RE.fullmatch(item) for item in segments[1:]):
                raise self.error(f"变量引用无效：${{{body}}}")
            while self.pos <= end:
                self.advance()
            return Ref(tuple(segments), span)
        if following == "(" and self.peek(2) == "(":
            raise self.error("不支持算术展开 $(( ))", "effect.script_unsupported")
        match = VARIABLE_RE.match(self.text, self.pos + 1)
        if not match or not match.group(0):
            return None
        self.advance()
        name = match.group(0)
        for _ in name:
            self.advance()
        return Ref((name,))

    def json_value(self) -> JsonValue:
        line = self.line
        if self.peek() == "'":
            self.advance()
            end = self.text.find("'", self.pos)
            if end < 0:
                raise self.error("单引号没有闭合")
            raw = self.text[self.pos:end]
            while self.pos <= end:
                self.advance()
            return JsonValue(tuple(_json_segments(raw, line)), line)
        start = self.pos
        depth = 0
        in_string = False
        while self.pos < len(self.text):
            char = self.peek()
            if in_string:
                if char == "\\":
                    self.advance()
                    if self.pos < len(self.text):
                        self.advance()
                    continue
                if char == '"':
                    in_string = False
                self.advance()
                continue
            if char == '"':
                in_string = True
                self.advance()
                continue
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
                if depth < 0:
                    raise self.error("JSON 括号不配对")
            elif depth == 0 and (char in " \t\r" or char in ";\n&|)"):
                break
            elif char == "\\" and self.peek(1) == "\n":
                raise self.error("JSON 值中不能续行；跨行的 JSON 请写在单引号中")
            self.advance()
        if in_string:
            raise self.error("JSON 字符串没有闭合")
        if depth != 0:
            raise self.error("JSON 括号不配对")
        raw = self.text[start:self.pos]
        if not raw:
            raise self.error(":= 之后缺少 JSON 值")
        return JsonValue(tuple(_json_segments(raw, line)), line)

    def substitution(self) -> Call:
        line = self.line
        tokens = self.tokens(until_paren=True)
        words = [token.word for token in tokens if token.kind == "word"]
        if any(token.kind != "word" for token in tokens) or not words:
            raise ScriptSyntaxError("$( … ) 中只能是一次指令调用", line)
        return self.make_call(words, line)

    def make_call(self, words: list[Word | None], line: int) -> Call:
        head = words[0]
        name = head.text if head is not None else None
        if not name or not INSTRUCTION_RE.fullmatch(name):
            raise ScriptSyntaxError(f"指令名无效：{name or '（含展开）'}", line)
        arguments = _arguments(words[1:], line, what=f"{name} 的参数")
        call = Call(head.offset if head is not None else 0, name, arguments, line)
        self.calls.append(call)
        return call


def _json_segments(raw: str, line: int) -> list[str | Ref]:
    segments: list[str | Ref] = []
    literal: list[str] = []
    position = 0
    in_string = False
    while position < len(raw):
        char = raw[position]
        if in_string:
            literal.append(char)
            if char == "\\" and position + 1 < len(raw):
                literal.append(raw[position + 1])
                position += 2
                continue
            if char == '"':
                in_string = False
            position += 1
            continue
        if char == '"':
            in_string = True
            literal.append(char)
            position += 1
            continue
        if char == "$":
            if raw.startswith("${", position):
                end = raw.find("}", position)
                if end < 0:
                    raise ScriptSyntaxError("JSON 中的 ${ 没有闭合", line)
                body = raw[position + 2:end]
                segments_list = body.split(".")
                if not VARIABLE_RE.fullmatch(segments_list[0]) or not all(PATH_SEGMENT_RE.fullmatch(item) for item in segments_list[1:]):
                    raise ScriptSyntaxError(f"JSON 中的变量引用无效：${{{body}}}", line)
                ref = Ref(tuple(segments_list))
                position = end + 1
            else:
                match = VARIABLE_RE.match(raw, position + 1)
                if not match or not match.group(0):
                    raise ScriptSyntaxError("JSON 中的 $ 之后应是变量名", line)
                ref = Ref((match.group(0),))
                position = match.end()
            if literal:
                segments.append("".join(literal))
                literal = []
            segments.append(ref)
            continue
        literal.append(char)
        position += 1
    if literal:
        segments.append("".join(literal))
    probe = "".join(item if isinstance(item, str) else "null" for item in segments)
    try:
        json.loads(probe)
    except json.JSONDecodeError as exc:
        raise ScriptSyntaxError(f"JSON 无效：{exc.msg}", line) from exc
    return segments


def _split_assignment(word: Word) -> tuple[str, str, Word | JsonValue | None] | None:
    """``name=value`` / ``name:=JSON`` → (name, operator, value) or None."""

    if word.json is not None:
        name = str(word.parts[0])[:-2]
        return name, ":=", word.json
    if word.call is not None:
        prefix = str(word.parts[0])
        return prefix[:-1], "=", None
    if not word.parts or not isinstance(word.parts[0], str):
        return None
    first = word.parts[0]
    match = ASSIGNMENT_PREFIX_RE.match(first)
    if not match or match.group(2) != "=":
        return None
    rest_first = first[match.end():]
    rest = tuple(([rest_first] if rest_first else []) + list(word.parts[1:]))
    if not rest and word.quoted:
        rest = ("",)
    return match.group(1), "=", Word(rest, word.quoted, word.line)


def _arguments(words: Iterable[Word | None], line: int, *, what: str) -> tuple[Argument, ...]:
    result: list[Argument] = []
    seen: set[str] = set()
    for word in words:
        if word is None:
            continue
        if word.call is not None:
            raise ScriptSyntaxError(f"{what}不能直接是指令调用；先保存到变量", word.line)
        split = _split_assignment(word)
        if split is None:
            raise ScriptSyntaxError(f"{what}应写成 名称=值 或 名称:=JSON", word.line)
        name, _operator, value = split
        if not ARGUMENT_RE.fullmatch(name):
            raise ScriptSyntaxError(f"参数名无效：{name}", word.line)
        if name in seen:
            raise ScriptSyntaxError(f"参数 {name} 重复", word.line)
        seen.add(name)
        assert value is not None
        result.append(Argument(name, value, word.line))
    return tuple(result)


# -- parser ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[_Token], lexer: _Lexer) -> None:
        self.tokens = tokens
        self.index = 0
        self.lexer = lexer

    def peek(self) -> _Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def keyword(self) -> str:
        token = self.peek()
        if token is None or token.kind != "word" or token.word is None or token.word.quoted:
            return ""
        text = token.word.text
        return text if text in KEYWORDS or text in BUILTINS or text in UNSUPPORTED_WORDS else ""

    def skip_separators(self) -> None:
        while (token := self.peek()) is not None and token.kind == "sep":
            self.index += 1

    def expect_keyword(self, name: str, line: int) -> None:
        self.skip_separators()
        if self.keyword() != name:
            token = self.peek()
            raise ScriptSyntaxError(f"缺少 {name}", token.line if token else line)
        self.index += 1

    def statements(self, terminators: frozenset[str]) -> tuple[Any, ...]:
        result: list[Any] = []
        while True:
            self.skip_separators()
            token = self.peek()
            if token is None:
                if terminators:
                    raise ScriptSyntaxError("缺少 " + " / ".join(sorted(terminators)), self.tokens[-1].line if self.tokens else 1)
                return tuple(result)
            if self.keyword() in terminators:
                return tuple(result)
            result.append(self.statement())

    def statement(self) -> Any:
        token = self.peek()
        assert token is not None
        if token.kind in {"and", "or"}:
            raise ScriptSyntaxError("&& 与 || 只能写在 if 条件中", token.line)
        keyword = self.keyword()
        if keyword in UNSUPPORTED_WORDS:
            raise ScriptSyntaxError(f"不支持 {keyword}", token.line, "effect.script_unsupported")
        if keyword == "if":
            return self.if_statement()
        if keyword == "for":
            return self.for_statement()
        if keyword in KEYWORDS:
            raise ScriptSyntaxError(f"这里不应出现 {keyword}", token.line)
        words = self.simple_words()
        if keyword == "output":
            return Output(_arguments(words[1:], token.line, what="output 的字段"), token.line)
        if keyword == "exit":
            if len(words) > 2 or (len(words) == 2 and words[1].text not in {"0"}):
                raise ScriptSyntaxError("exit 只表示提前成功结束；以失败结束请用 fail", token.line)
            return ExitStatement(token.line)
        if keyword == "fail":
            if len(words) > 2:
                raise ScriptSyntaxError('fail 只接受一个原因，例如 fail "原因"', token.line)
            return Fail(words[1] if len(words) == 2 else None, token.line)
        first = words[0]
        if first.text in {"[", "[[", "!"} and not first.quoted:
            raise ScriptSyntaxError("测试只能写在 if 条件中", token.line)
        split = _split_assignment(first)
        if split is not None:
            if len(words) > 1:
                raise ScriptSyntaxError("一行只写一个赋值", token.line)
            return self.assignment(first, split)
        return self.lexer.make_call(words, token.line)

    def assignment(self, word: Word, split: tuple[str, str, Word | JsonValue | None]) -> Assign:
        name, _operator, value = split
        if name in RESERVED_VARIABLES:
            raise ScriptSyntaxError(f"{name} 是只读变量", word.line, "effect.readonly_variable")
        if word.call is not None:
            return Assign(name, word.call, word.line)
        assert value is not None
        return Assign(name, value, word.line)

    def simple_words(self) -> list[Word]:
        words: list[Word] = []
        while (token := self.peek()) is not None and token.kind == "word":
            assert token.word is not None
            words.append(token.word)
            self.index += 1
        return words

    def if_statement(self) -> If:
        line = self.peek().line  # type: ignore[union-attr]
        self.index += 1
        branches: list[tuple[Any, tuple[Any, ...]]] = []
        condition = self.condition()
        self.expect_keyword("then", line)
        body = self.statements(frozenset({"elif", "else", "fi"}))
        branches.append((condition, body))
        otherwise: tuple[Any, ...] = ()
        while True:
            keyword = self.keyword()
            self.index += 1
            if keyword == "elif":
                condition = self.condition()
                self.expect_keyword("then", line)
                body = self.statements(frozenset({"elif", "else", "fi"}))
                branches.append((condition, body))
                continue
            if keyword == "else":
                otherwise = self.statements(frozenset({"fi"}))
                self.index += 1
            break
        return If(tuple(branches), otherwise, line)

    def for_statement(self) -> For:
        line = self.peek().line  # type: ignore[union-attr]
        self.index += 1
        token = self.peek()
        name = token.word.text if token is not None and token.word is not None else None
        if not name or not VARIABLE_RE.fullmatch(name):
            raise ScriptSyntaxError("for 之后应是变量名", line)
        if name in RESERVED_VARIABLES:
            raise ScriptSyntaxError(f"{name} 是只读变量", line, "effect.readonly_variable")
        self.index += 1
        if self.keyword() != "in":
            raise ScriptSyntaxError("for 变量之后应是 in", line)
        self.index += 1
        items = tuple(self.simple_words())
        self.expect_keyword("do", line)
        body = self.statements(frozenset({"done"}))
        self.index += 1
        return For(name, items, body, line)

    def condition(self) -> Any:
        left = self.pipeline()
        while (token := self.peek()) is not None and token.kind in {"and", "or"}:
            self.index += 1
            while (following := self.peek()) is not None and following.kind == "sep":
                self.index += 1
            right = self.pipeline()
            left = AndOr("&&" if token.kind == "and" else "||", left, right, token.line)
        token = self.peek()
        if token is None or token.kind != "sep":
            if self.keyword() not in {"then", "do"}:
                raise ScriptSyntaxError("条件之后应是 ; then", token.line if token else 1)
        return left

    def pipeline(self) -> Any:
        token = self.peek()
        if token is None or token.kind != "word":
            raise ScriptSyntaxError("缺少条件", token.line if token else 1)
        assert token.word is not None
        if token.word.text == "!" and not token.word.quoted:
            self.index += 1
            return Not(self.pipeline(), token.line)
        if token.word.text == "[" and not token.word.quoted:
            return self.test()
        if token.word.text == "[[" and not token.word.quoted:
            raise ScriptSyntaxError("不支持 [[ ]]，请用 [ ]", token.line, "effect.script_unsupported")
        words = self.simple_words()
        first = words[0]
        split = _split_assignment(first)
        if split is not None:
            if first.call is None or len(words) > 1:
                raise ScriptSyntaxError("条件中的赋值只能是 变量=$(指令名 …)", token.line)
            return CallCondition(self.assignment(first, split), token.line)
        return CallCondition(self.lexer.make_call(words, token.line), token.line)

    def test(self) -> Test:
        line = self.peek().line  # type: ignore[union-attr]
        self.index += 1
        words: list[Word] = []
        while True:
            token = self.peek()
            if token is None or token.kind != "word":
                raise ScriptSyntaxError("[ 缺少对应的 ]", line)
            assert token.word is not None
            self.index += 1
            if token.word.text == "]" and not token.word.quoted:
                break
            words.append(token.word)
        negate = False
        if words and words[0].text == "!" and not words[0].quoted:
            negate = True
            words = words[1:]
        if len(words) == 1:
            return Test("-n", (words[0],), negate, line)
        if len(words) == 2 and words[0].text in UNARY_OPERATORS and not words[0].quoted:
            return Test(str(words[0].text), (words[1],), negate, line)
        if len(words) == 3 and (words[1].text in TEXT_OPERATORS or words[1].text in INTEGER_OPERATORS) and not words[1].quoted:
            return Test(str(words[1].text), (words[0], words[2]), negate, line)
        raise ScriptSyntaxError("测试写法无效；支持 = != -z -n -eq -ne -lt -le -gt -ge", line)


def parse(text: str) -> Script:
    """Parse a script; raises ``ScriptSyntaxError`` with the failing line."""

    source = str(text or "")
    if len(source) > MAX_SCRIPT_CHARS:
        raise ScriptSyntaxError(f"脚本超过 {MAX_SCRIPT_CHARS} 字符", 1, "effect.script_too_long")
    lexer = _Lexer(source)
    tokens = lexer.tokens()
    parser = _Parser(tokens, lexer)
    statements = parser.statements(frozenset())
    return Script(statements, tuple(sorted(lexer.calls, key=lambda call: call.offset)))


# -- values ---------------------------------------------------------------------------


_MISSING = object()


def stringify(value: Any) -> str:
    if value is None or value is _MISSING:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _lookup(variables: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    if path[0] not in variables:
        return _MISSING
    value: Any = variables[path[0]]
    for segment in path[1:]:
        if isinstance(value, Mapping) and segment in value:
            value = value[segment]
        elif isinstance(value, list) and segment.isdigit() and int(segment) < len(value):
            value = value[int(segment)]
        else:
            return _MISSING
    return value


def _resolve(variables: Mapping[str, Any], ref: Ref) -> Any:
    value = _lookup(variables, ref.path)
    if ref.span is None:
        return value
    offset, length = ref.span
    return stringify(value)[offset:None if length is None else offset + length]


def expand(value: Word | JsonValue, variables: Mapping[str, Any]) -> Any:
    """Expand a word (typed when it is exactly one reference) or a JSON value."""

    if isinstance(value, JsonValue):
        text = "".join(
            item if isinstance(item, str) else json.dumps(
                None if (found := _lookup(variables, item.path)) is _MISSING else found,
                ensure_ascii=False,
            )
            for item in value.segments
        )
        return json.loads(text)
    meaningful = [part for part in value.parts if not (isinstance(part, str) and part == "")] or list(value.parts)
    if len(meaningful) == 1 and isinstance(meaningful[0], Ref):
        found = _resolve(variables, meaningful[0])
        return "" if found is _MISSING else deepcopy(found)
    return "".join(
        part if isinstance(part, str) else stringify(_resolve(variables, part))
        for part in value.parts
    )


def render_template_value(text: str) -> str:
    """Escape a Function Block template value for a double-quoted script string."""

    return "".join(TEMPLATE_ESCAPES.get(char, char) for char in str(text))


# -- static analysis ---------------------------------------------------------------------


@dataclass
class Analysis:
    script: Script | None
    diagnostics: list[tuple[str, str, int]] = field(default_factory=list)
    output_fields: set[str] | None = None
    argument_names: set[str] = field(default_factory=set)
    endpoint_names: set[str] = field(default_factory=set)
    instructions: list[str] = field(default_factory=list)


def _refs_of(value: Any) -> list[Ref]:
    if isinstance(value, Word):
        refs = [part for part in value.parts if isinstance(part, Ref)]
        return refs
    if isinstance(value, JsonValue):
        return [item for item in value.segments if isinstance(item, Ref)]
    if isinstance(value, Call):
        return [ref for argument in value.arguments for ref in _refs_of(argument.value)]
    return []


def analyze(
    text: str,
    *,
    declaration_for: Callable[[str], Any],
    declared_endpoints: Iterable[str] = (),
    connected_endpoints: Iterable[str] | None = None,
    endpoint_targets: dict[str, str] | None = None,
    runtime_supplied: Mapping[str, Iterable[str]] | None = None,
    validate_argument: Callable[[str, str, Any], None] | None = None,
) -> Analysis:
    """Parse and check a script against the instruction catalog.

    ``declaration_for(name)`` returns the catalog declaration (``streaming``,
    ``parameter_map``, ``one_of``, ``result``) or ``None``.
    """

    source = str(text or "")
    if not source.strip():
        return Analysis(None, [("effect.missing_script", "Effect 尚未编写脚本", 0)])
    try:
        script = parse(source)
    except ScriptSyntaxError as exc:
        return Analysis(None, [(exc.code, str(exc), exc.line)])
    result = Analysis(script)
    declared = set(declared_endpoints)
    connected = set(connected_endpoints) if connected_endpoints is not None else None
    supplied_extra = {key: set(value) for key, value in (runtime_supplied or {}).items()}
    defined: set[str] = set(RESERVED_VARIABLES)
    outputs: set[str] = set()
    has_output = False

    def check_refs(refs: Iterable[Ref], line: int) -> None:
        for ref in refs:
            root = ref.path[0]
            if root not in defined:
                result.diagnostics.append(("effect.undefined_variable", f"变量 {root} 未定义", line))
            if root == "arguments" and len(ref.path) > 1:
                result.argument_names.add(ref.path[1])
            if root == "endpoint" and len(ref.path) > 1:
                name = ref.path[1]
                result.endpoint_names.add(name)
                if name not in declared:
                    result.diagnostics.append(("effect.unknown_endpoint", f"端点 {name} 未声明", line))
                elif connected is not None and name not in connected:
                    result.diagnostics.append(("effect.missing_target", f"端点 {name} 尚未连接对象", line))

    def check_call(call: Call) -> None:
        result.instructions.append(call.instruction)
        for argument in call.arguments:
            check_refs(_refs_of(argument.value), argument.line)
            if validate_argument is not None and not _refs_of(argument.value):
                try:
                    validate_argument(call.instruction, argument.name, expand(argument.value, {}))
                except ValueError as exc:
                    result.diagnostics.append(("effect.invalid_argument", str(exc), argument.line))
        declaration = declaration_for(call.instruction)
        if declaration is None:
            result.diagnostics.append(("effect.unknown_instruction", f"指令 {call.instruction} 未登记", call.line))
            return
        if getattr(declaration, "streaming", False):
            result.diagnostics.append(("effect.streaming_instruction", f"{call.instruction} 是流式指令，Effect 只能调用非流式指令", call.line))
            return
        parameters = getattr(declaration, "parameter_map", {}) or {}
        names = {argument.name for argument in call.arguments}
        for name in sorted(names - set(parameters)):
            result.diagnostics.append(("effect.unknown_argument", f"{call.instruction} 没有参数 {name}", call.line))
        supplied = names | supplied_extra.get(call.instruction, set())
        for name, parameter in parameters.items():
            if getattr(parameter, "required", False) and name not in supplied:
                result.diagnostics.append(("effect.missing_argument", f"{call.instruction} 缺少必填参数 {name}", call.line))
        one_of = getattr(declaration, "one_of", ()) or ()
        if one_of and not any(all(name in supplied for name in group) for group in one_of):
            rendered = " 或 ".join("+".join(group) for group in one_of)
            result.diagnostics.append(("effect.missing_argument", f"{call.instruction} 需要提供其中一组参数：{rendered}", call.line))
        any_of = getattr(declaration, "any_of", ()) or ()
        if any_of and not supplied & set(any_of):
            result.diagnostics.append(("effect.missing_argument", f"{call.instruction} 至少需要一个参数：{'、'.join(any_of)}", call.line))

    def visit_condition(condition: Any) -> None:
        if isinstance(condition, Test):
            for operand in condition.operands:
                check_refs(_refs_of(operand), condition.line)
        elif isinstance(condition, CallCondition):
            visit(condition.target)
        elif isinstance(condition, Not):
            visit_condition(condition.condition)
        elif isinstance(condition, AndOr):
            visit_condition(condition.left)
            visit_condition(condition.right)

    def visit(statement: Any) -> None:
        nonlocal has_output
        if isinstance(statement, Call):
            check_call(statement)
        elif isinstance(statement, Assign):
            if isinstance(statement.value, Call):
                check_call(statement.value)
            else:
                check_refs(_refs_of(statement.value), statement.line)
            defined.add(statement.name)
        elif isinstance(statement, Output):
            has_output = True
            for argument in statement.arguments:
                check_refs(_refs_of(argument.value), argument.line)
                outputs.add(argument.name)
        elif isinstance(statement, Fail):
            if statement.reason is not None:
                check_refs(_refs_of(statement.reason), statement.line)
        elif isinstance(statement, If):
            for condition, body in statement.branches:
                visit_condition(condition)
                for item in body:
                    visit(item)
            for item in statement.otherwise:
                visit(item)
        elif isinstance(statement, For):
            for item in statement.items:
                check_refs(_refs_of(item), statement.line)
            defined.add(statement.name)
            for item in statement.body:
                visit(item)

    for statement in script.statements:
        visit(statement)
    from .lineage_effect_targets import target_diagnostics

    result.diagnostics.extend(target_diagnostics(script, declaration_for, endpoint_targets))
    if has_output:
        result.output_fields = outputs
    else:
        last = script.statements[-1] if script.statements else None
        call = last if isinstance(last, Call) else last.value if isinstance(last, Assign) and isinstance(last.value, Call) else None
        if call is not None:
            declaration = declaration_for(call.instruction)
            result.output_fields = {item.name for item in getattr(declaration, "result", ())} if declaration is not None else None
        else:
            result.output_fields = None
    return result


# -- interpreter --------------------------------------------------------------------------


@dataclass(frozen=True)
class CallRequest:
    position: str
    index: int
    line: int
    instruction: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class CallResult:
    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] = field(default_factory=dict)
    instruction_id: str = ""


@dataclass
class Outcome:
    status: str  # succeeded | failed
    outputs: dict[str, Any] | None
    last_call: tuple[str, dict[str, Any]] | None
    call_count: int
    error: ScriptFailure | None = None


class Interpreter:
    """Run a parsed script; every call goes through ``execute_call``."""

    def __init__(
        self,
        script: Script,
        inputs: Mapping[str, Any],
        execute_call: Callable[[CallRequest], CallResult],
        *,
        max_calls: int = MAX_CALLS,
    ) -> None:
        self.script = script
        self.variables: dict[str, Any] = {name: deepcopy(inputs.get(name, {})) for name in RESERVED_VARIABLES}
        self.execute_call = execute_call
        self.max_calls = max_calls
        self.loop_path: list[int] = []
        self.call_count = 0
        self.outputs: dict[str, Any] | None = None
        self.last_call: tuple[str, dict[str, Any]] | None = None

    def run(self) -> Outcome:
        try:
            self.block(self.script.statements)
        except _Exit:
            pass
        except ScriptFailure as failure:
            return Outcome("failed", None, self.last_call, self.call_count, failure)
        return Outcome("succeeded", self.outputs, self.last_call, self.call_count)

    def block(self, statements: Iterable[Any]) -> None:
        for statement in statements:
            self.statement(statement)

    def statement(self, statement: Any) -> None:
        if isinstance(statement, Call):
            self.call(statement, condition=False)
        elif isinstance(statement, Assign):
            self.assign(statement, condition=False)
        elif isinstance(statement, Output):
            if self.outputs is None:
                self.outputs = {}
            for argument in statement.arguments:
                self.outputs[argument.name] = expand(argument.value, self.variables)  # type: ignore[arg-type]
        elif isinstance(statement, ExitStatement):
            raise _Exit()
        elif isinstance(statement, Fail):
            reason = stringify(expand(statement.reason, self.variables)) if statement.reason is not None else ""
            raise ScriptFailure("effect.script_failed", reason or "脚本以 fail 结束", line=statement.line, retryable=False)
        elif isinstance(statement, If):
            for condition, body in statement.branches:
                if self.condition(condition):
                    self.block(body)
                    return
            self.block(statement.otherwise)
        elif isinstance(statement, For):
            items: list[Any] = []
            for word in statement.items:
                value = expand(word, self.variables)
                if isinstance(value, list):
                    items.extend(value)
                elif value != "" or word.quoted:
                    items.append(value)
            for position, item in enumerate(items):
                self.variables[statement.name] = deepcopy(item)
                self.loop_path.append(position)
                try:
                    self.block(statement.body)
                finally:
                    self.loop_path.pop()

    def assign(self, statement: Assign, *, condition: bool) -> bool:
        if isinstance(statement.value, Call):
            outcome = self.call(statement.value, condition=condition)
            if outcome.ok:
                self.variables[statement.name] = deepcopy(outcome.result)
            return outcome.ok
        self.variables[statement.name] = expand(statement.value, self.variables)
        return True

    def call(self, call: Call, *, condition: bool) -> CallResult:
        self.call_count += 1
        if self.call_count > self.max_calls:
            raise ScriptFailure(
                "effect.script_call_limit",
                f"一次激活最多执行 {self.max_calls} 次指令调用",
                line=call.line,
            )
        arguments = {argument.name: expand(argument.value, self.variables) for argument in call.arguments}  # type: ignore[arg-type]
        index = self.script.index_of(call)
        position = f"c{index}" + "".join(f".{item}" for item in self.loop_path)
        outcome = self.execute_call(CallRequest(position, index, call.line, call.instruction, arguments))
        if outcome.ok:
            self.last_call = (call.instruction, dict(outcome.result))
        elif not condition:
            error = outcome.error or {}
            raise ScriptFailure(
                str(error.get("code") or "instruction.failed"),
                str(error.get("message") or f"{call.instruction} 调用失败"),
                line=call.line,
                retryable=bool(error.get("retryable")),
            )
        return outcome

    def condition(self, condition: Any) -> bool:
        if isinstance(condition, Not):
            return not self.condition(condition.condition)
        if isinstance(condition, AndOr):
            left = self.condition(condition.left)
            if condition.operator == "&&":
                return left and self.condition(condition.right)
            return left or self.condition(condition.right)
        if isinstance(condition, CallCondition):
            if isinstance(condition.target, Assign):
                return self.assign(condition.target, condition=True)
            return self.call(condition.target, condition=True).ok
        if isinstance(condition, Test):
            result = self.test(condition)
            return not result if condition.negate else result
        raise ScriptFailure("effect.script_syntax", "未知条件", line=getattr(condition, "line", 0))

    def test(self, test: Test) -> bool:
        values = [stringify(expand(operand, self.variables)) for operand in test.operands]
        operator = test.operator
        if operator == "-z":
            return values[0] == ""
        if operator == "-n":
            return values[0] != ""
        if operator in {"=", "=="}:
            return values[0] == values[1]
        if operator == "!=":
            return values[0] != values[1]
        try:
            left, right = int(values[0].strip()), int(values[1].strip())
        except ValueError:
            raise ScriptFailure(
                "effect.script_integer_expected",
                f"{operator} 需要整数，得到 {values[0]!r} 与 {values[1]!r}",
                line=test.line,
            ) from None
        return {
            "-eq": left == right, "-ne": left != right, "-lt": left < right,
            "-le": left <= right, "-gt": left > right, "-ge": left >= right,
        }[operator]


# -- writing scripts ------------------------------------------------------------------------
# One spelling for every script Portal writes itself: Function Block builtins,
# business workflows and new-Effect defaults.


def quote(text: str) -> str:
    """A double-quoted script string whose content is exactly ``text``."""

    return '"' + render_template_value(text) + '"'


def reference(path: str) -> str:
    """A double-quoted single reference such as ``"${endpoint.target}"`` (keeps the value's type)."""

    return '"${' + path + '}"'


def literal_argument(name: str, value: Any) -> str:
    """``name="text"`` for text, ``name:=JSON`` for any other constant."""

    if isinstance(value, str):
        return f"{name}={quote(value)}"
    return f"{name}:=" + json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def call_line(instruction: str, arguments: Iterable[str]) -> str:
    return " ".join([instruction, *arguments])


def deliver_message_script(message: str, *, endpoint: str = "target", lend_temporary_credentials: bool = False,
                           **constants: Any) -> str:
    """The one-line script that delivers a fixed prompt to the Resident behind ``endpoint``."""

    if lend_temporary_credentials and "temporary_credentials" in constants:
        raise ValueError("Temporary credentials must have one source")
    return call_line("DeliverMessage", [
        f"resident_id={reference('endpoint.' + endpoint)}",
        literal_argument("message", message),
        *(literal_argument(name, value) for name, value in constants.items()),
        *([f"temporary_credentials={reference('event.payload.data.temporary_credentials')}"]
          if lend_temporary_credentials else []),
    ])


def deliver_script_parts(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """(endpoint, fixed prompt, other constants) of a script written by ``deliver_message_script``.

    ``None`` when the script has any other shape, for example after someone
    edited it into a longer script.
    """

    try:
        script = parse(text)
    except ScriptSyntaxError:
        return None
    if len(script.statements) != 1 or not isinstance(script.statements[0], Call):
        return None
    call = script.statements[0]
    if call.instruction != "DeliverMessage":
        return None
    endpoint = ""
    message: str | None = None
    constants: dict[str, Any] = {}
    for argument in call.arguments:
        value = argument.value
        if argument.name == "resident_id":
            refs = [part for part in value.parts if isinstance(part, Ref)] if isinstance(value, Word) else []
            if len(refs) != 1 or len(value.parts) != 1 or refs[0].path[:1] != ("endpoint",) or len(refs[0].path) != 2:
                return None
            endpoint = refs[0].path[1]
        elif (argument.name == "temporary_credentials" and isinstance(value, Word)
              and len(value.parts) == 1 and isinstance(value.parts[0], Ref)
              and value.parts[0].path == ("event", "payload", "data", "temporary_credentials")):
            constants["lend_temporary_credentials"] = True
        elif isinstance(value, Word) and value.text is not None:
            if argument.name == "message":
                message = value.text
            else:
                constants[argument.name] = value.text
        elif isinstance(value, JsonValue) and all(isinstance(item, str) for item in value.segments):
            constant = expand(value, {})
            if argument.name == "message":
                return None
            constants[argument.name] = constant
        else:
            return None
    if not endpoint or message is None:
        return None
    return endpoint, message, constants


def script_label(text: str) -> str:
    """Short description for cards and explanations: the instructions a script calls."""

    try:
        script = parse(text)
    except ScriptSyntaxError:
        return ""
    names = list(dict.fromkeys(call.instruction for call in script.calls))
    if not names:
        return ""
    if len(script.calls) == 1:
        return names[0]
    shown = "、".join(names[:3]) + ("…" if len(names) > 3 else "")
    return f"{len(script.calls)} 次调用：{shown}"
