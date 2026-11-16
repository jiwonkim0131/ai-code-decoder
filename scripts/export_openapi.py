"""FastAPI가 만드는 OpenAPI를 contract/openapi.json으로 내보낸다.

FE는 이 파일에서 타입을 생성한다: npx openapi-typescript contract/openapi.json -o src/api/schema.ts
`--check`를 주면 파일이 최신인지 확인만 하고, 다르면 종료 코드 1로 끝난다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "contract" / "openapi.json"


def build() -> str:
    # 개발 라우트는 계약에 넣지 않는다.
    os.environ["ENABLE_DEV_ROUTES"] = "false"
    sys.path.insert(0, str(ROOT))
    from app import create_app
    from server.config import get_settings

    get_settings.cache_clear()
    schema = create_app().openapi()
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    content = build()
    if "--check" in sys.argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            print(
                "contract/openapi.json이 최신이 아닙니다. scripts/export_openapi.py를 실행하세요."
            )
            return 1
        print("contract/openapi.json is up to date")
        return 0
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
