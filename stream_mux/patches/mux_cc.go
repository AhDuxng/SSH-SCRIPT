package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/quic-go/quic-go/logging"
)

// muxCongestionLogger ghi các callback recovery của quic-go thành JSONL.
type muxCongestionLogger struct {
	mu             sync.Mutex
	file           *os.File
	encoder        *json.Encoder
	interval       time.Duration
	lastMetricTime time.Time
	lastMetric     map[string]any
	closed         bool
}

// newMuxCongestionTracer bật tracer khi driver truyền SSH3_MUX_CC_LOG.
func newMuxCongestionTracer() func(context.Context, logging.Perspective, quic.ConnectionID) *logging.ConnectionTracer {
	path := os.Getenv("SSH3_MUX_CC_LOG")
	if path == "" {
		return nil
	}
	return newCongestionTracerFactory(
		func(_ quic.ConnectionID) string { return path }, "client",
	)
}

// newServerCongestionTracer tạo một log riêng cho mỗi QUIC connection phía gửi.
func newServerCongestionTracer() func(context.Context, logging.Perspective, quic.ConnectionID) *logging.ConnectionTracer {
	directory := os.Getenv("SSH3_SERVER_CC_DIR")
	if directory == "" {
		return nil
	}
	return newCongestionTracerFactory(func(connectionID quic.ConnectionID) string {
		name := fmt.Sprintf(
			"%d-%s.ssh3_server_quic.jsonl",
			time.Now().UnixNano(), connectionID.String(),
		)
		return filepath.Join(directory, name)
	}, "server")
}

// newCongestionTracerFactory mở JSONL và gắn callback recovery cho một đầu QUIC.
func newCongestionTracerFactory(
	pathFor func(quic.ConnectionID) string, endpoint string,
) func(context.Context, logging.Perspective, quic.ConnectionID) *logging.ConnectionTracer {
	interval := 100 * time.Millisecond
	if raw := os.Getenv("SSH3_MUX_CC_INTERVAL_MS"); raw != "" {
		if value, err := strconv.ParseFloat(raw, 64); err == nil && value >= 20 {
			interval = time.Duration(value * float64(time.Millisecond))
		}
	}
	return func(_ context.Context, perspective logging.Perspective, connectionID quic.ConnectionID) *logging.ConnectionTracer {
		path := pathFor(connectionID)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			fmt.Fprintf(os.Stderr, "SSH3 congestion log mkdir failed: %v\n", err)
			return nil
		}
		file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
		if err != nil {
			fmt.Fprintf(os.Stderr, "SSH3 congestion log open failed: %v\n", err)
			return nil
		}
		logger := &muxCongestionLogger{
			file: file, encoder: json.NewEncoder(file), interval: interval,
		}
		logger.write(map[string]any{
			"event": "collector_start", "time_ns": time.Now().UnixNano(),
			"transport": "quic", "source": "quic_go_connection_tracer",
			"endpoint": endpoint,
			// quic-go 1083d1fb8f98 khởi tạo NewCubicSender với use Reno=true.
			"cc_algorithm":  "reno",
			"perspective":   int(perspective),
			"connection_id": connectionID.String(),
			"interval_ms":   float64(interval) / float64(time.Millisecond),
		})
		return logger.tracer()
	}
}

// write ghi một event khi logger còn mở.
func (l *muxCongestionLogger) write(event map[string]any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.writeLocked(event)
}

// writeLocked ghi event khi caller đang giữ mutex.
func (l *muxCongestionLogger) writeLocked(event map[string]any) {
	if !l.closed {
		_ = l.encoder.Encode(event)
	}
}

// congestionStateName đổi mã trạng thái congestion thành tên dễ đọc.
func congestionStateName(state logging.CongestionState) string {
	switch state {
	case logging.CongestionStateSlowStart:
		return "slow_start"
	case logging.CongestionStateCongestionAvoidance:
		return "congestion_avoidance"
	case logging.CongestionStateRecovery:
		return "recovery"
	case logging.CongestionStateApplicationLimited:
		return "application_limited"
	default:
		return fmt.Sprintf("unknown_%d", state)
	}
}

// packetLossReasonName đổi lý do mất gói thành tên dễ đọc.
func packetLossReasonName(reason logging.PacketLossReason) string {
	switch reason {
	case logging.PacketLossReorderingThreshold:
		return "reordering_threshold"
	case logging.PacketLossTimeThreshold:
		return "time_threshold"
	default:
		return fmt.Sprintf("unknown_%d", reason)
	}
}

// tracer cung cấp các callback RTT, cwnd, loss, recovery và PTO.
func (l *muxCongestionLogger) tracer() *logging.ConnectionTracer {
	return &logging.ConnectionTracer{
		StartedConnection: func(local, remote net.Addr, src, dst logging.ConnectionID) {
			l.write(map[string]any{
				"event": "connection_started", "time_ns": time.Now().UnixNano(),
				"local": local.String(), "remote": remote.String(),
				"source_connection_id":      src.String(),
				"destination_connection_id": dst.String(),
			})
		},
		UpdatedMetrics: func(rtt *logging.RTTStats, cwnd, inFlight logging.ByteCount, packets int) {
			now := time.Now()
			metric := map[string]any{
				"event": "metrics", "time_ns": now.UnixNano(),
				"latest_rtt_us":   rtt.LatestRTT().Microseconds(),
				"smoothed_rtt_us": rtt.SmoothedRTT().Microseconds(),
				"min_rtt_us":      rtt.MinRTT().Microseconds(),
				"rtt_variance_us": rtt.MeanDeviation().Microseconds(),
				"cwnd_bytes":      int64(cwnd), "bytes_in_flight": int64(inFlight),
				"packets_in_flight": packets,
			}
			l.mu.Lock()
			l.lastMetric = metric
			if l.lastMetricTime.IsZero() || now.Sub(l.lastMetricTime) >= l.interval {
				l.writeLocked(metric)
				l.lastMetricTime = now
			}
			l.mu.Unlock()
		},
		LostPacket: func(level logging.EncryptionLevel, number logging.PacketNumber, reason logging.PacketLossReason) {
			l.write(map[string]any{
				"event": "packet_lost", "time_ns": time.Now().UnixNano(),
				"encryption_level": int(level), "packet_number": int64(number),
				"reason": packetLossReasonName(reason),
			})
		},
		UpdatedCongestionState: func(state logging.CongestionState) {
			l.write(map[string]any{
				"event": "congestion_state", "time_ns": time.Now().UnixNano(),
				"state": congestionStateName(state),
			})
		},
		UpdatedPTOCount: func(value uint32) {
			l.write(map[string]any{
				"event": "pto_count", "time_ns": time.Now().UnixNano(),
				"value": value,
			})
		},
		ClosedConnection: func(err error) {
			message := ""
			if err != nil {
				message = err.Error()
			}
			l.write(map[string]any{
				"event": "connection_closed", "time_ns": time.Now().UnixNano(),
				"error": message,
			})
		},
		Close: func() {
			l.mu.Lock()
			defer l.mu.Unlock()
			if l.closed {
				return
			}
			if l.lastMetric != nil {
				finalMetric := make(map[string]any, len(l.lastMetric))
				for key, value := range l.lastMetric {
					finalMetric[key] = value
				}
				finalMetric["event"] = "metrics_final"
				finalMetric["time_ns"] = time.Now().UnixNano()
				l.writeLocked(finalMetric)
			}
			l.writeLocked(map[string]any{
				"event": "collector_stop", "time_ns": time.Now().UnixNano(),
			})
			l.closed = true
			_ = l.file.Close()
		},
	}
}
