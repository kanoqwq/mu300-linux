"""Apply only the tracked early USB role block to an installed shell init."""
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]
BEGIN = '# BEGIN MU300 EARLY USB ROLE\n'
END = '# END MU300 EARLY USB ROLE\n'

def patch_init(text):
    canonical = (TOP/'boot/init').read_text()
    block = BEGIN + canonical.split(BEGIN, 1)[1].split(END, 1)[0] + END
    root_functions = block.split('read_usb_boot_role() {', 1)[0][len(BEGIN):]
    if BEGIN not in text:
        if text.count(root_functions) != 1:
            raise ValueError('installed root-selection functions differ; review required')
        text = text.replace(root_functions, '', 1)
        anchor = 'read_usb_boot_policy() {\n'
        if text.count(anchor) != 1:
            raise ValueError('installed USB boot-policy entry differs')
        text = text.replace(anchor, block + anchor + '    read_usb_boot_role\n'
                            '    [ "${MU300_USB_EARLY_HOST:-}" != 1 ] || return 0\n', 1)
    elif block not in text:
        raise ValueError('different early USB role block already installed')
    old = '[ "${DESKTOP_USB_READY:-}" = 1 ] || setup_usb_gadget\n'
    legacy = '[ "${DESKTOP_USB_READY:-}" = 1 ] || [ "${MU300_USB_EARLY_HOST:-}" = 1 ] || setup_usb_gadget\n'
    if old not in text and legacy not in text:
        raise ValueError('installed normal gadget entry differs')
    text = text.replace(legacy, old)
    for start, end, anchor, indentation in [
        ('# BEGIN MU300 UNBOUND GADGET\n', '# END MU300 UNBOUND GADGET\n',
         '    log "stage=usb-wait-udc"\n', '    '),
        ('# BEGIN MU300 KEEP PREPARED GADGET\n', '# END MU300 KEEP PREPARED GADGET\n',
         '    umount /config 2>/dev/null\n', '    '),
    ]:
        part = indentation + start + canonical.split(start, 1)[1].split(end, 1)[0] + end
        if start not in text:
            if text.count(anchor) != 1:
                raise ValueError('installed gadget preparation/handoff differs')
            text = text.replace(anchor, part + (anchor if 'UNBOUND' in start else ''), 1)
        elif part not in text:
            raise ValueError('different gadget preparation block already installed')
    return text
