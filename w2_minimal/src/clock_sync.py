import re
import statistics
import time

import pexpect

from terminal_io import ECHO_GAP, clean_digits, gapped_literal


TIMESTAMP_19 = rf"(\d(?:{ECHO_GAP}\d){{18}})"


# Ước lượng chênh lệch clock bằng midpoint của nhiều round trip probe.
def estimate_clock_offset(child, runner, requested_probes, minimum_probes, timeout):
    marker = "W2_CLOCK_TS:"
    pattern = re.compile(gapped_literal(marker) + TIMESTAMP_19)
    offsets = []
    rtts_ms = []
    errors = []

    for _ in range(requested_probes):
        try:
            t0 = time.time_ns()
            child.sendline(f'printf "{marker}%s\\n" "$(date +%s%N)"')
            child.expect(pattern, timeout=timeout)
            t1 = time.time_ns()
            remote_ns = int(clean_digits(child.match.group(1)))
            offsets.append(((t0 + t1) // 2) - remote_ns)
            rtts_ms.append((t1 - t0) / 1_000_000.0)
            runner.expect_prompt(child, timeout)
        except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
            errors.append(type(exc).__name__)

    if len(offsets) < minimum_probes:
        raise RuntimeError(
            f"clock sync has {len(offsets)}/{requested_probes} valid probes; "
            f"minimum={minimum_probes}; errors={errors}"
        )
    return {
        "requested_probes": requested_probes,
        "valid_probes": len(offsets),
        "clock_offset_ns": int(statistics.median(offsets)),
        "median_rtt_ms": statistics.median(rtts_ms),
        "method": "midpoint_round_trip_median_offset",
    }
