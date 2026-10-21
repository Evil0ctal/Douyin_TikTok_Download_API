"""The code of a shell script with its prose taken out, for comparing two
translations of one program.

install/install.sh and install/install.zh.sh are the same program in two
languages. Comparing their control-flow words alone let a changed number, flag
or condition through - ``MEM_MIB / 9`` made ``/ 2``, ``--ff-only`` made
``--no-ff`` - so this keeps every line that does something and every string in
it, and leaves out only what a translator decides: comments, heredoc bodies,
the lines that only print (``dim``, ``info``, ``warn``, ``ok``, ``step``,
``die``), and blank-line or comment ``printf``s, whose number differs between
the two languages. A string is then either the same on both sides or
translated - which, for the Chinese one, means it carries CJK, or names that
language's own files.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Stands in for a string in the skeleton. It cannot occur in a script.
MARK = "\x00"

_PRINTS = re.compile(r"^(?:\w+\)\s*)?(?:dim|info|warn|ok|step|die)\b")
_PRINTF = re.compile(r"^printf(?:\s+\x00)+(?:\s*>&2)?$")
_SPACING = re.compile(r"(?:\\n|\s)*(?:#.*)?", flags=re.S)
_HEREDOC = re.compile(r"<<-?\s*'?([A-Za-z_]\w*)'?")
_CJK = re.compile("[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")
_BRACED = re.compile(r"\$\{(\w+)\}")

Line = tuple[str, list[str]]


def _end_of_string(text: str, start: int) -> int:
    """Index just past the double-quoted string that opens at ``text[start]``,
    with whatever ``$( )`` and quotes are nested inside it."""
    stack = ["dq"]
    i = start + 1
    while stack:
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if stack[-1] == "dq":
            if char == '"':
                stack.pop()
            elif text.startswith("$(", i):
                stack.append("cmd")
                i += 1
        elif char == "'":
            i = text.index("'", i + 1)
        elif char == '"':
            stack.append("dq")
        elif char == "(":
            stack.append("paren")
        elif char == ")":
            stack.pop()
        i += 1
    return i


def _tokens(text: str) -> tuple[str, list[str]]:
    """The text with comments gone and every top-level string as MARK, and the
    strings in order."""
    out: list[str] = []
    strings: list[str] = []
    i, word_start = 0, True
    while i < len(text):
        char = text[i]
        if char == "\\":
            out.append(text[i : i + 2])
            i += 2
            word_start = False
        elif char == "#" and word_start:
            while i < len(text) and text[i] != "\n":
                i += 1
        elif char == "'":
            end = text.index("'", i + 1)
            strings.append(text[i + 1 : end])
            out.append(MARK)
            i = end + 1
            word_start = False
        elif char == '"':
            end = _end_of_string(text, i)
            strings.append(text[i + 1 : end - 1])
            out.append(MARK)
            i = end
            word_start = False
        else:
            out.append(char)
            word_start = char in " \t\n;|&(){}"
            i += 1
    return "".join(out), strings


def _without_heredocs(path: Path) -> str:
    kept: list[str] = []
    in_heredoc, tag = False, ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if in_heredoc:
            if line.strip() == tag:
                in_heredoc = False
            continue
        opened = _HEREDOC.search(line)
        if opened:
            in_heredoc, tag = True, opened.group(1)
            line = line[: opened.start()]
        kept.append(line)
    return "\n".join(kept)


def skeleton(path: Path) -> str:
    """The script with comments and heredoc bodies gone and every string
    emptied to ``""``, nested ``$( )`` and quotes inside it included."""
    text, _ = _tokens(_without_heredocs(path))
    return text.replace(MARK, '""')


def code_lines(path: Path) -> list[Line]:
    """Each line that does something, with the strings on it."""
    skeleton_text, strings = _tokens(_without_heredocs(path))
    lines: list[Line] = []
    taken = 0
    for raw in skeleton_text.split("\n"):
        count = raw.count(MARK)
        mine, taken = strings[taken : taken + count], taken + count
        line = raw.strip()
        if not line or _PRINTS.match(line):
            continue
        if _PRINTF.match(line) and all(_SPACING.fullmatch(text) for text in mine):
            continue
        lines.append((line, mine))
    return lines


def _same(english: str, chinese: str) -> bool:
    if _CJK.search(chinese):
        return True
    localized = chinese.replace("documents/zh/", "documents/en/").replace(
        "install.zh.sh", "install.sh"
    )
    # ${name} and $name are one expansion; the Chinese script braces every
    # variable that a CJK character could otherwise run into.
    return _BRACED.sub(r"$\1", english) == _BRACED.sub(r"$\1", localized)


def differences(english: Path, chinese: Path) -> list[str]:
    """Where the Chinese script does something the English one does not."""
    ours, theirs = code_lines(english), code_lines(chinese)
    found: list[str] = []
    if len(ours) != len(theirs):
        found.append(f"{len(ours)} lines of code against {len(theirs)}")
    for (line_en, strings_en), (line_zh, strings_zh) in zip(ours, theirs, strict=False):
        shown = line_en.replace(MARK, '"..."')
        if line_en != line_zh or len(strings_en) != len(strings_zh):
            found.append(f"code differs: {shown}  /  {line_zh.replace(MARK, '"..."')}")
            continue
        for text_en, text_zh in zip(strings_en, strings_zh, strict=True):
            if not _same(text_en, text_zh):
                found.append(f"string differs in {shown}: {text_en!r} / {text_zh!r}")
    return found


__all__ = ["code_lines", "differences", "skeleton"]
