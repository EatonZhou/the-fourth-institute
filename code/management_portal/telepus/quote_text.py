"""Visible Markdown text used only for conservative quote matching."""
from markdown_it import MarkdownIt


def quote_text(value: str) -> str:
    parser = MarkdownIt('commonmark').enable('strikethrough')

    def visible(tokens):
        for token in tokens:
            if token.children:
                yield from visible(token.children)
            elif token.type in {'text', 'code_inline', 'code_block', 'fence'}:
                yield token.content
            elif token.type in {'softbreak', 'hardbreak'} or token.block:
                yield '\n'

    return ' '.join(''.join(visible(parser.parse(str(value or '')))).split())
