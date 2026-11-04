"""Direct checks for Home report semantics, authorization and derived media."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from management_portal.home_report import build_report, normalize_target, reauthorize
from management_portal.home_report_media import MediaStore, chapters, spoken_text, speech_units
from management_portal.http_media_range import parse_byte_range

RESIDENT = "resident_0123456789ab"
LINEAGE = "lineage_0123456789ab"
TASK = RESIDENT + "/msg_0123456789abcdef0123456789abcdef"


class HomeReportTests(unittest.TestCase):
    def task_reader(self, instruction, arguments):
        if instruction == "GetMessage":
            return {"message": {"created_at": "2026-11-04T00:00:00Z", "content_parts": [{"type": "text", "text": "真实交付要检查产物"}]}}
        if instruction == "GetInputState":
            return {"input_state": {"state": "completed", "reply_message_ids": ["reply"], "updated_at": "2026-11-04T01:00:00Z"}}
        raise AssertionError(instruction)

    def test_reply_is_not_business_completion_and_version_is_stable(self):
        report = build_report({"kind": "task", "id": TASK}, read=self.task_reader)
        self.assertEqual(report["counts"], {"已有回复": 1})
        self.assertEqual(report["version"], build_report(report["target"], read=self.task_reader)["version"])
        self.assertEqual({s["read"]["instruction"] for s in report["sources"]}, {"GetMessage", "GetInputState"})
        self.assertEqual(report['rows'][0]['open']['target']['id'], TASK)
        self.assertEqual(report['rows'][0]['request_text'], '真实交付要检查产物')
        self.assertEqual(report['rows'][0]['replies'][0]['message_id'], 'reply')

    def test_unreadable_source_removes_workline(self):
        def read(name, args):
            if name == "GetLineage": return {"lineage": {"title": "A", "revision": 5}}
            if name == "GetSharedState": return {"value": {"schema": "tfi.exploration-frontier.v1", "items": [
                {"id": "private", "title": "SECRET TITLE", "progress": "SECRET BODY", "sources": ["article_0123456789ab"]}]}}
            raise PermissionError("not readable")
        report = build_report({"kind": "lineage", "id": LINEAGE}, read=read)
        self.assertEqual(report["rows"], [])
        self.assertNotIn("SECRET", json.dumps(report))
        self.assertTrue(report["warnings"])

    def test_changed_permission_denies_cached_media_and_other_principal(self):
        report = build_report({"kind": "task", "id": TASK}, read=self.task_reader)
        with tempfile.TemporaryDirectory() as folder:
            store = MediaStore(folder)
            job = "a" * 64
            directory = store.directory("alice", job)
            directory.mkdir(parents=True)
            store.save(directory, {"job_id": job, "status": "ready", "report": report})
            (directory / "video.mp4").write_bytes(b"generated-media")
            def denied(*args): raise PermissionError("revoked")
            with self.assertRaises(PermissionError): store.file(job, "video.mp4", "alice", read=denied)
            with self.assertRaises(FileNotFoundError): store.file(job, "video.mp4", "bob", read=self.task_reader)
            with self.assertRaises(FileNotFoundError): store.file(job, "../state.json", "alice", read=self.task_reader)
            path, mime = store.file(job, "video.mp4", "alice", read=self.task_reader)
            self.assertEqual(mime, "video/mp4")
            self.assertTrue(path.exists())

    def test_failed_video_does_not_modify_report_and_can_retry(self):
        report = build_report({"kind": "task", "id": TASK}, read=self.task_reader)
        before = json.dumps(report)
        release = threading.Event()
        attempts = []
        def render(snapshot, directory):
            attempts.append(1)
            release.wait(2)
            if len(attempts) == 1: raise RuntimeError("speech unavailable")
            return {"duration": 1, "generation_seconds": 0.01}
        with tempfile.TemporaryDirectory() as folder:
            store = MediaStore(folder, renderer=render)
            first = store.start(report, "alice")
            duplicate = store.start(report, "alice")
            self.assertEqual(first["job_id"], duplicate["job_id"])
            self.assertEqual(len(attempts), 1)
            release.set()
            for _ in range(100):
                state = store.status(first["job_id"], "alice", read=self.task_reader)
                if state["status"] != "pending": break
                time.sleep(0.01)
            self.assertEqual(state["status"], "failed")
            store.start(report, "alice", retry=True)
            for _ in range(100):
                state = store.status(first["job_id"], "alice", read=self.task_reader)
                if state["status"] != "pending": break
                time.sleep(0.01)
            self.assertEqual(state["status"], "ready")
            self.assertEqual(json.dumps(report), before)

    def test_generation_finishing_during_permission_check_is_ready(self):
        report = build_report({'kind':'task','id':TASK}, read=self.task_reader)
        with tempfile.TemporaryDirectory() as folder:
            store=MediaStore(folder);job='b'*64;directory=store.directory('alice',job)
            directory.mkdir(parents=True)
            pending={'job_id':job,'status':'pending','report':report}
            store.save(directory,pending)
            def finishes_during_read(name,args):
                store.save(directory,{**pending,'status':'ready','duration':2})
                return self.task_reader(name,args)
            self.assertEqual(store.status(job,'alice',read=finishes_during_read)['status'],'ready')

    def test_project_map_unavailable_is_explicit(self):
        def read(name, args):
            if name == "GetProject": return {"project": {"title": "Project", "description": "Goal"}}
            raise RuntimeError("owner unavailable")
        report = build_report({"kind": "project", "id": "Project"}, read=read)
        self.assertEqual(report["goal"], "Goal")
        self.assertTrue(report["warnings"])

    def test_target_and_range_validation(self):
        with self.assertRaises(ValueError): normalize_target({"kind": "task", "id": "task_0123456789ab"})
        with self.assertRaises(ValueError): normalize_target({"kind": "project", "id": "../../private"})
        self.assertEqual(parse_byte_range("bytes=4-9", 20), (4, 9))

    def test_empty_research_map_preserves_real_team_worklines(self):
        def read(name, args):
            if name == "GetProject": return {"project": {"title": "P"}, "team": {"lineages": [{"lineage_id": LINEAGE, "title": "团队"}]}}
            if name == "GetResearchMap": return {"directions": [], "revision": 0}
            if name == "GetSharedState": return {"value": {"schema": "tfi.exploration-frontier.v1", "items": [
                {"id": "actual", "title": "已登记工作", "progress": "正在研究", "sources": ["article_0123456789ab"]}]}}
            if name == "ResolveResource": return {"resource": {"title": "材料", "version": 1}}
            raise AssertionError(name)
        report = build_report({"kind": "project", "id": "P"}, read=read)
        self.assertEqual(len(report["rows"]), 1)
        self.assertIn("未关联研究方向 · 团队", report["rows"][0]["group"])


class HomeReportSpeechTests(unittest.TestCase):
    def test_speech_omits_identifiers_and_addresses_without_changing_sources(self):
        report = {'target': {'kind': 'task'}, 'goal': 'decision_0123456789ab 的开场介绍',
                  'rows': [{'id':'original-id', 'title':'关系说明', 'status':'已有回复',
                            'body':'依据 decision_0123456789ab（workspace_0123456789ab/file/a.json）。[完整说明](https://example.test/a)已经更新。。',
                            'next':'核对 /opt/tfi/data/report.json。'}]}
        before = json.dumps(report)
        speech = spoken_text(report['rows'][0]['body']) + spoken_text(report['rows'][0]['next'])
        self.assertNotIn('0123456789ab', speech)
        self.assertNotIn('https', speech)
        self.assertNotIn('/opt/', speech)
        self.assertNotIn('。。', speech)
        self.assertIn('完整说明已经更新。', speech)
        self.assertEqual(before, json.dumps(report))

    def test_speech_uses_complete_clauses_and_does_not_split_words(self):
        self.assertEqual(spoken_text('先显示文字。然后结合 Portal 实现逐项说明。', 14), '先显示文字。')
        self.assertEqual(spoken_text('完成第一项，再结合 Portal 显示下一项。', 11), '完成第一项。')
        self.assertEqual(spoken_text('PortalMechanism', 6), '完整内容请阅读文字报告。')
        self.assertIn('1.5 秒', spoken_text('生成用时 1.5 秒。。'))
        self.assertEqual(speech_units('继续研究 PortalMechanism，再确认。'), ['继续研究 PortalMechanism，', '再确认。'])

    def test_opening_starts_with_real_relation(self):
        rows = [dict(id=id, title=title, status='待核实',body='原始说明。',sources=['s']) for id,title in [('a','反例研究'),('b','判断依据')]]
        scenes = chapters({'target':{'kind':'project'},'rows':rows,'relations':[{'from':'a','to':'b','kind':'builds_on'}], 'goal':'不能朗读这一段通用长开场'})
        self.assertEqual(scenes[0]['mode'], 'objects')
        self.assertIn('影响仍然未知', scenes[-1]['title'])
        self.assertEqual(len(scenes), 5)
        self.assertNotIn('原始说明', scenes[0]['speech'])

    def test_prose_alone_cannot_become_an_explanation_video(self):
        report={'target':{'kind':'lineage'},'rows':[{'id':'a','title':'认识框架','status':'待核实','body':'复杂主题文字。'}]}
        with self.assertRaises(ValueError):
            chapters(report)
        report['rows'].append({'id':'b','title':'另一项工作','status':'推进中','body':'另一个没有结构的主题。'})
        with self.assertRaisesRegex(ValueError, '没有可视化'):
            chapters(report)


if __name__ == "__main__":
    unittest.main()
