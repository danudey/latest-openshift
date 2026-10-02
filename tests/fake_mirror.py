"""A synthetic mirror.openshift.com, served through an httpx mock transport.

Only the shapes the code actually depends on are modelled: Apache-style
directory indexes, ``release.txt`` files, and archive downloads.
"""

from __future__ import annotations

import httpx

BASE = "https://mirror.openshift.com/pub"


def index_page(entries: list[str]) -> str:
    """An Apache autoindex page listing ``entries``."""
    rows = "\n".join(
        f'<tr><td><a href="{entry}">{entry}</a></td><td>-</td></tr>' for entry in entries
    )
    return (
        "<html><head><title>Index</title></head><body><h1>Index</h1><table>"
        '<tr><td><a href="../">Parent Directory</a></td></tr>'
        f"{rows}</table></body></html>"
    )


def release_txt(version: str) -> str:
    return (
        "Client tools for OpenShift\n--------------------------\n\n"
        f"Name:           {version}\n"
        "Digest:         sha256:0000\n"
        "OS/Arch:        linux/amd64\n"
    )


class FakeMirror:
    """Routes requests to a dict of path -> body, and counts the traffic."""

    def __init__(self, pages: dict[str, str], blobs: dict[str, bytes] | None = None):
        self.pages = pages
        self.blobs = blobs or {}
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = str(request.url)
        self.requests.append(path)

        if path in self.blobs:
            body = self.blobs[path]
            return httpx.Response(
                200, content=body, headers={"content-length": str(len(body))}
            )

        for candidate in (path, path.rstrip("/")):
            if candidate in self.pages:
                return httpx.Response(200, text=self.pages[candidate])

        return httpx.Response(404, text="Not Found")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport(), follow_redirects=True)


#: A tree with 4.20-4.22 released, a 4.22.99 build published but not released,
#: and 5.0.0-ec.1 available only through the dev-preview channel.
STREAMS = {"4.20": "4.20.37", "4.21": "4.21.32", "4.22": "4.22.13"}

CLIENT_FILES = [
    "openshift-client-linux-{v}.tar.gz",
    "openshift-client-linux-arm64-{v}.tar.gz",
    "openshift-client-mac-arm64-{v}.tar.gz",
    "openshift-install-linux-{v}.tar.gz",
    "ccoctl-linux-{v}.tar.gz",
    "opm-linux-{v}.tar.gz",
    "oc-mirror.tar.gz",
    "release.txt",
    "sha256sum.txt",
]


def _release_dir_files(version: str) -> list[str]:
    return [name.format(v=version) for name in CLIENT_FILES]


def build_pages() -> dict[str, str]:
    pages: dict[str, str] = {
        f"{BASE}/": index_page(["openshift-v4/", "openshift-v5/", "openshift-v3/"]),
    }

    ocp_entries = [
        *[f"latest-{stream}/" for stream in STREAMS],
        *[f"stable-{stream}/" for stream in STREAMS],
        "latest/",
        "stable/",
        "4.22.13/",
        "4.22.99/",
        "unreleased/",
    ]

    for major in (4, 5):
        for arch in ("x86_64", "arm64", "ppc64le", "s390x"):
            root = f"{BASE}/openshift-v{major}/{arch}/clients"
            pages[f"{root}/ocp/"] = index_page(ocp_entries)
            pages[f"{root}/ocp-dev-preview/"] = index_page(["5.0.0-ec.1/"])

            for stream, version in STREAMS.items():
                for name in (f"latest-{stream}", f"stable-{stream}"):
                    pages[f"{root}/ocp/{name}/"] = index_page(_release_dir_files(version))
                    pages[f"{root}/ocp/{name}/release.txt"] = release_txt(version)

            for version in ("4.22.13", "4.22.99"):
                pages[f"{root}/ocp/{version}/"] = index_page(_release_dir_files(version))
                pages[f"{root}/ocp/{version}/release.txt"] = release_txt(version)

            preview = f"{root}/ocp-dev-preview/5.0.0-ec.1"
            pages[f"{preview}/"] = index_page(_release_dir_files("5.0.0-ec.1"))
            pages[f"{preview}/release.txt"] = release_txt("5.0.0-ec.1")

    return pages
