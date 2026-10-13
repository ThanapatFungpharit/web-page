"""The page interface steps drive.

These are structural types: any async page object with these methods works,
so Playwright's ``Page`` fits as do drop-in replacements for it. Only the
calls the built-in steps make are listed; return values steps ignore are
typed ``object`` so implementations may return anything there.

Steps pass ``timeout`` only when a manifest sets one, so a page library that
doesn't take it still works for actions that never ask for one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from types import TracebackType
from typing import Literal, Protocol, TypedDict

type ElementState = Literal["visible", "hidden", "attached", "detached"]
type MouseButton = Literal["left", "right", "middle"]


class FilePayload(TypedDict):
    """An in-memory file for ``set_input_files``, shaped like Playwright's."""

    name: str
    mimeType: str
    buffer: bytes


class Locator(Protocol):
    async def click(
        self, *, button: MouseButton = ..., timeout: float | None = ...
    ) -> object: ...
    async def hover(self, *, timeout: float | None = ...) -> object: ...
    async def check(self, *, timeout: float | None = ...) -> object: ...
    async def uncheck(self, *, timeout: float | None = ...) -> object: ...
    async def wait_for(
        self, *, state: ElementState, timeout: float | None = ...
    ) -> object: ...
    async def fill(self, value: str, *, timeout: float | None = ...) -> object: ...
    async def select_option(
        self, value: str, *, timeout: float | None = ...
    ) -> object: ...
    async def press(self, key: str, *, timeout: float | None = ...) -> object: ...
    async def inner_text(self, *, timeout: float | None = ...) -> str: ...
    async def all_inner_texts(self) -> list[str]: ...
    async def input_value(self, *, timeout: float | None = ...) -> str: ...
    async def get_attribute(
        self, name: str, *, timeout: float | None = ...
    ) -> str | None: ...
    async def set_input_files(
        self,
        files: Sequence[str] | Sequence[FilePayload],
        *,
        timeout: float | None = ...,
    ) -> object: ...
    async def is_visible(self) -> bool: ...
    async def count(self) -> int: ...
    async def evaluate(
        self, expression: str, arg: object = ..., *, timeout: float | None = ...
    ) -> object: ...
    async def evaluate_all(self, expression: str, arg: object = ...) -> object: ...


class Keyboard(Protocol):
    async def press(self, key: str) -> object: ...


class Download(Protocol):
    @property
    def suggested_filename(self) -> str: ...

    async def save_as(self, path: str) -> object: ...


class FileChooser(Protocol):
    async def set_files(
        self,
        files: Sequence[str] | Sequence[FilePayload],
        *,
        timeout: float | None = ...,
    ) -> object: ...


class EventInfo[T](Protocol):
    """What ``async with page.expect_<event>() as info`` binds ``info`` to."""

    @property
    def value(self) -> Awaitable[T]: ...


class ExpectEvent[T](Protocol):
    async def __aenter__(self) -> EventInfo[T]: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> object: ...


class Page(Protocol):
    @property
    def url(self) -> str: ...
    @property
    def keyboard(self) -> Keyboard: ...

    async def goto(
        self,
        url: str,
        *,
        wait_until: Literal["domcontentloaded"],
        timeout: float | None = ...,
    ) -> object: ...
    async def wait_for_url(
        self,
        url: str,
        *,
        wait_until: Literal["domcontentloaded"],
        timeout: float | None = ...,
    ) -> object: ...
    async def wait_for_timeout(self, timeout: float) -> object: ...
    def locator(self, selector: str) -> Locator: ...
    def expect_download(
        self, *, timeout: float | None = ...
    ) -> ExpectEvent[Download]: ...
    def expect_file_chooser(
        self, *, timeout: float | None = ...
    ) -> ExpectEvent[FileChooser]: ...


def default_timeout_errors() -> tuple[type[BaseException], ...]:
    """The errors an ``optional`` step treats as "not there": Python's own
    ``TimeoutError``, plus Playwright's when Playwright is installed."""
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:
        return (TimeoutError,)
    return (TimeoutError, PlaywrightTimeoutError)
