from __future__ import annotations


def tool_line_terminal_status(line: str) -> str:
    text = str(line or "").strip().lower()
    if text.startswith("❌") or " · failed" in text:
        return "failed"
    if text.startswith("✅") or " · done" in text:
        return "done"
    return ""


def tool_line_fingerprint(line: str) -> str:
    text = line.strip()
    if "] " in text and text.startswith("["):
        text = text.split("] ", 1)[1]
    for prefix in ("✅ ", "❌ ", "🔎 ", "📖 ", "✍️ ", "🔧 ", "💻 ", "📋 ", "🧑‍💻 ", "🧰 "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    for suffix in (" · running", " · done", " · failed"):
        if suffix in text:
            text = text.split(suffix, 1)[0]
            break
    return text.strip()
