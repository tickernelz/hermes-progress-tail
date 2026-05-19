import asyncio

from hermes_progress_tail.gateway.compat import delete_message


class BotWithDelete:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    async def delete_message(self, chat_id, message_id):
        self.calls.append((chat_id, message_id))
        return self.result


class AdapterWithBot:
    def __init__(self, result=None):
        self._bot = BotWithDelete(result=result)


def test_delete_message_bot_fallback_preserves_string_chat_ids():
    async def run():
        adapter = AdapterWithBot()

        deleted = await delete_message(adapter, "@channel_name", "123")

        assert deleted is True
        assert adapter._bot.calls == [("@channel_name", 123)]

    asyncio.run(run())


def test_delete_message_bot_fallback_honors_false_result():
    async def run():
        adapter = AdapterWithBot(result=False)

        deleted = await delete_message(adapter, "chat", "123")

        assert deleted is False
        assert adapter._bot.calls == [("chat", 123)]

    asyncio.run(run())
