"""Resettable mailbox fixture; pipeline execution uses the real MaaFramework Tasker.

Only the UI and its observation OCR are synthetic. The latter decodes the supplied
frame, so historical observations remain independent from the current UI state.
"""

from pathlib import Path
from typing import Any

import numpy as np
from maa.controller import CustomController
from PIL import Image, ImageDraw

from vibe_maa.backends.maa import MaaBackend
from vibe_maa.errors import DevError


class DemoWorld:
    COLORS = {"home": 20, "mailbox": 40, "popup": 60}

    def __init__(self, initial_state: str = "home") -> None:
        if initial_state not in self.COLORS:
            raise DevError("INVALID_ARGUMENT", "initial_state must be home, mailbox, or popup")
        self.state = initial_state
        self.actions: list[dict[str, Any]] = []

    @staticmethod
    def _elements(state: str) -> list[dict[str, Any]]:
        elements = {
            "home": [("Home", [20, 20, 120, 40]), ("Mailbox", [220, 140, 200, 60])],
            "mailbox": [
                ("Back", [20, 20, 100, 50]),
                ("Mailbox", [220, 20, 200, 40]),
                ("No claimable mail", [170, 160, 300, 40]),
            ],
            "popup": [
                ("Announcement", [140, 90, 360, 50]),
                ("Confirm", [220, 210, 200, 60]),
            ],
        }
        return [{"text": text, "box": box, "score": 1.0} for text, box in elements[state]]

    def capture(self) -> np.ndarray:
        gray = self.COLORS[self.state]
        image = Image.new("RGB", (640, 360), (gray, gray, gray))
        draw = ImageDraw.Draw(image)
        buttons = {
            "home": ([220, 140, 200, 60], 200),
            "mailbox": ([20, 20, 100, 50], 160),
            "popup": ([220, 210, 200, 60], 220),
        }
        box, fill = buttons[self.state]
        x, y, w, h = box
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=(fill, fill, fill))
        for element in self._elements(self.state):
            x, y, _, _ = element["box"]
            draw.text((x + 8, y + 12), element["text"], fill=(255, 255, 255))
        return np.asarray(image)[:, :, ::-1].copy()

    @classmethod
    def ocr(cls, image: np.ndarray) -> list[dict[str, Any]]:
        if image.shape != (360, 640, 3):
            return []
        for state, gray in cls.COLORS.items():
            if np.array_equal(image[0, 0], [gray, gray, gray]):
                return cls._elements(state)
        return []

    @staticmethod
    def _inside(x: int, y: int, box: list[int]) -> bool:
        bx, by, w, h = box
        return bx <= x < bx + w and by <= y < by + h

    def click(self, x: int, y: int) -> bool:
        self.actions.append({"type": "click", "x": x, "y": y})
        if self.state == "home" and self._inside(x, y, [220, 140, 200, 60]):
            self.state = "mailbox"
        elif self.state == "mailbox" and self._inside(x, y, [20, 20, 100, 50]):
            self.state = "home"
        elif self.state == "popup" and self._inside(x, y, [220, 210, 200, 60]):
            self.state = "home"
        return True

    def key(self, keycode: int) -> bool:
        self.actions.append({"type": "key", "keycode": keycode})
        if keycode in (4, 27):
            self.state = "home"
            return True
        return False


class DemoController(CustomController):
    def __init__(self, world: DemoWorld) -> None:
        self.world = world
        super().__init__()
        self.set_screenshot_use_raw_size(True)

    def connect(self) -> bool:
        return True

    def request_uuid(self) -> str:
        return "vibe-maa-demo"

    def get_features(self) -> int:
        return 0

    def start_app(self, intent: str) -> bool:
        return False

    def stop_app(self, intent: str) -> bool:
        return False

    def screencap(self) -> np.ndarray:
        return self.world.capture()

    def click(self, x: int, y: int) -> bool:
        return self.world.click(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
        self.world.actions.append(
            {"type": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration}
        )
        return False

    def touch_down(self, contact: int, x: int, y: int, pressure: int) -> bool:
        return False

    def touch_move(self, contact: int, x: int, y: int, pressure: int) -> bool:
        return False

    def touch_up(self, contact: int) -> bool:
        return False

    def click_key(self, keycode: int) -> bool:
        return self.world.key(keycode)

    def input_text(self, text: str) -> bool:
        self.world.actions.append({"type": "text", "text": text})
        return False

    def key_down(self, keycode: int) -> bool:
        return False

    def key_up(self, keycode: int) -> bool:
        return False


class DemoBackend(MaaBackend):
    """An in-memory UI backed by real native task/recognition execution.

    ``initial_state`` controls the fixture without giving the agent reset powers
    over a real game. Opening a fresh demo session creates a fresh UI.
    """

    def __init__(
        self,
        project: Path,
        initial_state: str = "home",
        resource_path: str = "resource",
        **kwargs: Any,
    ) -> None:
        self.world = DemoWorld(initial_state)
        controller = DemoController(self.world)
        super().__init__(
            project=project,
            device="demo",
            resource_path=resource_path,
            controller=controller,
        )
        if not controller.set_screenshot_use_raw_size(True):
            raise DevError("DEVICE_ERROR", "Cannot keep demo screenshot coordinates")

    def ocr(self, image: np.ndarray) -> list[dict[str, Any]]:
        return self.world.ocr(image)
