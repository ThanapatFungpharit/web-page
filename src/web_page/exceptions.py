from collections.abc import Collection, Iterable


class WebPageError(Exception):
    """Base class for every error raised by ``web_page``."""


# Manifest errors: a manifest file is missing, malformed or has bad values.
class BadManifest(WebPageError):
    """Raised when a manifest file cannot be read or fails validation."""


class BadActionFile(BadManifest):
    """Raised when an action manifest is invalid."""


class BadActionName(BadActionFile):
    """Raised when an action manifest's ``name`` is not a string."""


class BadSelectorsFile(BadManifest):
    """Raised when a selectors manifest is invalid."""


class BadSelectorsName(BadSelectorsFile):
    """Raised when a selectors manifest's ``name`` is not a string."""


# Registry errors: a name clashes with, or is missing from, what has been loaded.
class AlreadyLoaded(WebPageError):
    """Raised when loading a name that has already been loaded."""


class ActionAlreadyLoaded(AlreadyLoaded):
    def __init__(self, action_name: str) -> None:
        self.action_name = action_name
        super().__init__(f"Action {action_name!r} has already been loaded.")


class SelectorsAlreadyLoaded(AlreadyLoaded):
    def __init__(self, selectors_name: str) -> None:
        self.selectors_name = selectors_name
        super().__init__(f"Selectors {selectors_name!r} have already been loaded.")


class NotLoaded(WebPageError):
    """Raised when running something whose dependencies have not been loaded."""


class ActionNotLoaded(NotLoaded):
    def __init__(self, action_name: str, loaded: Iterable[str]) -> None:
        self.action_name = action_name
        self.loaded = tuple(sorted(loaded))
        super().__init__(
            f"Action {action_name!r} has not been loaded; loaded: {list(self.loaded)}."
        )


class SelectorsNotLoaded(NotLoaded):
    def __init__(self, action_name: str, selectors_name: str) -> None:
        self.action_name = action_name
        self.selectors_name = selectors_name
        super().__init__(
            f"Action {action_name!r} needs selectors {selectors_name!r}, "
            f"which have not been loaded."
        )


# Run errors: a loaded action cannot run with the given params or selectors.
class BadActionParams(WebPageError):
    def __init__(
        self,
        action_name: str,
        expected: Iterable[str],
        missing: Collection[str],
        unexpected: Collection[str],
    ) -> None:
        self.action_name = action_name
        self.expected = tuple(expected)
        self.missing = tuple(sorted(missing))
        self.unexpected = tuple(sorted(unexpected))
        super().__init__(
            f"Action {action_name!r} expects params {list(self.expected)}; "
            f"missing {list(self.missing)}, unexpected {list(self.unexpected)}."
        )


class BytesParamNotAllowed(WebPageError):
    def __init__(
        self, action_name: str, params: Collection[str], allowed: Iterable[str]
    ) -> None:
        self.action_name = action_name
        self.params = tuple(sorted(params))
        self.allowed = tuple(sorted(allowed))
        super().__init__(
            f"Action {action_name!r} got bytes for params {list(self.params)}; only "
            f"params used solely as upload data may be bytes: {list(self.allowed)}."
        )


class UnknownSelectorKey(WebPageError):
    def __init__(
        self,
        action_name: str,
        unknown: Collection[str],
        selectors_names: Iterable[str],
    ) -> None:
        self.action_name = action_name
        self.unknown = tuple(sorted(unknown))
        self.selectors_names = tuple(selectors_names)
        super().__init__(
            f"Action {action_name!r} uses selector keys {list(self.unknown)} not found "
            f"in selectors {list(self.selectors_names)}."
        )
