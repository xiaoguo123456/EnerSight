"""导出 OpenAPI schema 供前端 codegen。

契约单向流动：Pydantic 模型 → openapi.json → core/types/generated.ts。
前端类型是生成产物，不手写。见 docs/05 §1.3
"""

import json
from pathlib import Path

from app.main import app


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "openapi.json"
    schema = app.openapi()
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    names = sorted(schema.get("components", {}).get("schemas", {}).keys())
    print(f"→ {out.relative_to(Path.cwd())}  ({len(names)} schemas)")


if __name__ == "__main__":
    main()
