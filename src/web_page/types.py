from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from web_page.protocols import FilePayload, Page, default_timeout_errors

if TYPE_CHECKING:
    from web_page.exceptions import BadManifest
    from web_page.steps import Step

# Raw input: untrusted YAML data, not yet validated.
type RawYaml = object
type RawManifest = dict[RawYaml, RawYaml]

# Manifests
type ManifestKind = Literal["Action", "Selectors"]
type ManifestPath = str | Path
type FileSuffix = str
"""Lowercase file extension including the dot, e.g. ``.yaml``."""
type ManifestKey = str
type ManifestError = type[BadManifest]
type FieldLabel = str
"""Human-readable name of a manifest field, used in error messages."""

# Names
type ActionName = str
type SelectorsName = str

# Selectors
type SelectorKey = str
"""Name a step uses to refer to a selector, e.g. ``submit``."""
type CssSelector = str
"""Playwright selector string, e.g. ``button[type=submit]``."""
type Selectors = Mapping[SelectorKey, CssSelector]
"""In a manifest the selectors are templates; while an action runs, formatted."""

# Params
type ParamName = str
type ParamValue = str | bytes | list[str | FilePayload]
"""``bytes`` only for params an ``upload`` step reads as ``data``, and a list only
for params it reads as ``{param: <name>}`` files."""
type ParamNames = Sequence[ParamName]
type ParamValues = Mapping[ParamName, ParamValue]
type Template = str
"""Text whose ``{name}`` placeholders use params and ``{result.name}`` use saved
results; ``{{`` and ``}}`` are literal braces. Every value a step sends to the page or the file
system is one, and so is every selector; names (selector keys, params,
results, hooks, attributes) never are."""
type UrlTemplate = Template
type KeyTemplate = Template
"""Formats to a Playwright key or chord, e.g. ``Enter`` or ``Control+A``."""
type PathTemplate = Template
"""Template that formats to a file path; a relative path is against the cwd."""

# Results
type ResultName = str
"""Name a retrieval step saves its value under, e.g. ``price``."""
type ResultValue = str | list[str] | None
"""``None`` represents a missing attribute, skipped output or explicit hook value."""
type Results = dict[ResultName, ResultValue]
type ActionRunner = Callable[[ActionName, ParamValues], Awaitable[Results]]


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Everything a step handler, or a ``call`` hook, needs while an action runs."""

    page: Page
    sel: Selectors
    """Selector keys to selectors, resolved against current params and results."""
    params: ParamValues
    results: Results = field(default_factory=dict)
    """Filled by retrieval steps as they run; returned by ``WebPage.run``."""
    hooks: Mapping[HookName, Hook] = field(default_factory=dict)
    run_action: ActionRunner | None = None
    timeout_errors: tuple[type[BaseException], ...] = field(
        default_factory=default_timeout_errors
    )
    """What an ``optional`` step or a waited-for condition treats as "not there"."""


# Hooks
type HookName = str
"""Name a ``call`` step uses to refer to a registered hook."""
type Hook = Callable[[ActionContext], Awaitable[object]]
"""Async function a ``call`` step runs, for logic a manifest can't express."""


# Steps
type StepKind = str
"""Manifest name of a step, e.g. ``click``."""
type Milliseconds = int | float
type AttributeName = str
"""HTML attribute name, e.g. ``href``."""


@dataclass(frozen=True, slots=True)
class Action:
    """A validated action loaded from a manifest."""

    selectors_name: tuple[SelectorsName, ...]
    params: tuple[ParamName, ...]
    steps: tuple[Step, ...]
    bytes_params: frozenset[ParamName] = frozenset()
    """Params that may be given as ``bytes``: read as data, never formatted."""
    list_params: frozenset[ParamName] = frozenset()
    """Params that may be given as a list of files: never formatted."""
