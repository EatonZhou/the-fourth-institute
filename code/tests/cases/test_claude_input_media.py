""":module: execution.providers"""
import base64
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from management_portal import claude_input_media as media
from tests.cases.claude_sdk_support import load_claude_module

daemon_module = load_claude_module("claude_code_daemon")
from management_portal.claude_code_client import ClaudeCodeClient


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC')


class ClaudeImageInputTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'image.png'
        self.path.write_bytes(PNG)
        self.image = {'media_type': 'image', 'source_type': 'local_path',
                      'source': str(self.path), 'mime_type': 'image/png'}

    def test_images_are_encoded_in_order_without_path_text(self):
        """多张图片按顺序编码在文本之前，内容中不带本地路径"""
        url = {**self.image, 'source_type': 'url', 'source': 'https://example.com/image.png'}
        blocks = media.content_blocks('Compare these images', [self.image, url])
        self.assertEqual([b['type'] for b in blocks], ['image', 'image', 'text'])
        self.assertEqual(base64.b64decode(blocks[0]['source']['data']), PNG)
        self.assertEqual(blocks[0]['source']['media_type'], 'image/png')
        self.assertEqual(blocks[1]['source'], {'type': 'url', 'url': url['source']})
        self.assertEqual(blocks[2]['text'], 'Compare these images')
        self.assertNotIn(str(self.path), str(blocks))

    def test_bad_or_oversized_attachments_fail_before_sdk_submission(self):
        """来源或类型不合法、超出大小或数量上限的图片附件在提交 SDK 前报错"""
        for changes in ({'source': 'relative.png'}, {'mime_type': 'image/svg+xml'},
                        {'media_type': 'audio'}, {'source_type': 'bad'},
                        {'source_type': 'url', 'source': 'file:///secret'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                media.content_blocks('hi', [{**self.image, **changes}])
        with mock.patch.object(media, 'MAX_IMAGE_ENCODED_BYTES', 8), self.assertRaisesRegex(ValueError, 'encoded limit'):
            media.content_blocks('hi', [self.image])
        with mock.patch.object(media, 'MAX_REQUEST_ENCODED_BYTES', 8), self.assertRaisesRegex(ValueError, 'per-turn'):
            media.content_blocks('hi', [self.image])
        with self.assertRaises(ValueError):
            media.content_blocks('hi', [self.image] * 21)

    async def test_image_turn_uses_sdk_content_and_followup_remains_plain_text(self):
        """带图片的轮次以 SDK 图片内容块提交，后续轮次仍为纯文本"""
        received = []
        class Client:
            async def query(self, prompt):
                received.append([x async for x in prompt])

            async def receive_response(self):
                # The CLI echoes the prompt first; the daemon only takes the result after its own echo.
                yield daemon_module.UserMessage(content='prompt', uuid=received[-1][0]['uuid'])
                yield daemon_module.ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1,
                    is_error=False, num_turns=1, session_id='native-session', result='seen')

        daemon = daemon_module.ClaudeCodeDaemon()
        handle = daemon_module.SessionHandle(key='test', signature=(), client=Client(), session_id='native-session')
        profile = {'id': 'native', 'provider': 'claude', 'kind': 'subscription_login',
                   'default_model': 'claude-opus-5-5', 'models': [{'id': 'claude-opus-5-5', 'enabled': True}]}
        with mock.patch.object(daemon_module, '_provider_profile', return_value=profile), \
                mock.patch.object(daemon, '_handle_for', mock.AsyncMock(return_value=handle)):
            events = mock.AsyncMock()
            await daemon._run_turn_once({'prompt': '', 'input_media': [self.image], 'turn_id': 'image'}, events)
            await daemon._run_turn_once({'prompt': 'Follow up', 'turn_id': 'followup'}, events)
        self.assertEqual(received[0][0]['session_id'], 'native-session')
        self.assertEqual(received[0][0]['message']['content'][0]['type'], 'image')
        self.assertEqual(received[1][0]['message']['content'], 'Follow up')

    async def test_text_only_route_rejects_images_without_opening_a_session(self):
        """纯文本路由收到图片时直接报错，不打开会话"""
        daemon = daemon_module.ClaudeCodeDaemon()
        profile = {'id': 'text', 'provider': 'deepseek', 'kind': 'openai_compatible_chat',
                   'default_model': 'text', 'models': [{'id': 'text', 'enabled': True}]}
        with mock.patch.object(daemon_module, '_provider_profile', return_value=profile), \
                mock.patch.object(daemon, '_handle_for', mock.AsyncMock()) as open_session:
            with self.assertRaisesRegex(ValueError, 'does not support images'):
                await daemon._run_turn_once({'prompt': 'hi', 'input_media': [self.image]}, mock.AsyncMock())
            open_session.assert_not_called()

    def test_socket_client_preserves_attachments(self):
        """Claude 客户端原样转交图片附件

        ClaudeCodeClient.run_turn 把 input_media 原样放进 turn/run 请求交给 daemon。
        """
        client = ClaudeCodeClient()
        args = dict(session_key='test', session_id='', prompt='see', cwd='/', config_dir='/account',
                    provider_profile='native', model='claude-opus-5-5', is_owner=False, input_media=[self.image])
        with mock.patch.object(client, '_request', return_value={'response': 'seen'}) as request:
            client.run_turn(**args)
            self.assertEqual(request.call_args.args[1]['input_media'], [self.image])
