import time

import pexpect

from config import bool_cfg
from constants import COMMANDS
from protocol_runner import ProtocolRunner
from terminal_io import drain_pending_output, prompt_pattern


# Tạo một dòng kết quả cho một lệnh trong loop.
def sample_row(
    trial, loop_index, warmup, command_index, command, status,
    latency="", output_bytes="", note="",
):
    return {
        **trial,
        "loop_index": loop_index,
        "warmup": int(warmup),
        "command_index": command_index,
        "command": command,
        "status": status,
        "latency_ms": latency,
        "output_bytes": output_bytes,
        "note": note,
    }


# Điền đủ các vị trí chưa thể chạy sau khi session hoặc loop hỏng.
def unavailable_rows(
    trial, start_loop, total_loops, warmup_loops,
    first_command=1, status="trial_unavailable", note="",
):
    rows = []
    for loop_index in range(start_loop, total_loops + 1):
        command_start = first_command if loop_index == start_loop else 1
        for command_index in range(command_start, len(COMMANDS) + 1):
            rows.append(sample_row(
                trial, loop_index, loop_index <= warmup_loops, command_index,
                COMMANDS[command_index - 1], status, note=note,
            ))
    return rows


# Chạy một session độc lập và tuần tự mọi lệnh trong từng loop.
def run_trial(cfg, trial, total_loops, warmup_loops):
    protocol = trial["protocol"]
    prompt_marker = f"__W1_{trial['trial_order']:03d}_{trial['trial_id']}_PROMPT__# "
    timeout = float(cfg.get("COMMAND_TIMEOUT", "30"))
    runner = ProtocolRunner(cfg, protocol, prompt_marker)
    child = None
    rows, loops = [], []
    setup = {
        **trial,
        "status": "trial_unavailable",
        "session_setup_ms": "",
        "note": "",
    }
    try:
        child, setup_ms = runner.open()
        setup.update(status="success", session_setup_ms=f"{setup_ms:.3f}")
        if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
            print(f"[READY] trial={trial['trial_id']} session_setup_ms={setup_ms:.3f}", flush=True)

        for loop_index in range(1, total_loops + 1):
            warmup = loop_index <= warmup_loops
            loop_started = time.perf_counter_ns()
            completed = 0
            loop_note = ""
            loop_status = "success"
            for command_index, command in enumerate(COMMANDS, start=1):
                drain_pending_output(child)
                started = time.perf_counter_ns()
                try:
                    child.sendline(command)
                    child.expect(prompt_pattern(prompt_marker), timeout=timeout)
                    ended = time.perf_counter_ns()
                    latency = (ended - started) / 1_000_000.0
                    output = child.before or ""
                    rows.append(sample_row(
                        trial, loop_index, warmup, command_index, command, "success",
                        f"{latency:.3f}", len(output.encode("utf-8", errors="replace")),
                    ))
                    completed += 1
                    if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
                        print(
                            f"[LIVE] trial={trial['trial_id']} "
                            f"loop={loop_index:03d}/{total_loops:03d} "
                            f"command={command_index}/5 status=success "
                            f"latency_ms={latency:.3f}",
                            flush=True,
                        )
                except pexpect.TIMEOUT as exc:
                    loop_status, loop_note = "timeout", str(exc)
                except pexpect.EOF as exc:
                    loop_status, loop_note = "eof", str(exc)
                except Exception as exc:
                    loop_status, loop_note = "failure", repr(exc)

                if loop_status != "success":
                    rows.append(sample_row(
                        trial, loop_index, warmup, command_index, command,
                        loop_status, note=loop_note,
                    ))
                    rows.extend(unavailable_rows(
                        trial, loop_index, total_loops, warmup_loops,
                        first_command=command_index + 1,
                        status="skipped", note=loop_note,
                    ))
                    break

            elapsed = (time.perf_counter_ns() - loop_started) / 1_000_000.0
            loops.append({
                **trial,
                "loop_index": loop_index,
                "warmup": int(warmup),
                "status": loop_status,
                "completed_commands": completed,
                "loop_latency_ms": f"{elapsed:.3f}" if loop_status == "success" else "",
                "note": loop_note,
            })
            if loop_status != "success":
                for remaining in range(loop_index + 1, total_loops + 1):
                    loops.append({
                        **trial,
                        "loop_index": remaining,
                        "warmup": int(remaining <= warmup_loops),
                        "status": "skipped",
                        "completed_commands": 0,
                        "loop_latency_ms": "",
                        "note": loop_note,
                    })
                break
    except Exception as exc:
        note = repr(exc)
        if setup["status"] != "success":
            if isinstance(exc, pexpect.TIMEOUT):
                setup["status"] = "timeout"
            elif isinstance(exc, pexpect.EOF):
                setup["status"] = "eof"
            else:
                setup["status"] = "failure"
            setup["note"] = note
        rows.extend(unavailable_rows(trial, 1, total_loops, warmup_loops, note=note))
        for loop_index in range(1, total_loops + 1):
            loops.append({
                **trial,
                "loop_index": loop_index,
                "warmup": int(loop_index <= warmup_loops),
                "status": "trial_unavailable",
                "completed_commands": 0,
                "loop_latency_ms": "",
                "note": note,
            })
        print(f"[FAIL] trial={trial['trial_id']} note={note}", flush=True)
    finally:
        if child is not None:
            runner.close(child)
    return rows, loops, setup

