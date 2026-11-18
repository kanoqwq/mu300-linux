"""Reserve the UMS9620 AUDCP area before the page allocator starts."""
from pathlib import Path


def apply(tree):
    path = Path(tree) / 'drivers/of/fdt.c'
    text = path.read_text()
    include = '#include "ums9620-audio-memory.h"\n\n'
    call = '\n\tums9620_reserve_audio_memory();'
    signature = 'void __init early_init_fdt_scan_reserved_mem(void)\n'
    start = text.index(signature)
    end = text.index('\n}', start)
    body = text[start:end]
    if call not in body:
        if body.count('\tmemblock_reserve(base, size);') != 1:
            raise ValueError('Unexpected early FDT reservation lifecycle')
        text = text[:end] + call + text[end:]
    if include not in text:
        text = text.replace(signature, include + signature, 1)
    if text != path.read_text():
        path.write_text(text)
