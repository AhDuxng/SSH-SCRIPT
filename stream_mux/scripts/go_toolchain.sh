#!/usr/bin/env bash
# Chọn toolchain Go dùng để build SSH3.
#
# Vấn đề thực tế: PATH của phiên SSH không tương tác thường bắt được bản Go
# cũ ở /usr/bin trong khi bản mới nằm ở /usr/local/go/bin. Chọn theo phiên
# bản cao nhất chứ không theo đường dẫn, để không vô tình hạ cấp trên máy đã
# cài ngược lại.

STREAM_MUX_GO_MIN_MINOR="${STREAM_MUX_GO_MIN_MINOR:-21}"

# In ra số minor của một binary go, rỗng nếu không đọc được.
stream_mux_go_minor() {
    local binary="$1" raw
    [[ -x "$binary" ]] || return 1
    raw="$("$binary" env GOVERSION 2>/dev/null)" || return 1
    [[ -n "$raw" ]] || raw="$("$binary" version 2>/dev/null | awk '{print $3}')"
    printf '%s' "$raw" | sed -n 's/^go1\.\([0-9]*\).*/\1/p'
}

# Đặt PATH trỏ tới bản Go mới nhất >= yêu cầu; thoát 2 nếu không có bản nào.
stream_mux_require_go() {
    local candidates=() best="" best_minor=-1 binary minor found=""
    candidates+=("$(command -v go 2>/dev/null || true)")
    candidates+=(/usr/local/go/bin/go "$HOME/go/bin/go")
    while IFS= read -r binary; do
        candidates+=("$binary")
    done < <(ls -d /usr/lib/go-1.*/bin/go 2>/dev/null || true)

    for binary in "${candidates[@]}"; do
        [[ -n "$binary" && -x "$binary" ]] || continue
        minor="$(stream_mux_go_minor "$binary" || true)"
        [[ -n "$minor" ]] || continue
        found+="  $binary -> go1.$minor"$'\n'
        if (( minor > best_minor )); then
            best_minor="$minor"
            best="$binary"
        fi
    done

    if [[ -z "$best" ]]; then
        echo "Không tìm thấy Go. Cần >= 1.${STREAM_MUX_GO_MIN_MINOR}." >&2
        return 2
    fi
    if (( best_minor < STREAM_MUX_GO_MIN_MINOR )); then
        echo "Go quá cũ: bản mới nhất tìm được là go1.${best_minor}," \
             "cần >= 1.${STREAM_MUX_GO_MIN_MINOR}." >&2
        printf 'Đã tìm thấy:\n%s' "$found" >&2
        echo "Cài Go mới rồi thử lại, hoặc đặt PATH tới bản đủ mới." >&2
        return 2
    fi

    PATH="$(dirname "$best"):$PATH"
    export PATH
    hash -r 2>/dev/null || true
    echo "Dùng $best (go1.${best_minor})"
}
