"""Relay standard input to standard output with every complete CSI
sequence dropped and every other control character escaped (DESIGN §6
*No text leerie did not write reaches the operator's terminal as a control
sequence*): the launcher's relay of what a container
or a machine prints. The rule is `orchestrator/leerie.py`'s
`_terminal_text`, the same character for character (pinned by
tests/test_terminal_text.py). Standard library only: the host's `python3`
runs it with `-I -S`."""
import codecs
import os
import re
import sys

# A complete 7-bit CSI (colour, cursor, erase), dropped; else C0 but
# newline and tab, DEL and C1, the bidirectional and line/paragraph
# separators a terminal acts on, and a lone surrogate (a byte that was not
# UTF-8, which `surrogateescape` keeps), escaped.
_CONTROL = re.compile(
    "\x1b\\[[0-?]{0,64}[ -/]{0,8}[@-~]"
    "|[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e"
    "\u2066-\u2069\ud800-\udfff]")
# A CSI a read may have cut short, held for the next read. Both patterns
# bound the parameter (64) and intermediate (8) bytes, so a held prefix is
# at most 74 characters, and one longer than the bound is escaped by the
# whole-stream rule and the relay alike.
_CSI_PREFIX = re.compile("\x1b(?:\\[[0-?]{0,64}[ -/]{0,8})?\\Z")


def _escape(m: "re.Match[str]") -> str:
    if len(m.group()) > 1:
        return ""
    o = ord(m.group())
    if 0xdc80 <= o <= 0xdcff:
        return f"\\x{o - 0xdc00:02x}"
    return f"\\x{o:02x}" if o < 0x100 else f"\\u{o:04x}"


def text(s: str) -> str:
    """`s` with every complete CSI removed and every other control
    character written as its escape."""
    return _CONTROL.sub(_escape, s)


def main() -> int:
    dec = codecs.getincrementaldecoder("utf-8")("surrogateescape")
    held = ""
    while True:
        try:
            b = os.read(0, 65536)
        except InterruptedError:
            continue
        s = held + dec.decode(b, final=not b)
        held = ""
        m = _CSI_PREFIX.search(s) if b else None
        if m is not None:
            s, held = s[:m.start()], m.group()
        out = text(s)
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
