"""Testing utilities."""

import typing as t
import threading
import html.parser

import werkzeug.serving

if t.TYPE_CHECKING:
    import flask


class IndexParser(html.parser.HTMLParser):
    declaration: str
    title: str
    anchors: t.List[
        t.Tuple[t.Union[str, None], t.List[t.Tuple[str, t.Union[str, None]]]]
    ]
    _tag_chain: t.List[t.Tuple[str, t.List[t.Tuple[str, t.Union[str, None]]]]]
    _current_text: t.Union[str, None] = None

    def __init__(self):
        super().__init__()
        self._tag_chain = []
        self.anchors = []

    @classmethod
    def from_text(cls, text: str) -> "IndexParser":
        parser = cls()
        parser.feed(text)
        parser.close()
        return parser

    def handle_decl(self, decl):
        self.declaration = decl

    def handle_starttag(self, tag, attrs):
        self._tag_chain.append((tag, attrs))
        if self._current_text:
            self._current_text = None

    def handle_data(self, data):
        self._current_text = data

    def handle_endtag(self, tag):
        if tag == "a":
            if self._tag_chain and self._tag_chain[-1][0] == "a":
                _, attributes = self._tag_chain[-1]
                self.anchors.append((self._current_text, attributes))
        elif tag == "title":
            if self._tag_chain and self._tag_chain[-1][0] == "title":
                self.title = self._current_text
        while self._tag_chain:
            start_tag, _ = self._tag_chain.pop()
            if start_tag == tag:
                break
        self._current_text = None


class Thread(threading.Thread):
    exc = None

    def run(self):
        try:
            super().run()
        except Exception as e:
            self.exc = e


def make_server(app: "flask.Flask") -> t.Generator[str, None, None]:
    server = werkzeug.serving.make_server(host="localhost", port=0, app=app)
    thread = Thread(target=server.serve_forever, args=(0.05,))
    thread.start()
    yield f"http://localhost:{server.port}"
    server.shutdown()
    thread.join(timeout=0.1)
    if thread.exc:
        raise thread.exc
