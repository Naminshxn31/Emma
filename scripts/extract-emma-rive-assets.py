"""Extract the approved Emma reference artwork into Rive-ready layers."""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "client" / "assets" / "emma" / "emma-puppet-atlas-reference-v2.png"
OUTPUT = ROOT / "client" / "rive" / "emma" / "assets"

# Fixed transparent crops preserve predictable pivots in scene.rml.
CROPS = {
    "head.png": (20, 45, 605, 595),
    "body.png": (175, 570, 490, 885),
    "arm-left.png": (20, 580, 200, 815),
    "arm-right.png": (465, 580, 645, 815),
    "eye-left.png": (610, 160, 755, 280),
    "eye-right.png": (755, 160, 900, 280),
    "mouth-smile.png": (640, 360, 825, 480),
    "mouth-open.png": (855, 345, 1045, 490),
    "ring.png": (95, 850, 570, 995),
}


def keep_main_component(image: Image.Image) -> Image.Image:
    """Remove disconnected artwork that belongs to neighbouring atlas cells."""
    width, height = image.size
    alpha = image.getchannel("A").tobytes()
    visited = bytearray(width * height)
    largest: list[int] = []

    for start, value in enumerate(alpha):
        if value <= 8 or visited[start]:
            continue
        component: list[int] = []
        pending = [start]
        visited[start] = 1
        while pending:
            index = pending.pop()
            component.append(index)
            y, x = divmod(index, width)
            for next_y in range(max(0, y - 1), min(height, y + 2)):
                row = next_y * width
                for next_x in range(max(0, x - 1), min(width, x + 2)):
                    neighbour = row + next_x
                    if alpha[neighbour] > 8 and not visited[neighbour]:
                        visited[neighbour] = 1
                        pending.append(neighbour)
        if len(component) > len(largest):
            largest = component

    cleaned_alpha = bytearray(width * height)
    for index in largest:
        cleaned_alpha[index] = alpha[index]
    cleaned = image.copy()
    cleaned.putalpha(Image.frombytes("L", image.size, bytes(cleaned_alpha)))
    return cleaned


def main() -> None:
    image = Image.open(SOURCE).convert("RGBA")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, box in CROPS.items():
        layer = keep_main_component(image.crop(box))
        layer.save(OUTPUT / name, optimize=True)
        print(f"wrote {OUTPUT / name}")


if __name__ == "__main__":
    main()
