# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

# Minimal local stub that shadows the installed numpy on mypy_path.
#
# numpy is only a transitive dependency of the optional on-device voice stack
# (faster-whisper -> ctranslate2/onnxruntime); it is not a declared Anki
# dependency. The installed numpy 2.x ships PEP 695 `type` statements in its
# stubs that mypy cannot parse at the pinned 3.10 target, and per-module
# follow_imports=skip does not suppress that parse. This trivial stub makes mypy
# treat numpy as untyped (Any) without reading the broken real stubs.

from typing import Any

def __getattr__(name: str) -> Any: ...
