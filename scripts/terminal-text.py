"""Relay standard input to standard output with every control character
escaped (DESIGN §6 *No text leerie did not write reaches the operator's
terminal as a control sequence*): the launcher's relay of what a container
or a machine prints. The rule is `orchestrator/leerie.py`'s
`_terminal_text`, the same character for character (pinned by
tests/test_terminal_text.py). Standard library only: the host's `python3`
runs it with `-I -S`."""
import codecs
import os
import re
import sys

# C0 but newline and tab, DEL and C1, the bidirectional and line/paragraph
# separators a terminal acts on, and a lone surrogate (a byte that was not
# UTF-8, which `surrogateescape` keeps).
_CONTROL = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e"
    "\u2066-\u2069\ud800-\udfff]")


def _escape(m: "re.Match[str]") -> str:
    o = ord(m.group())
    if 0xdc80 <= o <= 0xdcff:
        return f"\\x{o - 0xdc00:02x}"
    return f"\\x{o:02x}" if o < 0x100 else f"\\u{o:04x}"


def text(s: str) -> str:
    """`s` with every control character written as its escape."""
    return _CONTROL.sub(_escape, s)


def main() -> int:
    dec = codecs.getincrementaldecoder("utf-8")("surrogateescape")
    while True:
        try:
            b = os.read(0, 65536)
        except InterruptedError:
            continue
        out = text(dec.decode(b, final=not b))
        data = out.encode("utf-8")
        try:
            while data:
                data = data[os.write(1, data):]
        except BrokenPipeError:
            return 0
        if not b:
            return 0


if __name__ == "__main__":
    sys.exit(main())
