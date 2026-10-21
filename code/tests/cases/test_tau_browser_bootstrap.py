"""The shared TAU browser bootstrap opens one authenticated browser and leaves receipts (decision_c953eab654cd)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from management_portal.test_framework.catalog import ROOT

BOOTSTRAP = ROOT / "management_portal/test_framework/tau_browser.js"


class _Portal(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == '/lifecycle':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(self.headers.get('X-Portal-Resource-Lifecycle', '').encode())
            return
        cookie = self.headers.get("Cookie", "")
        user = cookie.split("db_session=", 1)[1].split(";", 1)[0] if "db_session=" in cookie else ""
        if user == "identity-cookie-secret":
            self.close_connection = True
            return
        build = cookie.split("fixture_build=", 1)[1].split(";", 1)[0] if "fixture_build=" in cookie else "dev-fixture"
        body = json.dumps({"username": user, "build_id": build} if user else {}).encode()
        self.send_response(200 if user else 401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - http.server API
        # GetInputState: the message runs for two reads, then completes.
        call = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if call["instruction"] == "Disconnect":
            self.close_connection = True
            return
        if call["instruction"] == "Rejected":
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "failed", "error": {
                "code": "authorization.denied", "message": "private-error-message",
                "details": {"secret": "private-error-details"}}}).encode())
            return
        reads = type(self).input_reads = getattr(type(self), "input_reads", 0) + 1
        state = "running" if reads <= 2 else "completed"
        body = json.dumps({"status": "committed", "result": {"input_state": {
            "message_id": call["arguments"]["message_id"], "state": state}}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return None


@pytest.fixture
def portal():
    _Portal.input_reads = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Portal)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _open(tmp_path: Path, origin: str, user: str, body: str = "") -> subprocess.CompletedProcess:
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"cookies": [{
        "name": "db_session", "value": user, "domain": "127.0.0.1", "path": "/",
        "expires": -1, "httpOnly": True, "secure": False, "sameSite": "Lax"}], "origins": []}))
    script = (
        f"const {{ openPortal }} = require({json.dumps(str(BOOTSTRAP))});"
        f"(async () => {{ const p = await openPortal({{ evidenceDir: {json.dumps(str(evidence))},"
        f" storageState: {json.dumps(str(state))}, origin: {json.dumps(origin)} }});"
        " try { console.log(JSON.stringify({ actor: p.actor, build: p.buildId, title: await p.page.title() }));"
        f" {body} }}"
        " finally { await p.close(); } })().catch(e => { console.error(e.message); process.exit(3); });"
    )
    return subprocess.run(["node", "-e", script], cwd=evidence, capture_output=True, text=True, timeout=120)


def test_the_bootstrap_opens_the_portal_test_identity_and_records_its_lifecycle(tmp_path, portal):
    """TAU 浏览器以 test-actor 身份启动，记录启动与关闭并删除临时配置目录

    :module: spec.design-governance"""
    result = _open(tmp_path, portal, "test-actor")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["actor"] == "test-actor"
    launch = json.loads((tmp_path / "evidence/tau-browser-launch.json").read_text())
    closed = json.loads((tmp_path / "evidence/tau-browser-close.json").read_text())
    assert launch["ok"] is True and launch["build_id"] == "dev-fixture"
    assert closed["ok"] is True and not Path(launch["profile_root"]).exists()


def test_framework_browser_runtime_survives_a_helpers_different_home(tmp_path, portal, monkeypatch):
    """TAU 子进程使用框架登记的 Python 和浏览器目录，不依赖 Resident 的 HOME。"""
    if sys.platform == "win32":
        cache = Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"
    elif sys.platform == "darwin":
        cache = Path.home() / "Library/Caches/ms-playwright"
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "ms-playwright"
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    context = {"resource_lifecycle_id": "e" * 64, "browser_runtime": {
        "python_executable": sys.executable, "browsers_path": browsers}}
    (evidence / "run-context.json").write_text(json.dumps(context))
    monkeypatch.setenv("HOME", str(tmp_path / "another-home"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "absent-browser-cache"))

    result = _open(tmp_path, portal, "test-actor")

    assert result.returncode == 0, result.stderr
    launch = json.loads((evidence / "tau-browser-launch.json").read_text())
    assert launch["ok"] and launch["actor"] == "test-actor"
    assert not (tmp_path / "absent-browser-cache").exists()
    assert json.loads((evidence / "run-context.json").read_text())["browser_runtime"] == context["browser_runtime"]


def test_the_bootstrap_refuses_a_browser_with_another_identity(tmp_path, portal):
    """浏览器身份不是 test-actor 时 TAU 启动失败并清理配置目录

    :module: spec.design-governance"""
    result = _open(tmp_path, portal, "someone-else")

    assert result.returncode == 3
    assert "expected test-actor" in result.stderr
    launch = json.loads((tmp_path / "evidence/tau-browser-launch.json").read_text())
    assert launch["ok"] is False and not Path(launch["profile_root"]).exists()


def test_the_bootstrap_records_build_change_before_exposing_the_first_page(tmp_path, portal):
    """TAU 浏览器在打开第一个页面之前记录构建版本的变化"""
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    context = {'resource_lifecycle_id': 'b' * 64, 'build_id': 'dev-original', 'case_id': 'test-original'}
    (evidence / 'run-context.json').write_text(json.dumps(context))
    result = _open(tmp_path, portal, 'test-actor', body=(
        "const fs = require('fs');"
        " const before = JSON.parse(fs.readFileSync('run-context.json', 'utf8'));"
        " console.log(JSON.stringify({context:before, url:p.page.url()}));"
        " await p.page.goto('/lifecycle');"
    ))
    assert result.returncode == 0, result.stderr
    before = json.loads(result.stdout.strip().splitlines()[-1])
    assert before['url'] == 'about:blank'
    assert before['context']['build_id'] == 'dev-original'
    assert before['context']['case_id'] == 'test-original'
    observation, = before['context']['build_observations']
    assert observation['build_id'] == 'dev-fixture'
    assert observation['source'] == 'authenticated_browser_launch'
    launch = json.loads((evidence / 'tau-browser-launch.json').read_text())
    assert observation['observed_at'] == launch['observed_at']
    assert observation['recorded_at'] <= launch['written_at']
    assert launch['expected_build_id'] == 'dev-original'


def test_browser_and_api_requests_share_the_framework_resource_lifecycle(tmp_path, portal):
    """TAU 浏览器的页面请求和 API 请求带同一个资源生命周期 ID"""
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    lifecycle = 'a' * 64
    (evidence / 'run-context.json').write_text(json.dumps({'resource_lifecycle_id': lifecycle}))
    result = _open(tmp_path, portal, 'test-actor', body=(
        "const api = await p.context.request.get('/lifecycle');"
        " const page = await p.page.goto('/lifecycle');"
        " console.log(JSON.stringify({api:await api.text(),page:await page.text()}));"
    ))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {'api': lifecycle, 'page': lifecycle}


@pytest.mark.parametrize("actor", ["test-actor", "someone-else"])
def test_identity_recheck_records_the_actual_reloaded_generation_and_rejects_another_actor(tmp_path, portal, actor):
    """页面重载后身份复查记录实际构建版本，登录身份换成他人时失败"""
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'run-context.json').write_text(json.dumps({
        'resource_lifecycle_id': 'c' * 64, 'build_id': 'dev-fixture'}))
    result = _open(tmp_path, portal, 'test-actor', body=(
        "await p.page.goto('/before');"
        f" await p.context.addCookies([{{name:'fixture_build',value:'dev-reloaded',url:{json.dumps(portal)}}},"
        f"{{name:'db_session',value:{json.dumps(actor)},url:{json.dumps(portal)}}}]);"
        " await p.page.reload(); await p.verifyIdentity();"
    ))
    assert result.returncode == (0 if actor == 'test-actor' else 3), result.stderr
    records = [json.loads(line) for line in (evidence / 'tau-browser-identity.jsonl').read_text().splitlines()]
    initial, recheck = records
    assert initial['build_id'] == 'dev-fixture'
    assert initial['call_id'] != recheck['call_id']
    assert recheck['source'] == 'explicit_recovery'
    assert recheck['actor'] == actor and recheck['build_id'] == 'dev-reloaded'
    assert recheck['me_status'] == 200 and recheck['ok'] == (actor == 'test-actor')
    assert recheck['started_at'] <= recheck['observed_at'] <= recheck['finished_at']
    assert recheck['duration_ms'] > 0
    original = json.loads((evidence / 'run-context.json').read_text())
    assert original['build_id'] == 'dev-fixture'
    if actor == 'test-actor':
        assert original['build_observations'][-1]['build_id'] == 'dev-reloaded'
    else:
        assert 'build_observations' not in original


def test_identity_recheck_records_return_to_the_initial_build_without_duplicate_observations(tmp_path, portal):
    """身份复查时构建切回初始版本也被记录，同一构建不重复记录"""
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'run-context.json').write_text(json.dumps({
        'resource_lifecycle_id': 'd' * 64, 'build_id': 'dev-fixture'}))
    result = _open(tmp_path, portal, 'test-actor', body=(
        f"await p.context.addCookies([{{name:'fixture_build',value:'dev-reloaded',url:{json.dumps(portal)}}}]);"
        " await p.verifyIdentity(); await p.verifyIdentity();"
        f" await p.context.addCookies([{{name:'fixture_build',value:'dev-fixture',url:{json.dumps(portal)}}}]);"
        " await p.verifyIdentity();"
    ))
    assert result.returncode == 0, result.stderr
    context = json.loads((evidence / 'run-context.json').read_text())
    assert context['build_id'] == 'dev-fixture'
    assert [r['build_id'] for r in context['build_observations']] == ['dev-reloaded', 'dev-fixture']
    assert len((evidence / 'tau-browser-identity.jsonl').read_text().splitlines()) == 4


def test_instruction_failure_receipt_preserves_status_and_code_without_private_details(tmp_path, portal):
    """TAU 指令失败记录保留 HTTP 状态和错误码，不含私密错误详情"""
    result = _open(tmp_path, portal, 'test-actor', body=(
        "try { await p.instruction('Rejected'); } catch (_) {}"
    ))
    assert result.returncode == 0, result.stderr
    text = (tmp_path / 'evidence/tau-helper-calls.jsonl').read_text()
    records = [json.loads(line) for line in text.splitlines()]
    assert records[-1]['status'] == 'failed'
    assert records[-1]['error']['code'] == 'authorization.denied'
    assert records[-1]['error']['http_status'] == 403
    assert 'private-error' not in text and 'secret' not in text


def test_a_framework_case_cannot_silently_open_an_untracked_browser(tmp_path, portal):
    """运行上下文缺少 resource_lifecycle_id 时，TAU 浏览器拒绝启动"""
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'run-context.json').write_text('{}')
    result = _open(tmp_path, portal, 'test-actor')
    assert result.returncode == 3
    assert 'missing resource_lifecycle_id' in result.stderr
    assert not (evidence / 'tau-browser-host.json').exists()


@pytest.mark.parametrize("stage", ["identity", "instruction"])
def test_api_transport_failure_never_exposes_authenticated_call_logs(tmp_path, portal, stage):
    """TAU API 传输异常不暴露认证调用日志

    身份读取和指令传输断开时只返回安全诊断；异常输出与启动回执不包含 Cookie 或库调用日志。

    :design: decision_c953eab654cd
    """
    body = (
        f"await p.context.addCookies([{{name:'fixture_secret',value:'instruction-cookie-secret',url:{json.dumps(portal)}}}]);"
        " try { await p.instruction('Disconnect'); } catch(error) {"
        " console.error(error); console.error(JSON.stringify(error)); throw error; }"
    ) if stage == "instruction" else ""
    result = _open(tmp_path, portal, "identity-cookie-secret" if stage == "identity" else "test-actor", body)

    assert result.returncode == 3
    assert f"TAU {stage} transport failed" in result.stderr
    receipts = "".join(p.read_text() for p in (tmp_path / "evidence").glob("tau-browser-*.json"))
    helper_log = tmp_path / 'evidence/tau-helper-calls.jsonl'
    if stage == 'instruction':
        rows = [json.loads(line) for line in helper_log.read_text().splitlines()]
        assert len(rows) == 2
        assert rows[0]['call_id'] == rows[1]['call_id']
        assert rows[1]['status'] == 'failed'
        assert rows[1]['duration_ms'] > 0
        assert rows[1]['error']['code'] == 'tau.transport_failed'
        assert rows[1]['error']['http_status'] is None
        receipts += helper_log.read_text()
    for secret in ("identity-cookie-secret", "instruction-cookie-secret", "db_session", "Cookie:", "Call log:"):
        assert secret not in result.stdout + result.stderr + receipts


def test_the_bootstrap_waits_for_one_message_by_its_own_input_state(tmp_path, portal):
    """TAU 共享启动器按消息 ID 读取并等待这条消息自己的处理状态

    portal.inputState / waitForInput 以浏览器的 test-actor 会话调用 GetInputState，按消息 ID 判断执行中、完成或取消，不从页面事件推断。

    :module: spec.design-governance
    :design: decision_c953eab654cd
    """
    result = _open(tmp_path, portal, "test-actor", body=(
        "const first = await p.inputState('resident_111111111111', 'msg_a');"
        " const done = await p.waitForInput('resident_111111111111', 'msg_a', { intervalMs: 50 });"
        " await Promise.all(['msg_b', 'msg_c'].map(id => p.inputState('resident_111111111111', id)));"
        " console.log(JSON.stringify({ first: first.state, done: done.state }));"
    ))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"first": "running", "done": "completed"}
    text = (tmp_path / 'evidence/tau-helper-calls.jsonl').read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    starts = {r['call_id']: r for r in rows if r['kind'] == 'call_started'}
    ends = {r['call_id']: r for r in rows if r['kind'] == 'call_finished'}
    assert len(starts) == len(ends) == 5
    assert starts.keys() == ends.keys()
    assert all(r['tool'] == 'GetInputState' for r in rows)
    assert all(r['status'] == 'completed' and r['duration_ms'] > 0 for r in ends.values())
    assert all(starts[key]['at'] <= ends[key]['at'] for key in starts)
    assert 'resident_111111111111' not in text and 'msg_a' not in text


@pytest.mark.parametrize("scenario", ["summary", "reloaded_detail", "completed_only", "other_page", "other_page_closed_default", "tool_summary", "tool_detail", "completed_tool"])
def test_the_bootstrap_waits_for_a_running_tool_in_the_live_chat_card(tmp_path, portal, scenario):
    """TAU 共享启动器等到 Chat 实时活动摘要出现正在运行的工具

    portal.waitForRunningActivity 在指定 Chat 观察工具运行摘要或展开后的 running 工具条目；覆盖文档重载后重新展开、同页其他 Chat 隔离与 completed 条目不触发 Stop。

    :module: operations.test-workbench
    :aspects: behavior
    :design: decision_c953eab654cd
    """
    body = (
        "await p.page.setContent('<article data-message-key=\"__runtime__\"><details><summary>Codex 活动</summary></details></article>');"
        " await p.page.evaluate(() => setTimeout(() => { document.querySelector('summary').textContent ="
        " 'Codex 活动 · Command started /bin/bash -lc sleep 180'; }, 300));"
        " const running = await p.waitForRunningActivity({ timeoutMs: 10000 });"
        " console.log(JSON.stringify({ text: running.text, seen: Boolean(running.seen_at) }));"
    )
    expected = {"text": "Codex 活动 · Command started /bin/bash -lc sleep 180", "seen": True}
    if scenario == "tool_summary":
        body = body.replace("Command started /bin/bash -lc sleep 180", "Tool: clock.sleep")
        expected["text"] = "Codex 活动 · Tool: clock.sleep"
    if scenario not in {"summary", "tool_summary"}:
        # The sidebar's other Chat must not satisfy this Chat's Stop prerequisite.
        other = '<aside><article data-message-key="__runtime__"><details><summary>Command started WRONG CHAT</summary></details></article></aside>'
        card = '<section id="target"><article data-message-key="__runtime__"><details><summary>Agent 活动 · Execution service started</summary>{}</details></article></section>'
        completed = '<div class="runtimeDetailItem"><div class="runtimeDetailKind">Command started</div><div class="runtimeDetailText">OLD TOOL<div class="runtimeDetailMeta">completed</div></div></div>'
        running = '<div class="runtimeDetailItem"><div class="runtimeDetailKind">Command started</div><div class="runtimeDetailText">TARGET TOOL<div class="runtimeDetailMeta">running · now</div></div></div>'
        if scenario in {"tool_detail", "completed_tool"}:
            completed = completed.replace("Command started", "Tool: clock.sleep")
            running = running.replace("Command started", "Tool: clock.sleep")
        body = f"await p.page.setContent({json.dumps(other + card.format(completed))});"
        if scenario in {"reloaded_detail", "tool_detail"}:
            body += (
                f"await p.page.route('**/reloaded-chat', route => route.fulfill({{contentType:'text/html',body:{json.dumps(other + card.format(completed + running))}}}));"
                " const reload = (async () => {"
                " await p.page.locator('#target details[open]').waitFor({timeout:5000});"
                f" await p.page.goto({json.dumps(portal + '/reloaded-chat')}); }})();"
                " const [result] = await Promise.all([p.waitForRunningActivity({scope:p.page.locator('#target'),timeoutMs:10000}),reload]);"
                " console.log(JSON.stringify({target:result.text.includes('TARGET TOOL'),wrong:result.text.includes('WRONG'),old:result.text.includes('OLD'),seen:result.seen_at_ms>0}));"
            )
            expected = {"target": True, "wrong": False, "old": False, "seen": True}
        elif scenario.startswith("other_page"):
            body += (
                " const targetPage = await p.context.newPage();"
                f" await targetPage.setContent({json.dumps(card.format(completed + running))});"
                + (" await p.page.close();" if scenario == "other_page_closed_default" else "") +
                " const result = await p.waitForRunningActivity({scope:targetPage.locator('#target'),timeoutMs:3000});"
                " console.log(JSON.stringify({target:result.text.includes('TARGET TOOL'),old:result.text.includes('OLD'),seen:result.seen_at_ms>0}));"
            )
            expected = {"target": True, "old": False, "seen": True}
        else:
            body += (
                " let rejected=false; try { await p.waitForRunningActivity({scope:p.page.locator('#target'),timeoutMs:800}); }"
                " catch(error) { rejected=error.message.includes('No visible running'); }"
                " console.log(JSON.stringify({rejected}));"
            )
            expected = {"rejected": True}
    result = _open(tmp_path, portal, "test-actor", body=body)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == expected


def test_a_restarted_helper_reconnects_to_the_same_browser(tmp_path, portal):
    """TAU 辅助脚本重启后重新连接同一个浏览器，用例始终只有一个浏览器进程

    浏览器运行在启动器宿主进程中；未关闭就退出的辅助脚本再次 openPortal 时 reused=true 且宿主相同，close 后宿主退出、临时 profile 删除。

    :module: spec.design-governance
    :design: decision_c953eab654cd
    """
    # The first helper exits without closing (as a crashed helper would).
    crashed = _open(tmp_path, portal, "test-actor", body="process.exit(0);")
    assert crashed.returncode == 0, crashed.stderr
    first = json.loads((tmp_path / "evidence/tau-browser-launch.json").read_text())
    host = json.loads((tmp_path / "evidence/tau-browser-host.json").read_text())
    assert first["reused"] is False and host["state"] == "running" and host["host_pid"] == first["host_pid"]

    again = _open(tmp_path, portal, "test-actor")

    assert again.returncode == 0, again.stderr
    second = json.loads((tmp_path / "evidence/tau-browser-launch.json").read_text())
    closed = json.loads((tmp_path / "evidence/tau-browser-close.json").read_text())
    assert second["reused"] is True and second["host_pid"] == first["host_pid"]
    assert closed["ok"] is True and closed["host_pid"] == first["host_pid"]
    assert json.loads((tmp_path / "evidence/tau-browser-host.json").read_text())["state"] == "closed"
    assert not Path(host["profile_root"]).exists()
