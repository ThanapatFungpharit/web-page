from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from web_page.protocols import Page

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

# Params
type ParamName = str
type ParamValue = str
type ParamNames = Sequence[ParamName]
type ParamValues = Mapping[ParamName, ParamValue]
type Template = str
"""``str.format`` template whose placeholders are param names."""
type UrlTemplate = Template
type PathTemplate = Template
"""Template that formats to a file path; a relative path is against the cwd."""

# Results
type ResultName = str
"""Name a retrieval step saves its value under, e.g. ``price``."""
type ResultValue = str | list[str] | None
"""``None`` only from ``get_attribute`` when the attribute is missing."""
type Results = dict[ResultName, ResultValue]


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Everything a step handler needs while an action is running."""

    page: Page
    sel: Selectors
    params: ParamValues
    results: Results = field(default_factory=dict)
    """Filled by retrieval steps as they run; returned by ``WebPage.run``."""


# Steps
type StepKind = str
"""Manifest name of a step, e.g. ``click``."""
type KeyName = str
"""Playwright key or chord, e.g. ``Enter`` or ``Control+A``."""
type Milliseconds = int | float
type AttributeName = str
"""HTML attribute name, e.g. ``href``."""


@dataclass(frozen=True, slots=True)
class Action:
    """A validated action loaded from a manifest."""

    selectors_name: tuple[SelectorsName, ...]
    params: tuple[ParamName, ...]
    steps: tuple[Step, ...]
