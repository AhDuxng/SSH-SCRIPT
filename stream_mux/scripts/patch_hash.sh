#!/usr/bin/env bash
# Công thức băm dùng chung cho build và run. Băm phải phủ mọi thứ ảnh hưởng
# tới binary — kể cả thuật toán chống tắc nghẽn — nếu không việc đổi
# SSH3_CC sẽ không kích hoạt build lại và bộ số đo sẽ sai giao thức.

# Thuật toán chống tắc nghẽn của quic-go: reno (mặc định) hoặc cubic.
stream_mux_ssh3_cc() {
    local value="${SSH3_CC:-reno}"
    value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
    case "$value" in
        reno|cubic) printf '%s\n' "$value" ;;
        *) echo "SSH3_CC phải là reno hoặc cubic, nhận được: $value" >&2; return 2 ;;
    esac
}

# In ra băm của toàn bộ patch + script chuẩn bị + lựa chọn thuật toán.
stream_mux_patch_hash() {
    local shared_dir="$1" cc
    cc="$(stream_mux_ssh3_cc)" || return 2
    {
        shasum -a 256 \
            "$shared_dir/patches/ssh3_mux_stdio.patch" \
            "$shared_dir/patches/ssh3_jwt_clock_skew.patch" \
            "$shared_dir/patches/ssh3_qlog.patch" \
            "$shared_dir/patches/quic_go_cubic.patch" \
            "$shared_dir/scripts/prepare_quic_cc.sh"
        printf 'cc_algorithm=%s\n' "$cc"
    } | shasum -a 256 | awk '{print $1}'
}
