"""Native operation entry points use configured bindings, receipts, and versioned tags."""

import os
from pathlib import Path

from management_portal.test_framework import slots


def test_portal_operations_ui():
    from playwright.sync_api import expect, sync_playwright

    static = Path(os.environ.get("PORTAL_OPERATIONS_TEST_STATIC", Path(__file__).resolve().parents[2] / "management_portal" / "static"))
    fixtures = {
        name: (static / name).read_text()
        for name in (
            "portal_management_operations.js",
            "portal_management_resource_types.js",
            "portal_operations.js",
        )
        if (static / name).is_file()
    }
    lease = slots.acquire("browser", 1, stop=lambda: False)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("PORTAL_CHROMIUM_EXECUTABLE"))
            page = browser.new_page()
            page.set_default_timeout(5000)
            page.route("http://portal.test/**", lambda route: route.fulfill(
                status=200 if route.request.url.endswith("/") or route.request.url.rsplit("/", 1)[-1] in fixtures else 404,
                content_type="text/html" if route.request.url.endswith("/") else "text/javascript",
                body=fixtures.get(route.request.url.rsplit("/", 1)[-1], '<main><div id="ops"></div><div id="types"></div></main>'),
            ))
            page.goto("http://portal.test/")
            page.evaluate("""async () => {
              const text = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ""), "");
              window.PortalI18n = {t: String, locale: () => "zh", html: text, text};
              window.PortalUI = {confirm: async () => window.confirmNext};
              window.PortalUIPrimitives = {escapeHtml: String};
              window.confirmNext = true;
              window.calls = [];
              window.PortalAPI = {get: async path => {
                const url = new URL(path, location.origin);
                if (url.pathname === "/api/management/knowledge-tags") {
                  return {resource_id: "knowledge_tags_fixture", can_update: true};
                }
                if (url.pathname === "/api/portal-operations") {
                  const operation = url.searchParams.get("operation");
                  return {lineage_id: "lineage_fixture", path: "/fixture/" + operation + ".py", runtime: "python"};
                }
                throw Error(path);
              }};
              const runs = new Map();
              window.conflict = true;
              window.PortalInstructionClient = {execute: async (name, args, options) => {
                calls.push({name, args, expectedVersion: options?.expectedVersion});
                switch (name) {
                  case "ListResourceTypes": return {types: []};
                  case "ReadResource": return {text: "[]", version: window.conflict ? "v1" : "v2"};
                  case "UpdateResource":
                    if (window.conflict) throw Object.assign(Error("版本已改变"), {code: "instruction.version_conflict"});
                    return {version: "v3"};
                  case "RunScript": {
                    const id = "script_run_" + runs.size;
                    runs.set(id, args);
                    return {script_run_id: id, status: "running"};
                  }
                  case "GetScriptRun": {
                    const run = runs.get(args.script_run_id);
                    if (run.arguments.step === "apply") {
                      return {status: "failed", outputs: {error: "决策消息未核验"}};
                    }
                    return {status: "succeeded", outputs: run.arguments.step
                      ? {mode: "dry_run"} : {health: {status: "pending"}}};
                  }
                  default: throw Error(name);
                }
              }};
              const types = await import("/static/portal_management_resource_types.js");
              await types.mount(document.querySelector("#types"), {me: {access: {allows: {}}}});
            }""")

            page.get_by_text("知识标签", exact=True).click(timeout=2000)
            tags = page.get_by_role("textbox", name="标签列表")
            expect(tags).to_have_value("[]")
            draft = '[{"id":"article","description":"Example","folder":"article"}]'
            tags.fill(draft)
            page.get_by_role("button", name="保存").click()
            expect(page.get_by_role("status").filter(has_text="版本已改变")).to_be_visible()
            expect(tags).to_have_value(draft)
            expect(page.get_by_role("button", name="保存")).to_be_disabled()
            page.evaluate("window.conflict = false")
            page.get_by_role("button", name="重新读取").click()
            expect(tags).to_have_value("[]")
            tags.fill(draft)
            page.get_by_role("button", name="保存").click()
            expect(page.get_by_role("status").filter(has_text="已保存知识标签")).to_be_visible()
            writes = page.evaluate("calls.filter(call => call.name === 'UpdateResource')")
            assert [item["expectedVersion"] for item in writes] == ["v1", "v2"]
            assert writes[-1]["args"] == {"resource_id": "knowledge_tags_fixture", "changes": {"content": draft}}

            page.evaluate("""async () => {
              const ops = await import("/static/portal_management_operations.js");
              ops.mountOperations(document.querySelector("#ops"), new AbortController().signal);
            }""")
            page.get_by_text("发布 Portal", exact=True).click()
            publish = page.locator('[data-operation="publish_release"]')
            publish.get_by_role("textbox", name="提交说明").fill("Publish selected files")
            publish.get_by_role("textbox", name="发布路径（相对 code/，每行一条）").fill("a.py\nb.css")
            page.evaluate("window.confirmNext = false")
            publish.get_by_role("button", name="发布", exact=True).click()
            assert page.evaluate("calls.filter(call => call.name === 'RunScript').length") == 0
            page.evaluate("window.confirmNext = true")
            publish.get_by_role("button", name="发布", exact=True).click()
            expect(publish.get_by_role("status")).to_contain_text("等待健康检查")

            page.get_by_text("恢复 Article", exact=True).click()
            article = page.locator('[data-operation="article_restore"]')
            article.get_by_role("textbox", name="证据参数（JSON 对象）").fill('{"article_id":"article_fixture"}')
            article.get_by_role("button", name="运行", exact=True).click()
            expect(article.locator("[data-run-output]")).to_contain_text("dry_run")
            article.get_by_role("combobox").select_option("apply")
            article.get_by_role("button", name="运行", exact=True).click()
            expect(article.get_by_role("status")).to_contain_text("决策消息未核验")
            requests = page.evaluate("calls.filter(call => ['RunScript', 'GetScriptRun'].includes(call.name))")
            for submitted, read in zip(requests[::2], requests[1::2], strict=True):
                assert submitted["name"] == "RunScript" and read["name"] == "GetScriptRun"
                assert submitted["args"]["lineage_id"] == read["args"]["lineage_id"] == "lineage_fixture"
                assert submitted["args"]["runtime"] == "python"
            assert requests[0]["args"]["arguments"] == {"message": "Publish selected files", "paths": ["a.py", "b.css"]}
            assert [requests[i]["args"]["arguments"]["step"] for i in (2, 4)] == ["check", "apply"]
    finally:
        lease.release()


