"""Channel capabilities and shared Markdown segmentation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelCapabilities:
    buttons: bool
    edit_messages: bool
    unlimited_sends: bool
    command_menu: bool
    text_limit: int


def split_markdown_text(text: str, limit: int = 3000) -> list[str]:
    """Split Markdown at line ends; a fenced block cut in two is closed and reopened."""
    raw = str(text or "")
    if len(raw) <= limit:
        return [raw]
    room = limit - 40  # space for a reopened fence line and its closing
    chunks: list[str] = []
    current = ""
    fence = ""

    def close(chunk: str) -> str:
        if not fence:
            return chunk
        return chunk + ("" if chunk.endswith("\n") else "\n") + "```"

    for line in raw.splitlines(keepends=True):
        for start in range(0, max(1, len(line)), room):
            piece = line[start:start + room]
            if len(current) + len(piece) > room and current.strip():
                chunks.append(close(current))
                current = fence
            current += piece
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = "" if fence else stripped + "\n"
    if current.strip():
        chunks.append(current)
    return chunks
