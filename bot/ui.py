"""Small presentation helpers shared by Telegram screens."""
import unicodedata

HOME = {"text": "🏠 Menu", "callback_data": "c:menu"}


def compact_text(value, limit=60, fallback=""):
    """Keep literal titles on one line and truncate at a UTF-16 boundary."""
    text = "".join(" " if unicodedata.category(char) == "Cc" else char
                   for char in str(value or ""))
    text = " ".join(text.split()) or fallback
    encoded = text.encode("utf-16-le", errors="replace")
    if len(encoded) <= limit * 2:
        return text
    return encoded[:max(0, limit - 1) * 2].decode("utf-16-le", errors="ignore").rstrip() + "…"
