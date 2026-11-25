"""MCP 클라이언트에 정답을 보여 주지 않고 맡겨 두는 봉인 키.

서버는 상태를 저장하지 않으므로, 판정·코치에 필요한 숨은 값(결정 지점의 codeOption·conditions,
제약의 assumption·rationale)은 암호화해 클라이언트에게 들려 보낸다. 클라이언트(와 그 화면에서
도구 결과를 펼쳐 보는 사용자)는 키 안을 읽을 수 없고, 바꾸면 열리지 않는다.

Fernet(AES-128-CBC + HMAC-SHA256)이라 기밀성과 위변조 방지를 함께 얻고, 만든 시각이 들어 있어
만료(ttl)도 건다. 키 종류(kind)를 함께 넣어 제약 키를 결정 지점 키로 쓰지 못하게 한다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import zlib

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ValidationError

DEFAULT_TTL_SECONDS = 24 * 60 * 60


class SealError(ValueError):
    """키가 위조·손상됐거나, 만료됐거나, 다른 종류의 키다."""


class Sealer:
    def __init__(self, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        if not secret:
            raise ValueError("봉인 비밀값이 비어 있습니다.")
        # 어떤 문자열이든 받도록 SHA-256으로 Fernet 키(32바이트)를 만든다.
        digest = hashlib.sha256(f"ai-code-decoder/mcp-seal/{secret}".encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))
        self._ttl = ttl_seconds

    def seal(self, kind: str, value: BaseModel) -> str:
        payload = {"k": kind, "v": value.model_dump(mode="json", by_alias=True)}
        raw = zlib.compress(json.dumps(payload, ensure_ascii=False).encode())
        return self._fernet.encrypt(raw).decode()

    def open[T: BaseModel](self, kind: str, token: str, model: type[T]) -> T:
        try:
            raw = self._fernet.decrypt(token.encode(), ttl=self._ttl)
            payload = json.loads(zlib.decompress(raw))
        except (InvalidToken, zlib.error, ValueError, UnicodeError) as exc:
            raise SealError(kind) from exc
        if not isinstance(payload, dict) or payload.get("k") != kind:
            raise SealError(kind)
        try:
            return model.model_validate(payload.get("v"))
        except ValidationError as exc:
            raise SealError(kind) from exc
