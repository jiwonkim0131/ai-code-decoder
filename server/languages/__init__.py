"""언어 어댑터 등록표(BE-03). 허용 언어는 환경변수 `ALLOWED_LANGS`로 정한다."""

from __future__ import annotations

from server.config import get_settings
from server.languages.base import LanguageAdapter
from server.languages.python import PythonAdapter

DEFAULT_LANGUAGE = "python"

# 언어를 늘릴 때는 어댑터를 만들어 여기에 등록하고 ALLOWED_LANGS에 추가한다.
_ADAPTERS: dict[str, LanguageAdapter] = {
    PythonAdapter.id: PythonAdapter(),
}


class LanguageUnsupportedError(ValueError):
    def __init__(self, supported: list[str]) -> None:
        super().__init__("language_unsupported")
        self.supported = supported


def supported_languages() -> list[str]:
    """어댑터가 있고 ALLOWED_LANGS로 허용한 언어."""
    return [lang for lang in get_settings().allowed_langs if lang in _ADAPTERS]


def get_adapter(language: str = DEFAULT_LANGUAGE) -> LanguageAdapter:
    """허용 언어의 어댑터를 돌려준다. 허용하지 않거나 어댑터가 없으면 LanguageUnsupportedError."""
    key = language.strip().lower()
    supported = supported_languages()
    if key not in supported:
        raise LanguageUnsupportedError(supported)
    return _ADAPTERS[key]
