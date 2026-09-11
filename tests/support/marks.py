"""The project's mark, and how the language checks recognise it.

Three separate checks assert this codebase contains no CJK - two in Python here
and one eslint rule for the console - because writing a Chinese comment is the
low-effort choice and only a machine check holds the line. The mark - the cat
that has sat at the top of this project's entry points since v1 - is drawn
partly in katakana and half-width forms, so all three would reject it.

The exemption lives here, once, rather than in each of them: a rule that exists
in three copies is a rule that will disagree with itself. It is written by SHAPE
rather than by filename, so it cannot become a place to park a sentence.

Split in two because only half of it can be shared with the eslint rule.
:data:`MARK_DRAWING` is the cat itself and is identical in both languages;
``test_repo_hygiene.py`` asserts that this list and ``MARK_DRAWING`` in
``web/eslint.config.js`` agree. :data:`MARK_SYNTAX` is what the surrounding
language wraps it in, and that genuinely differs - a Python line starts with
``#``, a TypeScript one is a quoted string in an array.
"""

from __future__ import annotations

#: The cat, and nothing else: box strokes, the four katakana its face is made
#: of, the full-width forms it is spaced with, and the star.
#:
#: Escaped rather than written out, and that is not fussiness. The file that
#: defines the exemption must not need it - a literal cat here is CJK in a
#: Python source, and `ruff format` will happily join the lines into one that
#: no longer looks like the mark, at which point this file fails the check it
#: exists to serve. It did, once.
MARK_DRAWING: frozenset[str] = frozenset(
    "\u2502\u2b50\ufe0f"
    "\u3000\u3064\u30ce\u30d5\u30df\u30fd"
    "\u4e8c\uff09\uff0f\uff1e\uff3c\uff3f\uff89\uffe3"
)

#: ASCII the drawing uses, plus what either language wraps it in: a Python
#: comment's ``#``, and the quote and comma of a TypeScript string in a list.
#: Widening this cannot let prose through - a line still has to be made
#: ENTIRELY of these plus the drawing, and none of them is a Chinese character.
MARK_SYNTAX: str = " \t#'(),/=FS_`adelmrstx|"

MARK_GLYPHS: frozenset[str] = MARK_DRAWING | frozenset(MARK_SYNTAX)


def is_project_mark(line: str) -> bool:
    """Whether a line belongs to the mark rather than being prose.

    Every character has to come from the mark's own small alphabet, so a line
    that smuggles a real word in beside the cat is not exempt.
    """
    return bool(line.strip()) and set(line) <= MARK_GLYPHS


__all__ = ["MARK_DRAWING", "MARK_GLYPHS", "MARK_SYNTAX", "is_project_mark"]