def test_colab_device_uses_configured_run_script():
    import re

    from playwright.sync_api import expect, sync_playwright

    portal = Path(__file__).resolve().parents[2] / "management_portal"
    html = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>", "", (portal / "resource_catalog.html").read_text(), flags=re.S)
    scripts = {
        name: (portal / "static" / name).read_text()
        for name in ("resource_catalog.js", "portal_operations.js")
    }
    lease = slots.acquire("browser", 1, stop=lambda: False)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("PORTAL_CHROMIUM_EXECUTABLE"))
            page = browser.new_page()
            page.set_default_timeout(5000)
            def route_fixture(route):
                filename = route.request.url.rsplit("/", 1)[-1]
                route.fulfill(content_type="text/javascript" if filename in scripts else "text/html",
                              body=scripts.get(filename, html))
            page.route("http://portal.test/**", route_fixture)
            page.goto("http://portal.test/resource/device?item=device_fixture")
            page.evaluate("""() => {
              const text = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ""), "");
              window.PortalI18n = {t: String, locale: () => "zh", html: text, text};
              window.PortalUIPrimitives = {escapeHtml: String, objectOf: value => value || {},
                randomUUID: () => "fixture", asyncStateHtml: value => value.message};
              window.PortalUI = {createDialogController: () => ({}),
                toast: message => { throw Error(message); }, confirm: async () => true};
              window.PortalSite = {setTitle() {}};
              window.PortalFormControls = {bindFilterDropdown: () => ({open() {}, close() {}})};
              window.PortalComponentRecovery = {create: () => ({guard: fn => fn(), run() {}})};
              window.PortalResourceDetail = {
                createDetailHistory: () => ({acceptCurrent() {}}),
                createFrameHost: () => ({snapshot: () => ({status: "empty"})}),
                createLocalHost: options => ({
                  snapshot: () => ({status: "empty"}),
                  async open(record) {
                    options.onLoading(record);
                    options.present(await options.load(record));
                    options.modal.hidden = false;
                    options.modal.setAttribute("aria-hidden", "false");
                  },
                }),
              };
              const attributes = {device_kind: "google_colab"};
              window.PortalAPI = {get: async path => {
                if (path.includes("resource-index/resources/")) {
                  return {resource_id: "device_fixture", resource_type: "device", attributes};
                }
                if (path.includes("/resources/resolve")) {
                  return {device_id: "device_fixture", record: attributes};
                }
                if (path === "/api/portal-operations?operation=colab_session") {
                  return {lineage_id: "lineage_ops", path: "/fixture/colab_session.py", runtime: "python"};
                }
                throw Error(path);
              }};
              window.calls = [];
              window.PortalInstructionClient = {execute: async (name, args) => {
                calls.push({name, args});
                if (name === "GetDeviceStatus") return {devices: []};
                if (name === "RunScript") return {script_run_id: "script_run_fixture", status: "running"};
                if (name === "GetScriptRun") {
                  const last = calls.findLast(call => call.name === "RunScript");
                  return {status: "succeeded", outputs: {running: last.args.arguments.running}};
                }
                throw Error(name);
              }};
            }""")
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.add_script_tag(url="http://portal.test/static/resource_catalog.js")
            assert not page_errors, page_errors
            page.evaluate("dispatchEvent(new PopStateEvent('popstate'))")
            page.wait_for_function("calls.length || document.querySelector('[data-colab-session]')")
            assert not page_errors, page_errors
            colab = page.locator("[data-colab-session]")
            colab.get_by_role("combobox", name="硬件").select_option("A100")
            colab.get_by_role("button", name="启动会话").click()
            expect(colab.get_by_role("status")).to_have_text("会话已启动")
            colab.get_by_role("button", name="停止会话").click()
            expect(colab.get_by_role("status")).to_have_text("会话已停止")
            calls = page.evaluate("calls.filter(call => call.name === 'RunScript' || call.name === 'GetScriptRun')")
            assert calls[0]["args"] == {"lineage_id": "lineage_ops", "path": "/fixture/colab_session.py", "runtime": "python",
                                        "arguments": {"resource_id": "device_fixture", "running": True, "hardware": "A100"}}
            assert calls[2]["args"]["arguments"] == {"resource_id": "device_fixture", "running": False}
            assert calls[1]["args"] == calls[3]["args"] == {"lineage_id": "lineage_ops", "script_run_id": "script_run_fixture"}
    finally:
        lease.release()
