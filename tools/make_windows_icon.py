"""Convert the generated transparent master to Windows ICO sizes.

Requires Pillow. No drawing or creative image edits are performed here.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / 'models_storage' / 'branding' / 'icons'
SIZES = [(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)]


def main() -> None:
    source = ICONS / 'mini_agent_head_v2.png'
    target = source.with_suffix('.ico')
    with Image.open(source) as original:
        master = original.convert('RGBA')
        if master.width != master.height:
            raise ValueError('The icon master must be square')
        if master.getchannel('A').getextrema()[0] != 0:
            raise ValueError('The icon master must have transparent margins')
        master.save(target, format='ICO', sizes=SIZES)
    with Image.open(target) as result:
        assert result.ico.sizes() == set(SIZES)
    print(f'Created {target}: {len(SIZES)} sizes, transparent RGBA')


if __name__ == '__main__':
    main()
