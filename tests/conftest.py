from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from types import TracebackType

import pytest

from web_page import FilePayload, WebPage

type Call = tuple[str, ...]
type WriteYaml = Callable[..., Path]


def _timed(call: Call, timeout: float | None) -> Call:
    """Record a timeout only when a step passes one, so most calls stay short."""
    return call if timeout is None else (*call, f"timeout={timeout}")


@dataclass
class FakeLocator:
    """A locator on a page where every selector matches one visible element,
    except those in ``missing``, which match nothing: acting on one times out."""

    selector: str
    calls: list[Call]
    missing: set[str] = field(default_factory=set)

    async def _act(self, *call: str, timeout: float | None = None) -> None:
        await asyncio.sleep(0)
        if self.selector in self.missing:
            raise TimeoutError(f"{call[0]} {self.selector}")
        self.calls.append(_timed((call[0], self.selector, *call[1:]), timeout))

    async def click(self, *, timeout: float | None = None) -> None:
        await self._act("click", timeout=timeout)

    async def fill(self, value: str, *, timeout: float | None = None) -> None:
        await self._act("fill", value, timeout=timeout)

    async def wait_for(self, *, state: str, timeout: float | None = None) -> None:
        await asyncio.sleep(0)
        # A missing element is hidden; any other is visible and attached.
        if (self.selector in self.missing) != (state == "hidden"):
            raise TimeoutError(f"wait_for {self.selector} {state}")
        self.calls.append(_timed(("wait_for", self.selector, state), timeout))

    async def hover(self, *, timeout: float | None = None) -> None:
        await self._act("hover", timeout=timeout)

    async def check(self, *, timeout: float | None = None) -> None:
        await self._act("check", timeout=timeout)

    async def uncheck(self, *, timeout: float | None = None) -> None:
        await self._act("uncheck", timeout=timeout)

    async def select_option(self, value: str, *, timeout: float | None = None) -> None:
        await self._act("select_option", value, timeout=timeout)

    async def press(self, key: str, *, timeout: float | None = None) -> None:
        await self._act("press", key, timeout=timeout)

    async def inner_text(self, *, timeout: float | None = None) -> str:
        await self._act("inner_text", timeout=timeout)
        return f"text of {self.selector}"

    async def all_inner_texts(self) -> list[str]:
        await asyncio.sleep(0)
        return [f"text 1 of {self.selector}", f"text 2 of {self.selector}"]

    async def input_value(self, *, timeout: float | None = None) -> str:
        await self._act("input_value", timeout=timeout)
        return f"value of {self.selector}"

    async def get_attribute(
        self, name: str, *, timeout: float | None = None
    ) -> str | None:
        await self._act("get_attribute", name, timeout=timeout)
        return None if name == "missing" else f"{name} of {self.selector}"

    async def set_input_files(
        self,
        files: Sequence[str] | Sequence[FilePayload],
        *,
        timeout: float | None = None,
    ) -> None:
        await self._act("set_input_files", *_describe(files), timeout=timeout)

    async def is_visible(self) -> bool:
        await asyncio.sleep(0)
        return self.selector not in self.missing

    async def count(self) -> int:
        await asyncio.sleep(0)
        return 0 if self.selector in self.missing else 1

    async def evaluate(
        self, expression: str, arg: object = None, *, timeout: float | None = None
    ) -> str:
        await self._act("evaluate", str(arg), timeout=timeout)
        return f"text of {self.selector} without {arg}"

    async def evaluate_all(self, expression: str, arg: object = None) -> list[str]:
        await asyncio.sleep(0)
        return [f"text {i} of {self.selector} without {arg}" for i in (1, 2)]


def _describe(files: Sequence[str] | Sequence[FilePayload]) -> list[str]:
    return [
        f if isinstance(f, str) else f"{f['name']} {f['mimeType']} {f['buffer']!r}"
        for f in files
    ]


@dataclass
class FakeDownload:
    suggested_filename: str
    calls: list[Call]

    async def save_as(self, path: str) -> None:
        await asyncio.sleep(0)
        self.calls.append(("save_as", path))


@dataclass
class FakeFileChooser:
    calls: list[Call]

    async def set_files(
        self,
        files: Sequence[str] | Sequence[FilePayload],
        *,
        timeout: float | None = None,
    ) -> None:
        await asyncio.sleep(0)
        self.calls.append(_timed(("set_files", *_describe(files)), timeout))


@dataclass
class FakeEventInfo[T]:
    event: T

    @property
    async def value(self) -> T:
        await asyncio.sleep(0)
        return self.event


@dataclass
class FakeExpectEvent[T]:
    """``expect_<name>()``: records entering and leaving, then yields ``event``."""

    name: str
    event: T
    calls: list[Call]
    timeout: float | None = None

    async def __aenter__(self) -> FakeEventInfo[T]:
        self.calls.append(_timed((f"expect_{self.name}",), self.timeout))
        return FakeEventInfo(self.event)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.calls.append((f"{self.name} started",))


@dataclass
class FakePage:
    """Records what steps do to it instead of driving a real browser.

    Every call yields to the event loop, like real I/O, so concurrent runs
    would visibly interleave if the lock did not serialize them. Selectors in
    ``missing`` match nothing, so steps on them time out.
    """

    calls: list[Call] = field(default_factory=list)
    url: str = "about:blank"
    missing: set[str] = field(default_factory=set)

    async def goto(
        self, url: str, *, wait_until: str, timeout: float | None = None
    ) -> None:
        await asyncio.sleep(0)
        self.url = url
        self.calls.append(_timed(("goto", url, wait_until), timeout))

    async def wait_for_url(
        self, url: str, *, wait_until: str, timeout: float | None = None
    ) -> None:
        await asyncio.sleep(0)
        self.calls.append(_timed(("wait_for_url", url, wait_until), timeout))

    async def wait_for_timeout(self, timeout: float) -> None:
        await asyncio.sleep(0)
        self.calls.append(("wait_for_timeout", str(timeout)))

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(selector, self.calls, self.missing)

    def expect_download(
        self, *, timeout: float | None = None
    ) -> FakeExpectEvent[FakeDownload]:
        download = FakeDownload("report.csv", self.calls)
        return FakeExpectEvent("download", download, self.calls, timeout)

    def expect_file_chooser(
        self, *, timeout: float | None = None
    ) -> FakeExpectEvent[FakeFileChooser]:
        chooser = FakeFileChooser(self.calls)
        return FakeExpectEvent("file_chooser", chooser, self.calls, timeout)


@pytest.fixture
def fake_page() -> FakePage:
    return FakePage()


@pytest.fixture
def web_page(fake_page: FakePage) -> WebPage:
    return WebPage(fake_page)


@pytest.fixture
def write_yaml(tmp_path: Path) -> WriteYaml:
    """Write dedented YAML text to ``tmp_path / name`` and return its path."""

    def write(name: str, content: str) -> Path:
        path = tmp_path / name
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return write
