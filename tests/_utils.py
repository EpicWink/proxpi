"""Testing utilities."""

import socket
import typing as t
import asyncio
import logging
import threading
import html.parser

import hypercorn.utils
import hypercorn.config
import hypercorn.asyncio.run

if t.TYPE_CHECKING:
    import hypercorn.typing


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


def make_server(app: "hypercorn.typing.Framework") -> t.Generator[str, None, None]:
    async def serve():
        nonlocal shutdown_future
        shutdown_future = asyncio.Future()  # create in same asyncio loop

        # Can't use `hypercorn.asyncio.serve` as it creates the socket internally, so
        # we never know the randomly-assigned port
        await hypercorn.asyncio.run.worker_serve(
            app=hypercorn.utils.wrap_app(
                app, hypercorn.Config.wsgi_max_body_size, mode=None
            ),
            config=hypercorn.Config.from_mapping({
                "loglevel": "DEBUG",
                "bind": [],
                "insecure_bind": [f"localhost:{port}"],
                "errorlog": logging.getLogger("hypercorn.error"),
            }),
            sockets=hypercorn_sockets,
            shutdown_trigger=lambda: shutdown_future,
        )  # fmt: skip

    sock = socket.socket()
    sock.bind(("localhost", 0))
    port = sock.getsockname()[1]
    hypercorn_sockets = hypercorn.config.Sockets(
        secure_sockets=[], insecure_sockets=[sock], quic_sockets=[]
    )

    shutdown_future = None  # type: t.Union[asyncio.Future, None]
    thread = Thread(target=asyncio.run, args=(serve(),))
    thread.start()
    yield f"http://localhost:{port}"
    shutdown_future.set_result(None)
    thread.join(timeout=0.1)
    if thread.exc:
        raise thread.exc
