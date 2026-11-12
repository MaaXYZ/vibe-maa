from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

EventCallback = Callable[[dict[str, Any], np.ndarray | None], None]


class Backend(Protocol):
    def capture(self) -> np.ndarray: ...

    def ocr(self, image: np.ndarray) -> list[dict[str, Any]]: ...

    def act(self, action: dict[str, Any]) -> bool: ...

    def recognize(self, image: np.ndarray, node: dict[str, Any]) -> dict[str, Any]: ...

    def run(
        self,
        entry: str,
        override: dict[str, Any],
        timeout_seconds: float,
        on_event: EventCallback,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...
