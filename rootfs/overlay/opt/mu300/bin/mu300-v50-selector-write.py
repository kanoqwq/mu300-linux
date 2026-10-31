#!/usr/bin/python3
"""Cold-boot helper only. One write, explicit errno and bounded read verification."""
import json
import os
import sys
import time
from pathlib import Path


def verify_result(write, samples, target, expected_chip):
    stable = len(samples) == 3 and all(
        sample == {"psim": target, "chip": expected_chip} for sample in samples
    )
    normal = write["errno"] is None and write["bytes_written"] == 1
    # A full successful write and unchanged peer selector are both required.
    # Earlier field shell failures did not capture errno; do not assume EIO.
    accepted = stable and normal
    return {
        "write": write,
        "readback_samples": samples,
        "accepted": accepted,
        "acceptance": (
            "complete_write_and_stable_readback" if accepted else "rejected"
        ),
    }


def write_once(hw, target):
    result = {"phase": "open", "bytes_requested": 1, "bytes_written": None,
              "errno": None, "error": None}
    fd = None
    try:
        fd = os.open(str(hw / "psim_select"), os.O_WRONLY | os.O_CLOEXEC)
        result["phase"] = "write"
        # sysfs appends its own NUL. A single complete numeric value needs no
        # newline; os.write records the actual syscall result without buffering.
        result["bytes_written"] = os.write(fd, str(target).encode("ascii"))
    except OSError as exc:
        result["errno"] = exc.errno
        result["error"] = str(exc)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError as exc:
                # A close failure is distinct and never treated as success.
                result["phase"] = "close"
                result["errno"] = exc.errno
                result["error"] = str(exc)
    return result


def main():
    # Prevent accidental direct invocation outside the already-guarded helper.
    parent = os.getppid()
    expected_parent = os.environ.get("MU300_V50_WRITER_PARENT")
    expected_arg = os.environ.get("MU300_BIN", "/opt/mu300/bin") + "/mu300-v50-sim-preboot"
    cmdline = Path(f"/proc/{parent}/cmdline").read_bytes().split(b"\0")
    if expected_parent != str(parent) or expected_arg.encode() not in cmdline:
        raise RuntimeError("cold_boot_helper_required")
    if os.geteuid() != 0 or len(sys.argv) != 4:
        raise RuntimeError("invalid_invocation")
    hw = Path(sys.argv[1])
    if str(hw) != os.environ.get("MU300_V50_MUX", "/sys/bus/platform/devices/sim_gpio_ctl"):
        raise RuntimeError("unexpected_device_path")
    target, expected_chip = (int(v) for v in sys.argv[2:])
    if target not in (0, 1) or expected_chip not in (0, 1):
        raise RuntimeError("invalid_selector_value")
    write = write_once(hw, target)
    samples = []
    for delay in (0, 0.02, 0.03):
        if delay:
            time.sleep(delay)
        try:
            samples.append({"psim": int((hw / "psim_select").read_text().strip()),
                            "chip": int((hw / "chip_select").read_text().strip())})
        except (OSError, ValueError) as exc:
            samples.append({"read_error": str(exc)})
    result = verify_result(write, samples, target, expected_chip)
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"accepted": False, "helper_error": str(exc)}), flush=True)
        sys.exit(1)
