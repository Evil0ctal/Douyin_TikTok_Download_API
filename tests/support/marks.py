"""The project's mark, and how the language checks recognise it.

Two separate tests assert that this codebase's Python contains no CJK, because
writing a Chinese comment is the low-effort choice and only a machine check
holds the line. The mark - the cat that has sat at the top of this project's
entry points since v1 - is drawn partly in katakana and half-width forms, so
both checks would reject it.

The exemption lives here, once, rather than in both files: a rule that exists in
two copies is a rule that will disagree with itself. It is written by SHAPE
rather than by filename, so it cannot become a place to park a sentence.
"""

from __future__ import annotations

#: Every character the mark is drawn from, and nothing else. Derived from the
#: art itself rather than typed out: box strokes, punctuation, the four katakana
#: the cat's face is made of, and the star.
MARK_GLYPHS: frozenset[str] = frozenset(
    " \t#()/=FS_`adelmrstx|\u2502\u2b50\ufe0f"
    "\u3000\u3064\u30ce\u30d5\u30df\u30fd"
    "\u4e8c\uff09\uff0f\uff1e\uff3c\uff3f\uff89\uffe3"
)


def is_project_mark(line: str) -> bool:
    """Whether a line belongs to the mark rather than being prose.

    Every character has to come from the mark's own small alphabet, so a line
    that smuggles a real word in beside the cat is not exempt.
    """
    return bool(line.strip()) and set(line) <= MARK_GLYPHS


__all__ = ["MARK_GLYPHS", "is_project_mark"]
