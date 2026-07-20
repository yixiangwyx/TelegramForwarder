from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from enums.enums import ForwardMode
from filters.process import process_forwarded_message_edit


class TestMessageEditSync(unittest.IsolatedAsyncioTestCase):
    async def test_process_forwarded_message_edit_preserves_corrected_symbol(self):
        message = SimpleNamespace(
            text="#PROM  市價空\n\n我入倉價  1.7464\n建議3%或以下倉位即可",
            id=3795,
            date=None,
            media=None,
            grouped_id=None,
            buttons=None,
            sender_chat=None,
            peer_id=None,
        )
        event = SimpleNamespace(
            message=message,
            chat_id=-1002782620142,
            sender=None,
            client=AsyncMock(),
        )
        rule = SimpleNamespace(
            id=6,
            is_replace=False,
            is_ai=False,
            is_original_link=False,
            is_original_sender=False,
            is_original_time=False,
            time_template=None,
            forward_mode=ForwardMode.BLACKLIST,
            keywords=[],
            enable_reverse_blacklist=False,
            enable_reverse_whitelist=False,
            is_filter_user_info=False,
        )

        result = await process_forwarded_message_edit(
            AsyncMock(), event, "2782620142", rule
        )

        self.assertEqual(result, message.text)


if __name__ == "__main__":
    unittest.main()
