#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import socket
import ssl
import struct
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

HEADER = struct.Struct("!HBBIQI")
MSG_HELLO = 1
MSG_PROBE = 2
MSG_ECHO = 3
MSG_BG = 4

ROLE_CHANNELS = {
    "interactive": 0,
    "log": 4,
    "ping": 8,
    "sysmon": 12,
    "output": 16,
    "output_heavy": 16,
}

PROFILES = {
    "c0_only": [],
    "c0_bg2": ["log", "ping"],
    "c0_bg4": ["log", "ping", "sysmon", "output"],
    "c0_bg4_heavy": ["log", "ping", "sysmon", "output_heavy"],
}

PROBE_SEQUENCE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
ALPN = ["muxbench"]
MAX_FRAME = 1_000_000


@dataclass
class Frame:
    channel_id: int
    msg_type: int
    flags: int
    seq: int
    sent_ns: int
    payload: bytes


def now_ns() -> int:
    return time.perf_counter_ns()


def pack_frame(channel_id: int, msg_type: int, seq: int, sent_ns: int, payload: bytes = b"", flags: int = 0) -> bytes:
    if len(payload) > MAX_FRAME:
        raise ValueError(f"payload too large: {len(payload)}")
    return HEADER.pack(channel_id, msg_type, flags, seq, sent_ns, len(payload)) + payload


def unpack_from_buffer(buf: bytearray) -> list[Frame]:
    frames: list[Frame] = []
    while len(buf) >= HEADER.size:
        channel_id, msg_type, flags, seq, sent_ns, length = HEADER.unpack(buf[:HEADER.size])
        if length > MAX_FRAME:
            raise ValueError(f"frame length too large: {length}")
        total = HEADER.size + length
        if len(buf) < total:
            break
        payload = bytes(buf[HEADER.size:total])
        del buf[:total]
        frames.append(Frame(channel_id, msg_type, flags, seq, sent_ns, payload))
    return frames


async def read_tcp_frame(reader: asyncio.StreamReader) -> Frame:
    header = await reader.readexactly(HEADER.size)
    channel_id, msg_type, flags, seq, sent_ns, length = HEADER.unpack(header)
    if length > MAX_FRAME:
        raise ValueError(f"frame length too large: {length}")
    payload = await reader.readexactly(length) if length else b""
    return Frame(channel_id, msg_type, flags, seq, sent_ns, payload)


async def write_tcp_frame(writer: asyncio.StreamWriter, lock: asyncio.Lock, frame: bytes) -> None:
    async with lock:
        writer.write(frame)
        await writer.drain()


def profile_roles(profile: str) -> list[str]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    return PROFILES[profile]


def role_payload(role: str, seq: int) -> bytes:
    prefix = f"{role} seq={seq:08d} ts={time.time_ns()} ".encode("ascii")
    if role == "output_heavy":
        return prefix + (b"X" * 4096)
    if role == "output":
        return prefix + (b"abcdefghijklmnopqrstuvwxyz0123456789" * 4)
    if role == "sysmon":
        return prefix + b"cpu=12 mem=34 load=0.42"
    if role == "ping":
        return prefix + b"rtt=0.1ms"
    return prefix + b"log-line"


def role_interval(role: str) -> float:
    if role == "output_heavy":
        return 0.001
    if role == "output":
        return 0.05
    if role == "ping":
        return 0.20
    if role == "sysmon":
        return 1.00
    return 0.10


class EchoRouter:
    def __init__(self) -> None:
        self._waiters: dict[int, asyncio.Queue[Frame]] = {}
        self._stale: dict[int, Frame] = {}

    async def put(self, frame: Frame) -> None:
        queue = self._waiters.get(frame.seq)
        if queue is not None:
            await queue.put(frame)
        else:
            self._stale[frame.seq] = frame

    async def wait(self, seq: int, timeout: float) -> Frame:
        stale = self._stale.pop(seq, None)
        if stale is not None:
            return stale
        queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=1)
        self._waiters[seq] = queue
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        finally:
            self._waiters.pop(seq, None)


