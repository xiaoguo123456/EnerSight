.PHONY: help install dev-server dev-miniapp test lint codegen check

help:
	@echo "install      安装全部依赖（pnpm + uv）"
	@echo "dev-server   启动 BFF，含 /docs"
	@echo "dev-miniapp  构建小程序到 dist/，用开发者工具导入"
	@echo "test         跑全部测试"
	@echo "lint         代码检查"
	@echo "preview      浏览器预览 H5（最快，不需要开发者工具）"
	@echo "codegen      Pydantic → openapi.json → core/types/generated.ts"
	@echo "check        codegen + lint + test，CI 用"

install:
	pnpm install
	cd packages/server && uv sync

dev-server:
	cd packages/server && uv run fastapi dev app/main.py

dev-miniapp:
	pnpm --filter @enersight/miniapp dev:weapp

# 浏览器预览：不需要微信开发者工具，也不需要 AppID
preview:
	pnpm --filter @enersight/miniapp build:h5
	@echo ""
	@echo "→ http://127.0.0.1:4173   （手机尺寸下看，Chrome 设备模拟 iPhone）"
	@cd packages/miniapp/dist/h5 && python3 -m http.server 4173

test:
	pnpm -r test
	cd packages/server && uv run pytest -q

lint:
	pnpm -r typecheck
	cd packages/server && uv run ruff check app tests

# 契约单向流动：服务端 Pydantic 模型是唯一来源，前端类型是生成产物。
# 见 docs/05 §1.3
codegen:
	cd packages/server && uv run python -m app.export_openapi
	pnpm exec openapi-typescript packages/server/openapi.json \
		-o packages/core/src/types/generated.ts

# CI 入口。codegen 后若工作区有改动说明类型未同步，直接失败
check: codegen
	@git diff --exit-code packages/core/src/types/generated.ts \
		|| (echo ""; echo "✗ 类型未同步，请跑 make codegen 并提交生成结果"; exit 1)
	$(MAKE) lint
	$(MAKE) test
