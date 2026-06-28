"""Tests for TUI pixel pet widget."""

from cc.tui.pet import PixelPetWidget


def test_pet_falls_back_without_asset() -> None:
    pet = PixelPetWidget(pet_dir="Z:/definitely/missing")

    assert not pet.is_pixel_pet_loaded
    assert "idle" in pet.available_states()


def test_pet_state_unknown_falls_back_to_idle() -> None:
    pet = PixelPetWidget(pet_dir="Z:/definitely/missing")

    pet.set_state("unknown")

    assert pet.state == "idle"


def test_pixel_pair_uses_half_blocks() -> None:
    pet = PixelPetWidget(pet_dir="Z:/definitely/missing")

    assert pet._pixel_pair_to_cell((1, 2, 3, 255), (4, 5, 6, 255))[0] == "\u2580"
    assert pet._pixel_pair_to_cell((1, 2, 3, 0), (4, 5, 6, 255))[0] == "\u2584"


def test_padded_bbox_clamps_to_image_bounds() -> None:
    pet = PixelPetWidget(pet_dir="Z:/definitely/missing")

    assert pet._padded_bbox((2, 3, 8, 9), (10, 10), padding=5) == (0, 0, 10, 10)
