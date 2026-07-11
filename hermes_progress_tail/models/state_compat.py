from __future__ import annotations

from collections import deque


class SessionStateCompatibility:
    """Explicit legacy flat accessors for nested state owners."""

    @property
    def strategy(self):
        return self.routing.strategy

    @strategy.setter
    def strategy(self, value):
        self.routing.strategy = value

    @property
    def lines(self):
        return self.routing.lines

    @lines.setter
    def lines(self, value):
        self.routing.lines = value

    @property
    def preview_length(self):
        return self.routing.preview_length

    @preview_length.setter
    def preview_length(self, value):
        self.routing.preview_length = value

    @property
    def edit_interval(self):
        return self.routing.edit_interval

    @edit_interval.setter
    def edit_interval(self, value):
        self.routing.edit_interval = value

    @property
    def tools_enabled(self):
        return self.routing.tools_enabled

    @tools_enabled.setter
    def tools_enabled(self, value):
        self.routing.tools_enabled = value

    @property
    def assistant_enabled(self):
        return self.routing.assistant_enabled

    @assistant_enabled.setter
    def assistant_enabled(self, value):
        self.routing.assistant_enabled = value

    @property
    def reasoning_enabled(self):
        return self.routing.reasoning_enabled

    @reasoning_enabled.setter
    def reasoning_enabled(self, value):
        self.routing.reasoning_enabled = value

    @property
    def delegates_enabled(self):
        return self.routing.delegates_enabled

    @delegates_enabled.setter
    def delegates_enabled(self, value):
        self.routing.delegates_enabled = value

    @property
    def background_jobs_enabled(self):
        return self.routing.background_jobs_enabled

    @background_jobs_enabled.setter
    def background_jobs_enabled(self, value):
        self.routing.background_jobs_enabled = value

    @property
    def timestamp(self):
        return self.routing.timestamp

    @timestamp.setter
    def timestamp(self, value):
        self.routing.timestamp = value

    @property
    def timestamp_format(self):
        return self.routing.timestamp_format

    @timestamp_format.setter
    def timestamp_format(self, value):
        self.routing.timestamp_format = value

    @property
    def agent_label(self):
        return self.routing.agent_label

    @agent_label.setter
    def agent_label(self, value):
        self.routing.agent_label = value

    @property
    def chat_type(self):
        return self.routing.chat_type

    @chat_type.setter
    def chat_type(self, value):
        self.routing.chat_type = value

    @property
    def source_message_id(self):
        return self.routing.source_message_id

    @source_message_id.setter
    def source_message_id(self, value):
        self.routing.source_message_id = value

    @property
    def metadata(self) -> dict[str, str | bool] | None:
        if not self.thread_id:
            return None
        metadata: dict[str, str | bool] = {"thread_id": self.thread_id}
        if self.platform == "telegram" and self.chat_type == "dm":
            metadata["telegram_dm_topic_reply_fallback"] = True
            if self.thread_id not in {"", "1"}:
                metadata["direct_messages_topic_id"] = self.thread_id
            if self.source_message_id:
                metadata["telegram_reply_to_message_id"] = self.source_message_id
        return metadata

    def resize(self, lines: int) -> None:
        if self.tool_lines.maxlen == lines:
            return
        self.tool_lines = deque(list(self.tool_lines)[-lines:], maxlen=lines)
        self.lines = lines

    @property
    def message_id(self):
        return self.delivery.message_id

    @message_id.setter
    def message_id(self, value):
        self.delivery.message_id = value

    @property
    def can_edit(self):
        return self.delivery.can_edit

    @can_edit.setter
    def can_edit(self, value):
        self.delivery.can_edit = value

    @property
    def disabled(self):
        return self.delivery.disabled

    @disabled.setter
    def disabled(self, value):
        self.delivery.disabled = value

    @property
    def progress_state(self):
        return self.delivery.progress_state

    @progress_state.setter
    def progress_state(self, value):
        self.delivery.progress_state = value

    @property
    def finalized_at(self):
        return self.delivery.finalized_at

    @finalized_at.setter
    def finalized_at(self, value):
        self.delivery.finalized_at = value

    @property
    def last_render_at(self):
        return self.delivery.last_render_at

    @last_render_at.setter
    def last_render_at(self, value):
        self.delivery.last_render_at = value

    @property
    def edit_state(self):
        return self.delivery.edit_state

    @edit_state.setter
    def edit_state(self, value):
        self.delivery.edit_state = value

    @property
    def edit_backoff_until(self):
        return self.delivery.edit_backoff_until

    @edit_backoff_until.setter
    def edit_backoff_until(self, value):
        self.delivery.edit_backoff_until = value

    @property
    def edit_failure_count(self):
        return self.delivery.edit_failure_count

    @edit_failure_count.setter
    def edit_failure_count(self, value):
        self.delivery.edit_failure_count = value

    @property
    def edit_recovery_sends(self):
        return self.delivery.edit_recovery_sends

    @edit_recovery_sends.setter
    def edit_recovery_sends(self, value):
        self.delivery.edit_recovery_sends = value

    @property
    def delayed_flush_task(self):
        return self.delivery.delayed_flush_task

    @delayed_flush_task.setter
    def delayed_flush_task(self, value):
        self.delivery.delayed_flush_task = value

    @property
    def delete_task(self):
        return self.delivery.delete_task

    @delete_task.setter
    def delete_task(self, value):
        self.delivery.delete_task = value

    @property
    def fallback_send_count(self):
        return self.delivery.fallback_send_count

    @fallback_send_count.setter
    def fallback_send_count(self, value):
        self.delivery.fallback_send_count = value

    @property
    def snapshots_sent(self):
        return self.delivery.snapshots_sent

    @snapshots_sent.setter
    def snapshots_sent(self, value):
        self.delivery.snapshots_sent = value

    @property
    def last_event_at(self):
        return self.diagnostics.last_event_at

    @last_event_at.setter
    def last_event_at(self, value):
        self.diagnostics.last_event_at = value

    @property
    def new_events_since_snapshot(self):
        return self.diagnostics.new_events_since_snapshot

    @new_events_since_snapshot.setter
    def new_events_since_snapshot(self, value):
        self.diagnostics.new_events_since_snapshot = value

    @property
    def total_events(self):
        return self.diagnostics.total_events

    @total_events.setter
    def total_events(self, value):
        self.diagnostics.total_events = value

    @property
    def last_error(self):
        return self.diagnostics.last_error

    @last_error.setter
    def last_error(self, value):
        self.diagnostics.last_error = value

    @property
    def downgrade_reason(self):
        return self.diagnostics.downgrade_reason

    @downgrade_reason.setter
    def downgrade_reason(self, value):
        self.diagnostics.downgrade_reason = value

    @property
    def downgrade_at(self):
        return self.diagnostics.downgrade_at

    @downgrade_at.setter
    def downgrade_at(self, value):
        self.diagnostics.downgrade_at = value

    @property
    def compaction_count(self):
        return self.diagnostics.compaction_count

    @compaction_count.setter
    def compaction_count(self, value):
        self.diagnostics.compaction_count = value

    @property
    def tool_lines(self):
        return self.tool.lines

    @tool_lines.setter
    def tool_lines(self, value):
        self.tool.lines = value

    @property
    def active_tool_lines(self):
        return self.tool.active_lines

    @active_tool_lines.setter
    def active_tool_lines(self, value):
        self.tool.active_lines = value

    @property
    def active_tool_fingerprints(self):
        return self.tool.active_fingerprints

    @active_tool_fingerprints.setter
    def active_tool_fingerprints(self, value):
        self.tool.active_fingerprints = value

    @property
    def tool_started_count(self):
        return self.tool.started_count

    @tool_started_count.setter
    def tool_started_count(self, value):
        self.tool.started_count = value

    @property
    def tool_completed_count(self):
        return self.tool.completed_count

    @tool_completed_count.setter
    def tool_completed_count(self, value):
        self.tool.completed_count = value

    @property
    def tool_failed_count(self):
        return self.tool.failed_count

    @tool_failed_count.setter
    def tool_failed_count(self, value):
        self.tool.failed_count = value

    @property
    def completed_tool_ids(self):
        return self.tool.completed_ids

    @completed_tool_ids.setter
    def completed_tool_ids(self, value):
        self.tool.completed_ids = value

    @property
    def todo_items(self):
        return self.tool.todo_items

    @todo_items.setter
    def todo_items(self, value):
        self.tool.todo_items = value

    @property
    def todo_updated_at(self):
        return self.tool.todo_updated_at

    @todo_updated_at.setter
    def todo_updated_at(self, value):
        self.tool.todo_updated_at = value

    @property
    def delegate_branches(self):
        return self.delegate.branches

    @delegate_branches.setter
    def delegate_branches(self, value):
        self.delegate.branches = value

    @property
    def delegate_order(self):
        return self.delegate.order

    @delegate_order.setter
    def delegate_order(self, value):
        self.delegate.order = value

    @property
    def background_jobs(self):
        return self.background.jobs

    @background_jobs.setter
    def background_jobs(self, value):
        self.background.jobs = value

    @property
    def background_order(self):
        return self.background.order

    @background_order.setter
    def background_order(self, value):
        self.background.order = value

    @property
    def assistant_lines(self):
        return self.assistant.lines

    @assistant_lines.setter
    def assistant_lines(self, value):
        self.assistant.lines = value

    @property
    def assistant_latest_text(self):
        return self.assistant.latest_text

    @assistant_latest_text.setter
    def assistant_latest_text(self, value):
        self.assistant.latest_text = value

    @property
    def assistant_pending_chars(self):
        return self.assistant.pending_chars

    @assistant_pending_chars.setter
    def assistant_pending_chars(self, value):
        self.assistant.pending_chars = value

    @property
    def last_assistant_chars(self):
        return self.assistant.last_chars

    @last_assistant_chars.setter
    def last_assistant_chars(self, value):
        self.assistant.last_chars = value

    @property
    def last_assistant_at(self):
        return self.assistant.last_at

    @last_assistant_at.setter
    def last_assistant_at(self, value):
        self.assistant.last_at = value

    @property
    def assistant_transient(self):
        return self.assistant.transient

    @assistant_transient.setter
    def assistant_transient(self, value):
        self.assistant.transient = value

    @property
    def reasoning_text(self):
        return self.reasoning.text

    @reasoning_text.setter
    def reasoning_text(self, value):
        self.reasoning.text = value

    @property
    def reasoning_pending_chars(self):
        return self.reasoning.pending_chars

    @reasoning_pending_chars.setter
    def reasoning_pending_chars(self, value):
        self.reasoning.pending_chars = value

    @property
    def last_reasoning_source(self):
        return self.reasoning.last_source

    @last_reasoning_source.setter
    def last_reasoning_source(self, value):
        self.reasoning.last_source = value

    @property
    def last_reasoning_chars(self):
        return self.reasoning.last_chars

    @last_reasoning_chars.setter
    def last_reasoning_chars(self, value):
        self.reasoning.last_chars = value

    @property
    def last_reasoning_at(self):
        return self.reasoning.last_at

    @last_reasoning_at.setter
    def last_reasoning_at(self, value):
        self.reasoning.last_at = value

    @property
    def line_buffer(self) -> deque[str]:
        return self.tool_lines

    @line_buffer.setter
    def line_buffer(self, value: deque[str]) -> None:
        self.tool_lines = value