class CsvOutputs:
    def __init__(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self.samples_path = out_dir / "mux_samples.csv"
        self.stream_map_path = out_dir / "mux_stream_map.csv"
        self.sample_fields = [
            "ts", "protocol", "profile", "stream_role", "app_channel_id",
            "transport_stream_id", "sample_idx", "probe", "latency_ms",
            "status", "note",
        ]
        self.map_fields = [
            "ts", "protocol", "profile", "stream_role", "app_channel_id",
            "transport_stream_id", "connection_tag", "note",
        ]
        self._init_file(self.samples_path, self.sample_fields)
        self._init_file(self.stream_map_path, self.map_fields)

    @staticmethod
    def _init_file(path: Path, fields: list[str]) -> None:
        if path.exists() and path.stat().st_size > 0:
            return
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()

    def sample(self, row: dict) -> None:
        with self.samples_path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.sample_fields).writerow(row)

    def stream_map(self, row: dict) -> None:
        with self.stream_map_path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.map_fields).writerow(row)


async def tcp_background_sender(writer: asyncio.StreamWriter, lock: asyncio.Lock, role: str) -> None:
    channel_id = ROLE_CHANNELS[role]
    seq = 0
    interval = role_interval(role)
    while True:
        payload = role_payload(role, seq)
        await write_tcp_frame(writer, lock, pack_frame(channel_id, MSG_BG, seq, now_ns(), payload))
        seq += 1
        await asyncio.sleep(interval)


async def handle_tcp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    lock = asyncio.Lock()
    tasks: list[asyncio.Task] = []
    try:
        while True:
            frame = await read_tcp_frame(reader)
            if frame.msg_type == MSG_HELLO:
                role = frame.payload.decode("ascii", "ignore")
                if role != "interactive":
                    tasks.append(asyncio.create_task(tcp_background_sender(writer, lock, role)))
            elif frame.msg_type == MSG_PROBE:
                echo = pack_frame(frame.channel_id, MSG_ECHO, frame.seq, frame.sent_ns, frame.payload)
                await write_tcp_frame(writer, lock, echo)
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def run_tcp_server(args: argparse.Namespace) -> None:
    server = await asyncio.start_server(handle_tcp_client, args.host, args.port)
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"[server/tcp] listening on {sockets}", flush=True)
    async with server:
        await server.serve_forever()


async def tcp_reader_loop(reader: asyncio.StreamReader, router: EchoRouter) -> None:
    while True:
        frame = await read_tcp_frame(reader)
        if frame.msg_type == MSG_ECHO:
            await router.put(frame)


async def run_tcp_client_profile(args: argparse.Namespace, profile: str, outputs: CsvOutputs) -> None:
    reader, writer = await asyncio.open_connection(args.host, args.port)
    lock = asyncio.Lock()
    router = EchoRouter()
    reader_task = asyncio.create_task(tcp_reader_loop(reader, router))
    roles = ["interactive"] + profile_roles(profile)
    for role in roles:
        channel_id = ROLE_CHANNELS[role]
        await write_tcp_frame(writer, lock, pack_frame(channel_id, MSG_HELLO, 0, now_ns(), role.encode("ascii")))
        outputs.stream_map({
            "ts": time.time(), "protocol": "tcp", "profile": profile, "stream_role": role,
            "app_channel_id": channel_id, "transport_stream_id": "",
            "connection_tag": "one_tcp_connection", "note": "logical channel in one TCP byte stream",
        })
    await asyncio.sleep(args.warmup_seconds)
    await measure_profile(args, outputs, "tcp", profile, ROLE_CHANNELS["interactive"], "", send_tcp_probe(writer, lock), router)
    reader_task.cancel()
    writer.close()
    await writer.wait_closed()


def send_tcp_probe(writer: asyncio.StreamWriter, lock: asyncio.Lock):
    async def _send(seq: int, sent_ns: int, probe: bytes) -> None:
        await write_tcp_frame(writer, lock, pack_frame(ROLE_CHANNELS["interactive"], MSG_PROBE, seq, sent_ns, probe))
    return _send


class UdpServerProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.bg_tasks: dict[tuple[tuple[str, int], str], asyncio.Task] = {}

    def connection_made(self, transport):
        self.transport = transport
        print(f"[server/udp] listening on {transport.get_extra_info('sockname')}", flush=True)

    def datagram_received(self, data: bytes, addr):
        buf = bytearray(data)
        try:
            frames = unpack_from_buffer(buf)
        except Exception:
            return
        for frame in frames:
            if frame.msg_type == MSG_HELLO:
                role = frame.payload.decode("ascii", "ignore")
                if role != "interactive":
                    key = (addr, role)
                    if key not in self.bg_tasks:
                        self.bg_tasks[key] = asyncio.create_task(self._bg_sender(addr, role))
            elif frame.msg_type == MSG_PROBE and self.transport is not None:
                self.transport.sendto(pack_frame(frame.channel_id, MSG_ECHO, frame.seq, frame.sent_ns, frame.payload), addr)

    async def _bg_sender(self, addr, role: str) -> None:
        assert self.transport is not None
        seq = 0
        interval = role_interval(role)
        channel_id = ROLE_CHANNELS[role]
        while True:
            self.transport.sendto(pack_frame(channel_id, MSG_BG, seq, now_ns(), role_payload(role, seq)), addr)
            seq += 1
            await asyncio.sleep(interval)


async def run_udp_server(args: argparse.Namespace) -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: UdpServerProtocol(), local_addr=(args.host, args.port))
    try:
        await asyncio.Event().wait()
    finally:
        transport.close()


class UdpClientProtocol(asyncio.DatagramProtocol):
    def __init__(self, router: EchoRouter) -> None:
        self.router = router
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        buf = bytearray(data)
        try:
            frames = unpack_from_buffer(buf)
        except Exception:
            return
        for frame in frames:
            if frame.msg_type == MSG_ECHO:
                asyncio.create_task(self.router.put(frame))


async def run_udp_client_profile(args: argparse.Namespace, profile: str, outputs: CsvOutputs) -> None:
    router = EchoRouter()
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UdpClientProtocol(router), remote_addr=(args.host, args.port)
    )
    roles = ["interactive"] + profile_roles(profile)
    for role in roles:
        channel_id = ROLE_CHANNELS[role]
        transport.sendto(pack_frame(channel_id, MSG_HELLO, 0, now_ns(), role.encode("ascii")))
        outputs.stream_map({
            "ts": time.time(), "protocol": "udp", "profile": profile, "stream_role": role,
            "app_channel_id": channel_id, "transport_stream_id": "",
            "connection_tag": "one_udp_socket_flow", "note": "application channel over UDP datagrams",
        })
    await asyncio.sleep(args.warmup_seconds)

    async def send(seq: int, sent_ns: int, probe: bytes) -> None:
        transport.sendto(pack_frame(ROLE_CHANNELS["interactive"], MSG_PROBE, seq, sent_ns, probe))

    await measure_profile(args, outputs, "udp", profile, ROLE_CHANNELS["interactive"], "", send, router)
    transport.close()


def import_aioquic():
    try:
        from aioquic.asyncio import QuicConnectionProtocol, connect, serve
        from aioquic.quic.configuration import QuicConfiguration
        from aioquic.quic.events import StreamDataReceived
    except ImportError as exc:
        raise SystemExit(
            "Missing QUIC dependency: aioquic. Install on both client and Pi with: python -m pip install aioquic"
        ) from exc
    return QuicConnectionProtocol, connect, serve, QuicConfiguration, StreamDataReceived


