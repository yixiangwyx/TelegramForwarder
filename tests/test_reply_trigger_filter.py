from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from filters.reply_trigger_filter import ReplyTriggerFilter
from filters.push_filter import PushFilter


def make_context(message, *, reply_forward_ai_check=False):
    event = SimpleNamespace(
        message=message,
        chat_id=-1001739957542,
        client=AsyncMock(),
        sender=None,
        chat=None,
    )
    return SimpleNamespace(
        event=event,
        client=event.client,
        rule=SimpleNamespace(
            id=8,
            enable_reply_forward=True,
            reply_forward_ai_check=reply_forward_ai_check,
            source_chat=None,
        ),
        should_forward=True,
        reply_source_id=None,
        reply_target_id=None,
        reply_text="",
        reply_matched_forward=False,
        skip_keyword_filter=False,
        skip_ai_filter=False,
        original_message_text=message.text,
        message_text=message.text,
        media_files=[],
    )


class TestReplyTriggerFilter(unittest.IsolatedAsyncioTestCase):
    async def test_retries_until_concurrent_root_mapping_exists(self):
        root = SimpleNamespace(id=16755, text="#NIGHT 輕倉多", reply_to_msg_id=None)
        reply = SimpleNamespace(
            id=16756,
            text="入场价格：0.0235",
            reply_to_msg_id=16755,
            get_reply_message=AsyncMock(return_value=root),
        )
        context = make_context(reply)
        mapping = SimpleNamespace(target_message_id=422)
        filter_ = ReplyTriggerFilter()
        filter_._find_mapping = unittest.mock.Mock(side_effect=[None, mapping])

        with patch("filters.reply_trigger_filter.asyncio.sleep", new=AsyncMock()):
            self.assertTrue(await filter_.process(context))

        self.assertEqual(context.reply_source_id, 16755)
        self.assertEqual(context.reply_target_id, 422)
        self.assertTrue(context.reply_matched_forward)
        self.assertTrue(context.skip_keyword_filter)
        self.assertTrue(context.skip_ai_filter)

    async def test_walks_to_forwarded_ancestor_when_direct_reply_was_filtered(self):
        root = SimpleNamespace(id=16755, text="#NIGHT 輕倉多", reply_to_msg_id=None)
        intermediate = SimpleNamespace(
            id=16756,
            text="入场价格：0.0235",
            reply_to_msg_id=16755,
        )
        reply = SimpleNamespace(
            id=16757,
            text="止盈：0.02462-0.02601\n止损：0.02234",
            reply_to_msg_id=16756,
            get_reply_message=AsyncMock(return_value=intermediate),
        )
        context = make_context(reply)
        context.event.client.get_messages = AsyncMock(return_value=root)
        mapping = SimpleNamespace(target_message_id=422)
        filter_ = ReplyTriggerFilter()
        filter_._find_mapping = unittest.mock.Mock(side_effect=[None, mapping])

        self.assertTrue(await filter_.process(context))

        self.assertEqual(context.reply_source_id, 16755)
        self.assertEqual(context.reply_target_id, 422)
        self.assertTrue(context.reply_matched_forward)

    async def test_api_payload_links_to_matched_ancestor(self):
        direct = SimpleNamespace(
            id=16757,
            text="止盈：0.02462-0.02601\n止损：0.02234",
            reply_to_msg_id=16756,
            media=None,
            sender_chat=None,
            get_reply_message=AsyncMock(return_value=SimpleNamespace(text="入场价格：0.0235", media=None)),
        )
        context = make_context(direct)
        context.reply_source_id = 16755
        context.reply_matched_forward = True

        payload = await PushFilter()._build_api_payload(context, direct.text)

        self.assertEqual(payload["reply_to_source_message_id"], "16755")
        self.assertTrue(payload["reply_matched_forward"])


if __name__ == "__main__":
    unittest.main()
