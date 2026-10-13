from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from types import TracebackType

import pytest

from web_page import WebPage

type Call = tuple[str, ...]
type WriteYaml = Callable[..., Path]


@dataclass
class FakeLocator:
    selector: str
    calls: list[Call]

    async def click(self) -> None:
        await asyncio.sleep(0)
        self.calls.append(("click", self.selector))

    async def fill(self, value: str) -> None:
        await asyncio.sleep(0)
        self.calls.append(("fill", self.selector, value))

    async def wait_for(self, state: str) -> None:
        await asyncio.sleep(0)
        self.calls.append(("wait_for", self.selector, state))

    async def hover(self) -> None:
        await asyncio.sleep(0)
        self.calls.append(("hover", self.selector))

    async def check(self) -> None:
        await asyncio.sleep(0)
        self.calls.append(("check", self.selector))

    async def uncheck(self) -> None:
        await asyncio.sleep(0)
        self.calls.append(("uncheck", self.selector))

    async def select_option(self, value: str) -> None:
        await asyncio.sleep(0)
        self.calls.append(("select_option", self.selector, value))

    async def press(self, key: str) -> None:
        await asyncio.sleep(0)
        self.calls.append(("press", self.selector, key))

    async def inner_text(self) -> str:
        await asyncio.sleep(0)
        return f"text of {self.selector}"

    async def all_inner_texts(self) -> list[str]:
        await asyncio.sleep(0)
        return [f"text 1 of {self.selector}", f"text 2 of {self.selector}"]

    async def input_value(self) -> str:
        await asyncio.sleep(0)
        return f"value of {self.selector}"

    async def get_attribute(self, name: str) -> str | None:
        await asyncio.sleep(0)
        return None if name == "missing" else f"{name} of {self.selector}"

    async def set_input_files(self, files: Sequence[str]) -> None:
        await asyncio.sleep(0)
        self.calls.append(("set_input_files", self.selector, *files))


@dataclass
class FakeDownload:
    suggested_filename: str
    calls: list[Call]

    async def save_as(self, path: str) -> None:
        await asyncio.sleep(0)
        self.calls.append(("save_as", path))


@dataclass
class FakeDownloadInfo:
    download: FakeDownload

    @property
    async def value(self) -> FakeDownload:
        await asyncio.sleep(0)
        return self.download


@dataclass
class FakeExpectDownload:
    download: FakeDownload

    async def __aenter__(self) -> FakeDownloadInfo:
        self.download.calls.append(("expect_download",))
        return FakeDownloadInfo(self.download)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.download.calls.append(("downloaded",))


@dataclass
class FakePage:
    """Records what steps do to it instead of driving a real browser.

    Every call yields to the event loop, like real I/O, so concurrent runs
    would visibly interleave if the lock did not serialize them.
    """

    calls: list[Call] = field(default_factory=list)
    url: str = "about:blank"

    async def goto(self, url: str, wait_until: str) -> None:
        await asyncio.sleep(0)
        self.url = url
        self.calls.append(("goto", url, wait_until))

    async def wait_for_url(self, url: str, wait_until: str) -> None:
        await asyncio.sleep(0)
        self.calls.append(("wait_for_url", url, wait_until))

    async def wait_for_timeout(self, timeout: float) -> None:
        await asyncio.sleep(0)
        self.calls.append(("wait_for_timeout", str(timeout)))

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(selector, self.calls)

    def expect_download(self) -> FakeExpectDownload:
        return FakeExpectDownload(FakeDownload("report.csv", self.calls))


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
