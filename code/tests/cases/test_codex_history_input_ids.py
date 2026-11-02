"""Imported Portal history must not invent Responses API item identities."""
import copy
import unittest

from management_portal.execution.codex_history import response_items_from_segments


class ImportedItemIdentities(unittest.TestCase):
    def test_portal_items_omit_ids_and_keep_tool_pairing(self):
        segments = [
            {'segment_id': 'user', 'segment_type': 'input', 'content': 'Remember blue.'},
            {'segment_id': 'assistant', 'segment_type': 'model', 'content': 'Remembered.',
             'metadata': {'phase': 'final_answer'}},
            {'segment_id': 'tool', 'segment_type': 'tool', 'content': {
                'call_id': 'call-original', 'tool_name': 'inspect', 'input': {'x': 1}, 'result': 'blue'}},
        ]
        original = copy.deepcopy(segments)
        items = response_items_from_segments(segments)
        self.assertTrue(all('id' not in item for item in items))
        self.assertEqual(items[1]['phase'], 'final_answer')
        self.assertEqual(items[2]['call_id'], items[3]['call_id'])
        self.assertEqual(items[2]['call_id'], 'call-original')
        self.assertEqual(segments, original)

    def test_native_encrypted_history_keeps_provider_identity(self):
        item = {'type': 'reasoning', 'id': 'rs_from_provider', 'encrypted_content': 'native-encrypted'}
        segments = [{'segment_id': 'compact', 'segment_type': 'compaction',
                     'content': {'replacement_history': [item]}}]
        self.assertEqual(response_items_from_segments(segments), [item])


if __name__ == '__main__':
    unittest.main()
