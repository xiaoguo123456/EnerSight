.PHONY: help install dev-server dev-miniapp test lint codegen check

help:
	@echo "install      安装全部依赖（pnpm + uv）"
	@echo "dev-server   启动 BFF，含 /docs"
	@echo "dev-miniapp  构建小程序到 dist/，用开发者工具导入"
	@echo "test         跑全部测试"
	@echo "lint         代码检查"
	@echo "preview      浏览器预览 H5（最快，不需要开发者工具）"
	@echo "preview-bg   后台预览，不占终端"
	@echo "shot         截图自检 PAGE=home，改完 UI 必跑（独立端口）"
	@echo "codegen      Pydantic → openapi.json → core/types/generated.ts"
	@echo "check        codegen + lint + test，CI 用"

install:
	pnpm install
	cd packages/server && uv sync

dev-server:
	cd packages/server && ENERSIGHT_DEBUG=true uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

dev-miniapp:
	pnpm --filter @enersight/miniapp dev:weapp

# 端口分工：4173 给人看（常驻），4174 给截图用（每次自起自清）
PREVIEW_PORT ?= 4173
SHOT_PORT    ?= 4174

# 页面截图自检：改完 UI 必须跑一遍看结果，不要凭想象交付
# 用法：make shot PAGE=home
# 独立端口，不会干掉 make preview 起的常驻服务
PAGE ?= home
# 预览/截图都连本机后端；要看线上换 API_BASE=https://...
API_BASE ?= http://127.0.0.1:8000
shot:
	TARO_APP_API_BASE=$(API_BASE) pnpm --filter @enersight/miniapp build:h5
	@(cd packages/miniapp/dist/h5 && python3 -m http.server $(SHOT_PORT) >/dev/null 2>&1 &) ; sleep 2
	@node packages/miniapp/scripts/screenshot.mjs \
		"http://127.0.0.1:$(SHOT_PORT)/#/pages/$(PAGE)/index" \
		"/tmp/enersight-$(PAGE).png" 1500
	@pkill -f "http.server $(SHOT_PORT)" 2>/dev/null || true
	@echo "→ /tmp/enersight-$(PAGE).png"

# 浏览器预览：不需要微信开发者工具，也不需要 AppID
# 前台运行，Ctrl+C 停止
preview:
	TARO_APP_API_BASE=$(API_BASE) pnpm --filter @enersight/miniapp build:h5
	@echo ""
	@echo "→ http://127.0.0.1:$(PREVIEW_PORT)   （Chrome 设备模拟切 iPhone 尺寸）"
	@echo ""
	@cd packages/miniapp/dist/h5 && python3 -m http.server $(PREVIEW_PORT) --bind 127.0.0.1

# 后台常驻预览，不占终端。停止：make preview-stop
preview-bg:
	TARO_APP_API_BASE=$(API_BASE) pnpm --filter @enersight/miniapp build:h5
	@pkill -f "http.server $(PREVIEW_PORT)" 2>/dev/null || true
	@(cd packages/miniapp/dist/h5 && nohup python3 -m http.server $(PREVIEW_PORT) --bind 127.0.0.1 \
		> /tmp/enersight-preview.log 2>&1 &) ; sleep 2
	@echo "→ http://127.0.0.1:$(PREVIEW_PORT)   （后台运行，make preview-stop 停止）"

preview-stop:
	@pkill -f "http.server $(PREVIEW_PORT)" 2>/dev/null && echo "已停止" || echo "没有在运行" 

test:
	pnpm -r test
	cd packages/server && uv run pytest -q
	bash deploy/tests/test-cleanup-images.sh

lint:
	pnpm -r typecheck
	cd packages/server && uv run ruff check app tests scripts

# 契约单向流动：服务端 Pydantic 模型是唯一来源，前端类型是生成产物。
# 见 docs/05 §1.3
codegen:
	cd packages/server && uv run python -m app.export_openapi
	pnpm exec openapi-typescript packages/server/openapi.json \
		-o packages/core/src/types/generated.ts

# CI 入口。跑一次 codegen，若生成结果与跑之前不同，说明有人改了 schema
# 却没重新生成 —— 直接失败。比对的是「跑前 vs 跑后」而非 git HEAD，
# 这样本地改完 schema、跑过 codegen 但尚未提交时也能通过。
GEN := packages/core/src/types/generated.ts
check:
	@cp $(GEN) /tmp/enersight-gen-before.ts 2>/dev/null || true
	@$(MAKE) codegen >/dev/null
	@cmp -s /tmp/enersight-gen-before.ts $(GEN) \
		|| (echo ""; echo "✗ 类型未同步：schema 变了但没跑 make codegen"; exit 1)
	$(MAKE) lint
	$(MAKE) test
