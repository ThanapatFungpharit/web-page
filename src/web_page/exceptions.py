from collections.abc import Collection, Iterable, Mapping


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


class HookAlreadyRegistered(AlreadyLoaded):
    def __init__(self, hook_name: str) -> None:
        self.hook_name = hook_name
        super().__init__(f"Hook {hook_name!r} has already been registered.")


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


class HookNotRegistered(NotLoaded):
    def __init__(self, action_name: str, unknown: Collection[str]) -> None:
        self.action_name = action_name
        self.unknown = tuple(sorted(unknown))
        super().__init__(
            f"Action {action_name!r} calls hooks {list(self.unknown)}, "
            f"which have not been registered."
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


class ListParamNotAllowed(WebPageError):
    def __init__(
        self, action_name: str, params: Collection[str], allowed: Iterable[str]
    ) -> None:
        self.action_name = action_name
        self.params = tuple(sorted(params))
        self.allowed = tuple(sorted(allowed))
        super().__init__(
            f"Action {action_name!r} got lists for params {list(self.params)}; only "
            f"params used solely as upload files may be lists: {list(self.allowed)}."
        )


class BadFilesParam(WebPageError):
    def __init__(self, action_name: str, param: str, item: object) -> None:
        self.action_name = action_name
        self.param = param
        self.item = item
        super().__init__(
            f"Action {action_name!r} got {item!r} in files param {param!r}; each "
            f"file should be a path or a dict of 'name', 'mimeType' and 'buffer'."
        )


class BadParamType(WebPageError):
    def __init__(self, action_name: str, param: str, value: object) -> None:
        self.action_name, self.param, self.value = action_name, param, value
        super().__init__(
            f"Action {action_name!r} got {type(value).__name__} for param {param!r}; "
            "expected a string, upload bytes or a list of files."
        )


class BadResultValue(WebPageError):
    def __init__(
        self, result: str, value: object, expected: str = "a string in a template"
    ) -> None:
        self.result, self.value = result, value
        super().__init__(f"Result {result!r} must be {expected}, not {value!r}.")


class UnknownSelectorParam(WebPageError):
    def __init__(
        self,
        action_name: str,
        selector_key: str,
        selector: str,
        unknown: Collection[str],
    ) -> None:
        self.action_name = action_name
        self.selector_key = selector_key
        self.selector = selector
        self.unknown = tuple(sorted(unknown))
        super().__init__(
            f"Selector {selector_key!r} ({selector!r}) uses params "
            f"{list(self.unknown)} that action {action_name!r} does not declare."
        )


class ActionFailed(WebPageError):
    """Raised by a ``fail`` step: the action found the page in a state it treats
    as an error. ``results`` holds what it had saved by then."""

    def __init__(self, message: str, results: Mapping[str, object]) -> None:
        self.message = message
        self.results = dict(results)
        super().__init__(message)


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
