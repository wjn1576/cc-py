"""Terminal pixel pet widget for the Textual TUI.

The widget can render a Codex-style pet spritesheet as ANSI-colored block
characters. If Pillow or a spritesheet is unavailable, it falls back to a
small ASCII pet so the TUI remains usable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.widgets import Static


class PixelPetWidget(Static):
    """Animated pet rendered inside a terminal."""

    FALLBACK_FRAMES: ClassVar[dict[str, list[str]]] = {
        "idle": [
            " /\\_/\\\\\n( o.o )\n > ^ < ",
            " /\\_/\\\\\n( -.- )\n > ^ < ",
        ],
        "thinking": [
            " /\\_/\\\\  ?\n( o.o )\n > ^ < ",
            " /\\_/\\\\  .\n( o.o )\n > ^ < ",
        ],
        "talking": [
            " /\\_/\\\\\n( o.o )\n > o < ",
            " /\\_/\\\\\n( o.o )\n > ^ < ",
        ],
        "working": [
            " /\\_/\\\\\n( >.< )\n /|_|\\ ",
            " /\\_/\\\\\n( >.< )\n \\|_|/ ",
        ],
        "error": [
            " /\\_/\\\\\n( x.x )\n > ! < ",
        ],
    }

    STATE_ROWS: ClassVar[dict[str, int]] = {
        "idle": 0,
        "thinking": 1,
        "talking": 2,
        "working": 3,
        "success": 4,
        "error": 5,
        "sleep": 6,
        "compact": 7,
    }

    def __init__(
        self,
        *,
        pet_dir: str | None = None,
        frame_width: int = 192,
        frame_height: int = 208,
        render_width: int = 22,
        render_height: int = 18,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.pet_dir = self._resolve_pet_dir(pet_dir)
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.render_width = render_width
        self.render_height = render_height
        self.state = "idle"
        self.frame_index = 0
        self._rendered_frames: dict[str, list[Text]] = {}
        self._loaded_pixel_pet = False
        self._load_pixel_frames()

    def on_mount(self) -> None:
        self.set_interval(0.45, self.next_frame)
        self.next_frame()

    def set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state if state in self.available_states() else "idle"
        self.frame_index = 0
        if self.is_mounted:
            self.next_frame()

    def available_states(self) -> set[str]:
        if self._rendered_frames:
            return set(self._rendered_frames)
        return set(self.FALLBACK_FRAMES)

    def next_frame(self) -> None:
        frames = self._rendered_frames.get(self.state)
        if frames:
            self.update(frames[self.frame_index % len(frames)])
            self.frame_index += 1
            return

        fallback = self.FALLBACK_FRAMES.get(self.state) or self.FALLBACK_FRAMES["idle"]
        self.update(Text(fallback[self.frame_index % len(fallback)], style="bold #ff7a45"))
        self.frame_index += 1

    @property
    def is_pixel_pet_loaded(self) -> bool:
        return self._loaded_pixel_pet

    def _resolve_pet_dir(self, pet_dir: str | None) -> Path | None:
        candidates: list[Path] = []
        if pet_dir:
            candidate = Path(pet_dir).expanduser()
            return candidate if (candidate / "pet.json").is_file() else None
        if env_dir := os.environ.get("CC_PY_PET_DIR"):
            candidate = Path(env_dir).expanduser()
            return candidate if (candidate / "pet.json").is_file() else None
        candidates.append(Path.home() / ".codex" / "pets" / "ddo-zvzo-2")

        for candidate in candidates:
            if (candidate / "pet.json").is_file():
                return candidate
        return None

    def _load_pixel_frames(self) -> None:
        if self.pet_dir is None:
            return

        try:
            from PIL import Image
        except ImportError:
            return

        try:
            metadata = json.loads((self.pet_dir / "pet.json").read_text(encoding="utf-8"))
            spritesheet_path = self.pet_dir / metadata.get("spritesheetPath", "spritesheet.webp")
            sheet = Image.open(spritesheet_path).convert("RGBA")
        except (OSError, json.JSONDecodeError):
            return

        cols = max(1, sheet.width // self.frame_width)
        rows = max(1, sheet.height // self.frame_height)
        cell_w = sheet.width // cols
        cell_h = sheet.height // rows

        for state, row in self.STATE_ROWS.items():
            if row >= rows:
                continue
            frames: list[Text] = []
            for col in range(cols):
                box = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
                frame = sheet.crop(box)
                rendered = self._frame_to_text(frame)
                if rendered.plain.strip():
                    frames.append(rendered)
            if frames:
                self._rendered_frames[state] = frames

        self._loaded_pixel_pet = bool(self._rendered_frames)

    def _frame_to_text(self, image: object) -> Text:
        from PIL import Image

        bbox = image.getbbox()
        if bbox is None:
            return Text()

        img = image.crop(self._padded_bbox(bbox, image.size, padding=10))
        img = img.resize((self.render_width, self.render_height), Image.Resampling.BOX)
        pixels = img.load()
        text = Text()

        for y in range(0, img.height, 2):
            for x in range(img.width):
                top = pixels[x, y]
                bottom = pixels[x, y + 1] if y + 1 < img.height else (0, 0, 0, 0)
                char, style = self._pixel_pair_to_cell(top, bottom)
                text.append(char, style=style)
            if y + 2 < img.height:
                text.append("\n")
        return text

    def _padded_bbox(
        self,
        bbox: tuple[int, int, int, int],
        size: tuple[int, int],
        *,
        padding: int,
    ) -> tuple[int, int, int, int]:
        left, top, right, bottom = bbox
        width, height = size
        return (
            max(0, left - padding),
            max(0, top - padding),
            min(width, right + padding),
            min(height, bottom + padding),
        )

    def _pixel_pair_to_cell(
        self,
        top: tuple[int, int, int, int],
        bottom: tuple[int, int, int, int],
    ) -> tuple[str, str]:
        top_visible = top[3] > 48
        bottom_visible = bottom[3] > 48
        if top_visible and bottom_visible:
            return "\u2580", f"rgb({top[0]},{top[1]},{top[2]}) on rgb({bottom[0]},{bottom[1]},{bottom[2]})"
        if top_visible:
            return "\u2580", f"rgb({top[0]},{top[1]},{top[2]})"
        if bottom_visible:
            return "\u2584", f"rgb({bottom[0]},{bottom[1]},{bottom[2]})"
        return " ", ""
