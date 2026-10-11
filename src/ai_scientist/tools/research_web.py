"""Bounded public-web reading with pinned DNS, TLS verification and no credentials.

Retrieved content is untrusted evidence, never agent instructions. A successful
fetch proves retrieval, not scientific validity, peer review, or truth.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_BYTES = 2 * 1024 * 1024
# This is an on-disk/cache budget, not a per-model-call context allowance.
# Background reading exposes bounded pages and explicit offsets instead.
MAX_TEXT = 200000


def public_target(url: str) -> tuple[str, str, list[str]]:
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.port not in (None, 443) or len(url) > 2048):
        raise ValueError('Only public HTTPS URLs without credentials on port 443 are allowed')
    if any(ord(c) < 33 for c in url):
        raise ValueError('Invalid URL characters')
    host = parts.hostname.encode('idna').decode('ascii')
    addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    parsed_addresses = [ipaddress.ip_address(ip) for ip in addresses]
    if not addresses or any(not ip.is_global or ip.is_multicast or ip.is_reserved for ip in parsed_addresses):
        raise ValueError('Private, loopback, reserved and mixed public/private DNS targets are blocked')
    path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
    return host, path, addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the checked address without resolving the hostname again.
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch_public(url: str) -> tuple[str, str, bytes]:
    deadline = time.monotonic() + 25
    for _ in range(4):
        host, path, addresses = public_target(url)
        connection = _PinnedHTTPS(host, addresses[0], max(.1, deadline - time.monotonic()))
        try:
            connection.request('GET', path, headers={
                'User-Agent': 'AI-Scientist-Research/1.0', 'Accept-Encoding': 'identity',
                'Accept': 'text/html,application/json,text/plain,application/xml;q=0.9',
            })
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location:
                    raise ValueError('Redirect without location')
                url = urljoin(url, location)
                continue  # Every redirect is independently checked before connecting.
            if response.status != 200:
                raise ValueError(f'Public source returned HTTP {response.status}')
            if response.getheader('Content-Encoding', 'identity') not in ('identity', ''):
                raise ValueError('Compressed source responses are not accepted')
            kind = response.getheader('Content-Type', '').split(';')[0].lower()
            if kind not in {'text/html', 'text/plain', 'text/csv', 'text/tab-separated-values', 'application/json', 'application/xml', 'text/xml', 'application/xhtml+xml'}:
                raise ValueError('Unsupported source type; upload PDFs through the document parser')
            chunks, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise ValueError('Source download deadline exceeded')
                if connection.sock:
                    connection.sock.settimeout(max(.1, deadline - time.monotonic()))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError('Source exceeds 2 MiB read budget')
            return url, kind, b''.join(chunks)
        finally:
            connection.close()
    raise ValueError('Too many redirects')


class _ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []
        self.title_parts = []
        self.in_title = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a' and not self.hidden:
            href = dict(attrs).get('href')
            if href and len(self.links) < 200:
                self.links.append(href)
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden += 1
        if tag == 'title':
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden = max(0, self.hidden - 1)
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if not self.hidden:
            self.parts.append(data)


def normalize_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def source_access_failure(text: str, title: str = '') -> str:
    """Recognize short access-interstitial pages, not scientific relevance.

    A paper discussing CAPTCHAs must not be rejected just for the keyword.
    Only explicit challenge-page titles or characteristic operational messages
    in a short response are considered a failure to retrieve source content.
    """
    if len(text) > 8000:
        return ''
    heading = normalize_text(title).lower().rstrip('.!')
    challenge_titles = {
        'just a moment', 'access denied', 'security check', 'robot check',
        'checking your browser', 'verify you are human', 'recaptcha', 'captcha',
        'human verification', 'attention required! | cloudflare',
    }
    operational_markers = (
        r'\bincapsula incident id\b',
        r'\bchecking your browser before accessing\b',
        r'\bplease (?:complete the security check|verify (?:that )?you are (?:a )?human)\b',
        r'\benable javascript and cookies to continue\b',
        r'\bverify you are human by completing the action below\b',
    )
    if heading in challenge_titles or any(re.search(pattern, text, re.I) for pattern in operational_markers):
        return ('read_failed: the server returned a CAPTCHA/access challenge, not the requested source text. '
                'Try another public full-text source or ask the researcher to upload the document; '
                'do not treat this response as a paper that has been read.')
    return ''


def read_source_snapshot(url: str, *, max_text: int = MAX_TEXT) -> dict:
    """Fetch a bounded cache; callers must page this before sending it to a model."""
    if not 1 <= max_text <= MAX_TEXT:
        raise ValueError(f'Source cache budget must be between 1 and {MAX_TEXT} characters')
    final_url, kind, body = fetch_public(url)
    text = body.decode('utf-8', errors='replace')
    title = final_url
    links = []
    if kind in {'text/html', 'application/xhtml+xml'}:
        parser = _ReadableHTML()
        parser.feed(text)
        text = ' '.join(parser.parts)
        title = normalize_text(' '.join(parser.title_parts)) or title
        links = list(dict.fromkeys(urljoin(final_url, link) for link in parser.links
                                  if urlsplit(urljoin(final_url, link)).scheme == 'https'))
    text = normalize_text(text)
    access_failure = source_access_failure(text, title)
    if access_failure:
        raise ValueError(access_failure)
    if len(text) < 80:
        raise ValueError('Insufficient readable text; page may require JavaScript or authentication')
    return {'url': final_url, 'title': title[:500], 'text': text[:max_text],
            'links': links,
            'truncated': len(text) > max_text, 'cache_truncated': len(text) > max_text,
            'source_text_chars': len(text), 'cache_chars': min(len(text), max_text),
            'cache_limit_chars': max_text, 'content_type': kind,
            'sha256': hashlib.sha256(body).hexdigest(), 'retrieval_status': 'retrieved',
            'verification_note': 'Public content retrieved; scientific claims are not verified.'}


def read_source(url: str) -> dict:
    """Legacy consumers have no paging; retain their former 24k context ceiling."""
    return read_source_snapshot(url, max_text=24000)


def search_papers(query: str) -> dict:
    # Metadata only: Crossref hits are not full text and never become evidence automatically.
    _, _, body = fetch_public('https://api.crossref.org/works?rows=5&query.bibliographic=' + quote(query, safe=''))
    items = json.loads(body)['message']['items']
    return {'provider': 'Crossref', 'metadata_only': True, 'papers': [
        {'title': (item.get('title') or [''])[0], 'doi': item.get('DOI'),
         'url': item.get('URL'), 'publisher': item.get('publisher'),
         'type': item.get('type'), 'published': item.get('published'),
         'authors': [{'given': a.get('given'), 'family': a.get('family')}
                     for a in item.get('author', [])[:8]],
         'abstract': re.sub('<[^>]+>', ' ', item.get('abstract', ''))[:2500]}
        for item in items]}
