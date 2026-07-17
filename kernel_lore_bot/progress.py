"""The terminal boundary, so tests and dry runs are not polluted by tqdm."""

from __future__ import annotations

from typing import Protocol

import tqdm as tqdm_lib


class ProgressBar(Protocol):
    def update(self, n: int = 1) -> None: ...
    def set_note(self, note: str) -> None: ...
    def close(self) -> None: ...
    def __enter__(self) -> "ProgressBar": ...
    def __exit__(self, *exc_info: object) -> None: ...


class Progress(Protocol):
    def bar(self, desc: str, total: int | None = None) -> ProgressBar: ...


class NullBar:
    """Does nothing. Used in tests and dry runs."""

    def update(self, n: int = 1) -> None:
        pass

    def set_note(self, note: str) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> "NullBar":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class NullProgress:
    def bar(self, desc: str, total: int | None = None) -> NullBar:
        return NullBar()


class TqdmBar:
    def __init__(self, desc: str, total: int | None) -> None:
        self._bar = tqdm_lib.tqdm(
            total=total, desc=desc, unit=" entries", dynamic_ncols=True, leave=False
        )

    def update(self, n: int = 1) -> None:
        self._bar.update(n)

    def set_note(self, note: str) -> None:
        self._bar.set_postfix_str(note)

    def close(self) -> None:
        self._bar.close()

    def __enter__(self) -> "TqdmBar":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class TqdmProgress:
    def bar(self, desc: str, total: int | None = None) -> TqdmBar:
        return TqdmBar(desc, total)
