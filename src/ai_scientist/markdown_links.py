"""Presentation-only repair of bare URL boundaries; stored model text is unchanged."""
import re


def readable_links(text):
    # Keep explicit Markdown links, angle links and code verbatim. Only enclose
    # bare URLs so Chinese sentence punctuation cannot become part of the href.
    pattern = r'(```[\s\S]*?```|`[^`\n]*`|\[[^\]\n]*\]\([^\n]*?\)|<https?://[^>]+>)'
    def replace(match):
        value = match.group(0)
        url = value.rstrip('.,;!?:')
        while url.endswith(')') and url.count(')') > url.count('('):
            url = url[:-1]
        return '<' + url + '>' + value[len(url):]
    return ''.join(part if i % 2 else re.sub(r'https?://[^\s<>`，。；！？：）（、【】]+', replace, part)
                   for i, part in enumerate(re.split(pattern, text)))