def make_quic_protocols():
    QuicConnectionProtocol, connect, serve, QuicConfiguration, StreamDataReceived = import_aioquic()

    class MuxQuicServerProtocol(QuicConnectionProtocol):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.buffers: dict[int, bytearray] = defaultdict(bytearray)
            self.bg_tasks: dict[int, asyncio.Task] = {}

        def quic_event_received(self, event):
            if isinstance(event, StreamDataReceived):
                buf = self.buffers[event.stream_id]
                buf.extend(event.data)
                try:
                    frames = unpack_from_buffer(buf)
                except Exception:
                    return
                for frame in frames:
                    if frame.msg_type == MSG_HELLO:
                        role = frame.payload.decode("ascii", "ignore")
                        print(f"[server/quic] stream_id={event.stream_id} role={role}", flush=True)
                        if role != "interactive" and event.stream_id not in self.bg_tasks:
                            self.bg_tasks[event.stream_id] = asyncio.create_task(self._bg_sender(event.stream_id, role))
                    elif frame.msg_type == MSG_PROBE:
                        payload = pack_frame(frame.channel_id, MSG_ECHO, frame.seq, frame.sent_ns, frame.payload)
                        self._quic.send_stream_data(event.stream_id, payload, end_stream=False)
                        self.transmit()

        async def _bg_sender(self, stream_id: int, role: str) -> None:
            seq = 0
            interval = role_interval(role)
            channel_id = ROLE_CHANNELS[role]
            while True:
                payload = pack_frame(channel_id, MSG_BG, seq, now_ns(), role_payload(role, seq))
                self._quic.send_stream_data(stream_id, payload, end_stream=False)
                self.transmit()
                seq += 1
                await asyncio.sleep(interval)

    class MuxQuicClientProtocol(QuicConnectionProtocol):
        def __init__(self, router: EchoRouter, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.router = router
            self.buffers: dict[int, bytearray] = defaultdict(bytearray)

        def quic_event_received(self, event):
            if isinstance(event, StreamDataReceived):
                buf = self.buffers[event.stream_id]
                buf.extend(event.data)
                try:
                    frames = unpack_from_buffer(buf)
                except Exception:
                    return
                for frame in frames:
                    if frame.msg_type == MSG_ECHO:
                        asyncio.create_task(self.router.put(frame))

        def send_frame(self, stream_id: int, data: bytes) -> None:
            self._quic.send_stream_data(stream_id, data, end_stream=False)
            self.transmit()

    return connect, serve, QuicConfiguration, MuxQuicServerProtocol, MuxQuicClientProtocol


async def run_quic_server(args: argparse.Namespace) -> None:
    _, serve, QuicConfiguration, MuxQuicServerProtocol, _ = make_quic_protocols()
    configuration = QuicConfiguration(is_client=False, alpn_protocols=ALPN)
    configuration.load_cert_chain(args.cert, args.key)
    await serve(args.host, args.port, configuration=configuration, create_protocol=MuxQuicServerProtocol)
    print(f"[server/quic] listening on {args.host}:{args.port}", flush=True)
    await asyncio.Event().wait()


async def run_quic_client_profile(args: argparse.Namespace, profile: str, outputs: CsvOutputs) -> None:
    connect, _, QuicConfiguration, _, MuxQuicClientProtocol = make_quic_protocols()
    router = EchoRouter()
    configuration = QuicConfiguration(is_client=True, alpn_protocols=ALPN)
    configuration.verify_mode = ssl.CERT_NONE
    keylog_file = None
    if args.keylog:
        Path(args.keylog).parent.mkdir(parents=True, exist_ok=True)
        keylog_file = open(args.keylog, "a", encoding="utf-8")
        configuration.secrets_log_file = keylog_file
    try:
        print(f"[client/quic] connecting to {args.host}:{args.port}", flush=True)
        connect_cm = connect(
            args.host,
            args.port,
            configuration=configuration,
            create_protocol=lambda *a, **kw: MuxQuicClientProtocol(router, *a, **kw),
        )
        protocol = await asyncio.wait_for(connect_cm.__aenter__(), timeout=args.timeout)
        try:
            print(f"[client/quic] connected to {args.host}:{args.port}", flush=True)
            roles = ["interactive"] + profile_roles(profile)
            role_streams: dict[str, int] = {}
            for role in roles:
                stream_id = protocol._quic.get_next_available_stream_id(is_unidirectional=False)
                role_streams[role] = stream_id
                channel_id = ROLE_CHANNELS[role]
                protocol.send_frame(stream_id, pack_frame(channel_id, MSG_HELLO, 0, now_ns(), role.encode("ascii")))
                outputs.stream_map({
                    "ts": time.time(), "protocol": "quic", "profile": profile, "stream_role": role,
                    "app_channel_id": channel_id, "transport_stream_id": stream_id,
                    "connection_tag": "one_quic_connection", "note": "client-initiated bidirectional QUIC stream",
                })
            await asyncio.sleep(args.warmup_seconds)

            async def send(seq: int, sent_ns: int, probe: bytes) -> None:
                protocol.send_frame(
                    role_streams["interactive"],
                    pack_frame(ROLE_CHANNELS["interactive"], MSG_PROBE, seq, sent_ns, probe),
                )

            await measure_profile(
                args, outputs, "quic", profile, ROLE_CHANNELS["interactive"],
                str(role_streams["interactive"]), send, router,
            )
        finally:
            await connect_cm.__aexit__(None, None, None)
    finally:
        if keylog_file is not None:
            keylog_file.close()


async def measure_profile(args: argparse.Namespace, outputs: CsvOutputs, protocol: str, profile: str, channel_id: int, transport_stream_id: str, send_probe, router: EchoRouter) -> None:
    for i in range(args.runs):
        probe = PROBE_SEQUENCE[i % len(PROBE_SEQUENCE)].encode("ascii")
        sent_ns = now_ns()
        status = "success"
        latency_ms = ""
        note = ""
        try:
            await send_probe(i, sent_ns, probe)
            frame = await router.wait(i, timeout=args.timeout)
            latency_ms = f"{(now_ns() - frame.sent_ns) / 1_000_000.0:.3f}"
        except asyncio.TimeoutError:
            status = "timeout"
        except Exception as exc:
            status = "failure"
            note = repr(exc)
        outputs.sample({
            "ts": time.time(),
            "protocol": protocol,
            "profile": profile,
            "stream_role": "interactive",
            "app_channel_id": channel_id,
            "transport_stream_id": transport_stream_id,
            "sample_idx": i,
            "probe": probe.decode("ascii"),
            "latency_ms": latency_ms,
            "status": status,
            "note": note,
        })
        if args.live:
            shown = latency_ms or "-"
            print(f"[LIVE] {protocol:4s} {profile:12s} sample={i+1}/{args.runs} status={status:8s} latency_ms={shown}", flush=True)
        await asyncio.sleep(args.interval)


async def run_client(args: argparse.Namespace) -> None:
    outputs = CsvOutputs(Path(args.out_dir))
    protocols = parse_csv(args.protocols)
    profiles = parse_csv(args.profiles)
    for protocol in protocols:
        for profile in profiles:
            print(f"[RUN] protocol={protocol} profile={profile} runs={args.runs}", flush=True)
            if protocol == "tcp":
                await run_tcp_client_profile(args, profile, outputs)
            elif protocol == "udp":
                await run_udp_client_profile(args, profile, outputs)
            elif protocol == "quic":
                await run_quic_client_profile(args, profile, outputs)
            else:
                raise ValueError(f"unknown protocol: {protocol}")


async def run_server(args: argparse.Namespace) -> None:
    if args.protocol == "tcp":
        await run_tcp_server(args)
    elif args.protocol == "udp":
        await run_udp_server(args)
    elif args.protocol == "quic":
        await run_quic_server(args)
    else:
        raise ValueError(f"unknown protocol: {args.protocol}")


def parse_csv(value: str) -> list[str]:
    if value == "all":
        return list(PROFILES)
    return [part.strip() for part in value.replace(" ", ",").split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="One-connection multi-stream/channel benchmark for QUIC/TCP/UDP")
    sub = p.add_subparsers(dest="mode", required=True)

    sp = sub.add_parser("server")
    sp.add_argument("--protocol", choices=["quic", "tcp", "udp"], required=True)
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=44333)
    sp.add_argument("--cert", default="certs/mux_cert.pem")
    sp.add_argument("--key", default="certs/mux_key.pem")

    cp = sub.add_parser("client")
    cp.add_argument("--protocols", default="quic,tcp,udp")
    cp.add_argument("--host", required=True)
    cp.add_argument("--port", type=int, default=44333)
    cp.add_argument("--profiles", default="all")
    cp.add_argument("--runs", type=int, default=100)
    cp.add_argument("--interval", type=float, default=0.20)
    cp.add_argument("--timeout", type=float, default=2.0)
    cp.add_argument("--warmup-seconds", type=float, default=0.5)
    cp.add_argument("--out-dir", default="results_mux")
    cp.add_argument("--keylog", default="")
    cp.add_argument("--live", action="store_true")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.mode == "server":
            asyncio.run(run_server(args))
        else:
            asyncio.run(run_client(args))
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
