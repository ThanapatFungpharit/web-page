from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import cast

import pytest
from playwright.async_api import Page

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


@dataclass
class FakePage:
    """Records what steps do to it instead of driving a real browser.

    Every call yields to the event loop, like real I/O, so concurrent runs
    would visibly interleave if the lock did not serialize them.
    """

    calls: list[Call] = field(default_factory=list)

    async def goto(self, url: str, wait_until: str) -> None:
        await asyncio.sleep(0)
        self.calls.append(("goto", url, wait_until))

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(selector, self.calls)


@pytest.fixture
def fake_page() -> FakePage:
    return FakePage()


@pytest.fixture
def web_page(fake_page: FakePage) -> WebPage:
    return WebPage(cast(Page, fake_page))


@pytest.fixture
def write_yaml(tmp_path: Path) -> WriteYaml:
    """Write dedented YAML text to ``tmp_path / name`` and return its path."""

    def write(name: str, content: str) -> Path:
        path = tmp_path / name
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return write
