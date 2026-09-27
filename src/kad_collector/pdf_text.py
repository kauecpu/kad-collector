"""Conservative repair of physical line breaks, before question segmentation."""

from __future__ import annotations

import re
from typing import Any

_LETTERS = r"[^\W\d_]"
_WORD = re.compile(rf"(?<![\w-]){_LETTERS}+(?![\w-])")
_COMPOUND = re.compile(rf"{_LETTERS}+(?:-{_LETTERS}+)+")
_LINE_HYPHEN = re.compile(
    rf"(?P<left>{_LETTERS}{{2,}})(?P<dash>[-\u00ad])[ \t]*\r?\n[ \t]*"
    rf"(?P<right>{_LETTERS}{{2,}})"
)


def repair_page_hyphenation(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join only discretionary hyphens or spellings corroborated in this document.

    ASCII '-' does not tell us whether a break is typographic or lexical. A
    hard-hyphenated spelling anywhere in the document vetoes automatic joining.
    Uncorroborated words and cross-page continuations are left untouched.
    """
    document = "\n\n".join(str(page["text"]) for page in pages)
    words = {match.group().casefold() for match in _WORD.finditer(document)}
    words.update(
        (match["left"] + match["right"]).casefold()
        for match in _LINE_HYPHEN.finditer(document)
        if match["dash"] == "\u00ad" and match["right"][0].islower()
    )
    compounds = {match.group().casefold() for match in _COMPOUND.finditer(document)}

    def repair(match: re.Match[str]) -> str:
        left, right = match["left"], match["right"]
        joined = left + right
        if not right[0].islower():
            return match.group()
        if (left + "-" + right).casefold() in compounds:
            return match.group()
        if match["dash"] == "\u00ad" or joined.casefold() in words:
            return joined
        return match.group()

    return [{**page, "text": _LINE_HYPHEN.sub(repair, str(page["text"]))} for page in pages]
