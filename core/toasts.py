"""
Pop-up notifications (toasts) instead of Django's green/red message bars.

How it works
------------
* `toast_messages` replaces Django's `messages` context processor. When a page is rendered it
  takes the pending messages, hands the page an EMPTY list (so no bar is drawn) and remembers
  them on the request.
* `ToastMiddleware` writes them into the page as a small data block; core/static/core/erp.js
  turns that into pop-ups (see `showToasts`).
"""
import json
from html.parser import HTMLParser

from django.contrib.messages import get_messages
from django.contrib.messages.constants import DEFAULT_LEVELS

from . import sysconf


class _Parts(HTMLParser):
    """Turn a message that contains simple HTML (Django's own "unit added" link) into plain
    text pieces; only links to pages inside this site are kept, everything else is text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._href = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self._href = href if href.startswith("/") and not href.startswith("//") else None

    def handle_endtag(self, tag):
        if tag == "a":
            self._href = None

    def handle_data(self, data):
        if data:
            part = {"t": data}
            if self._href:
                part["href"] = self._href
            self.parts.append(part)


def _parts(message):
    text = str(message)
    if hasattr(message, "__html__"):  # a message that Django marked as HTML
        parser = _Parts()
        parser.feed(text)
        parser.close()
        return parser.parts or [{"t": ""}]
    return [{"t": text}]


def toast_messages(request):
    storage = get_messages(request)
    items = [
        {"level": (m.level_tag or "info").split()[0], "parts": _parts(m.message)} for m in storage
    ]
    request._erp_toasts = items
    return {"messages": [], "DEFAULT_MESSAGE_LEVELS": DEFAULT_LEVELS}


def _safe_json(data):
    # Safe inside a <script> block: no "<", ">" or "&" can end the block early.
    return (
        json.dumps(data)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


class ToastMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        items = getattr(request, "_erp_toasts", None)
        if (
            not items
            or getattr(response, "streaming", False)
            or "text/html" not in response.get("Content-Type", "")
        ):
            return response
        html = response.content.decode(response.charset or "utf-8")
        script = (
            "<script>window.__erpToastSeconds=" + str(int(sysconf.get("toast_seconds"))) + ";"
            "window.__erpToasts=" + _safe_json(items) + ";"
            "if(window.erpShowToasts){window.erpShowToasts();}</script>"
        )
        index = html.rfind("</body>")
        html = html[:index] + script + html[index:] if index != -1 else html + script
        response.content = html.encode(response.charset or "utf-8")
        if response.has_header("Content-Length"):
            response["Content-Length"] = str(len(response.content))
        return response
