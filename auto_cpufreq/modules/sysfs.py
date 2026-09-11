from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Generic, Optional, TypeVar


T = TypeVar("T")


class ReadStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    INVALID = "invalid"


@dataclass(frozen=True)
class ReadResult(Generic[T]):
    status: ReadStatus
    value: Optional[T] = None


def read_text(path: Path) -> ReadResult[str]:
    try:
        return ReadResult(ReadStatus.AVAILABLE, Path(path).read_text().strip())
    except FileNotFoundError:
        return ReadResult(ReadStatus.MISSING)
    except OSError:
        return ReadResult(ReadStatus.UNREADABLE)


def read_int(path: Path) -> ReadResult[int]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    try:
        return ReadResult(ReadStatus.AVAILABLE, int(result.value))
    except (TypeError, ValueError):
        return ReadResult(ReadStatus.INVALID)


def read_bool01(path: Path, invert: bool = False) -> ReadResult[bool]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    if result.value not in ("0", "1"):
        return ReadResult(ReadStatus.INVALID)
    value = result.value == "1"
    return ReadResult(ReadStatus.AVAILABLE, not value if invert else value)
