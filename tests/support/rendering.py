from __future__ import annotations

import asyncio

from hermes_progress_tail.state import SessionContext


class Result:
    def __init__(self, success=True, message_id=None, error=""):
        self.success = success
        self.message_id = message_id
        self.error = error


class EditableAdapter:
    name = "editable"

    def __init__(self):
        self.sent = []
        self.edits = []
        self.next_id = 1

    async def send(self, chat_id, content, metadata=None):
        message_id = f"m{self.next_id}"
        self.next_id += 1
        self.sent.append((chat_id, content, metadata))
        return Result(True, message_id)

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return Result(True, message_id)


class NoEditAdapter:
    name = "noedit"

    def __init__(self):
        self.sent = []

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        return Result(True, f"m{len(self.sent)}")


class FailingEditAdapter(EditableAdapter):
    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return Result(False, message_id, "edit not supported")


class SequenceEditAdapter(EditableAdapter):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)
        self.deleted = []

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        if self.errors:
            return Result(False, message_id, self.errors.pop(0))
        return Result(True, message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        return True


class SequenceSendAdapter(EditableAdapter):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        if self.errors:
            return Result(False, None, self.errors.pop(0))
        message_id = f"m{self.next_id}"
        self.next_id += 1
        return Result(True, message_id)


class ExceptionSendAdapter(SequenceSendAdapter):
    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        if self.errors:
            raise RuntimeError(self.errors.pop(0))
        message_id = f"m{self.next_id}"
        self.next_id += 1
        return Result(True, message_id)


def make_live_context(
    adapter,
    *,
    strategy="live_tail",
    timestamp=False,
    platform="discord",
):
    return SessionContext(
        "s1",
        "k1",
        platform,
        "chat",
        "thread",
        adapter,
        asyncio.get_running_loop(),
        strategy,
        timestamp=timestamp,
    )
