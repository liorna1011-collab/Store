"""
A ZIP written while it is sent.

Building the archive in a temp file first meant: nothing reached the browser until every
video was copied (a multi-GB package looked like a frozen download), the disk briefly
held a second copy of every output, and two downloads of the same project wrote the same
temp file. Here the archive is produced in 1 MB pieces straight into the response; the
files are stored, not compressed (MP4 does not compress), and ZIP64 is used so archives
and members over 4 GB work.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable, Iterator, Union

PIECE = 1024 * 1024

Entry = tuple[str, Union[Path, str, bytes]]   # (name in the archive, file path or content)


class _Sink:
    """A write-only, non-seekable stream: zipfile then writes data descriptors."""

    def __init__(self) -> None:
        self.parts: list[bytes] = []
        self.pos = 0

    def write(self, b: bytes) -> int:
        self.parts.append(bytes(b))
        self.pos += len(b)
        return len(b)

    def tell(self) -> int:
        return self.pos

    def seekable(self) -> bool:
        return False

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out, self.parts = b"".join(self.parts), []
        return out


def stream_zip(entries: Iterable[Entry]) -> Iterator[bytes]:
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for name, src in entries:
            if isinstance(src, (bytes, str)):
                data = src.encode("utf-8") if isinstance(src, str) else src
                zf.writestr(name, data)
            else:
                info = zipfile.ZipInfo.from_file(str(src), arcname=name)
                info.compress_type = zipfile.ZIP_STORED
                with open(src, "rb") as fh, zf.open(info, "w", force_zip64=True) as dst:
                    while piece := fh.read(PIECE):
                        dst.write(piece)
                        chunk = sink.take()
                        if chunk:
                            yield chunk
            chunk = sink.take()
            if chunk:
                yield chunk
    chunk = sink.take()
    if chunk:
        yield chunk
