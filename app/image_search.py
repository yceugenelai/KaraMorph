"""Replaceable image-search contract. No network provider is enabled by default.

Register a SearchMethod with a request factory; the UI calls start() only after
the user clicks Search. Implementations must do I/O asynchronously, honor cancel,
and emit resultsReady with a list of ImageSearchResult. A result's local_path
must point to an already downloaded, validated image owned by the request until
it is cancelled. The editor copies selected images before cancellation.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class ImageSearchQuery:
    keywords: str
    kind: str
    title: str = ''
    artist: str = ''
    album: str = ''


@dataclass(frozen=True)
class ImageSearchResult:
    title: str
    source_url: str = ''
    local_path: Path | None = None


class ImageSearchRequest(QObject):
    resultsReady = Signal(object)
    failed = Signal(str)

    def start(self):
        raise NotImplementedError

    def cancel(self):
        raise NotImplementedError


@dataclass(frozen=True)
class SearchMethod:
    key: str
    label: str
    factory: Callable[[ImageSearchQuery, QObject], ImageSearchRequest] | None = None


class ImageSearchRegistry:
    def __init__(self):
        self.methods = {}
        for key, label in [('duckduckgo', 'DuckDuckGo'), ('google_images', 'Google Images'),
                           ('musicbrainz', 'MusicBrainz / Cover Art Archive'), ('custom', 'Custom')]:
            self.register(SearchMethod(key, label))

    def register(self, method):
        self.methods[method.key] = method

    def available_methods(self):
        return list(self.methods.values())
