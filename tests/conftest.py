import pytest

_SDK_PATCH_TARGETS = [
    ("openai", "openai.resources.chat.completions", "Completions", "create"),
    ("openai", "openai.resources.chat.completions", "AsyncCompletions", "create"),
    ("anthropic", "anthropic.resources.messages", "Messages", "create"),
    ("anthropic", "anthropic.resources.messages", "AsyncMessages", "create"),
]

_PRISTINE: dict = {}


def _snapshot_pristine() -> None:
    for _provider, module_name, class_name, attr in _SDK_PATCH_TARGETS:
        try:
            module = __import__(module_name, fromlist=[class_name])
            target = getattr(module, class_name)
        except (ImportError, AttributeError):
            continue
        if hasattr(target, attr):
            _PRISTINE[(module_name, class_name, attr)] = getattr(target, attr)


def _restore_pristine() -> None:
    for (module_name, class_name, attr), original in _PRISTINE.items():
        try:
            module = __import__(module_name, fromlist=[class_name])
            target = getattr(module, class_name)
        except (ImportError, AttributeError):
            continue
        try:
            setattr(target, attr, original)
        except AttributeError:
            pass


@pytest.fixture(autouse=True)
def _restore_sdk_pristine():
    """Restore the pristine SDK methods before every test.

    Global ``instrument()`` calls in earlier tests/files (e.g. test_api.py)
    patch the shared ``openai``/``anthropic`` classes. Snapshot the original
    methods on first use and force-restore them before each test so one test
    cannot poison the class-level patches used by another.
    """
    if not _PRISTINE:
        _snapshot_pristine()
    _restore_pristine()
    yield
