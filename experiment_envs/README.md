# Cấu hình theo môi trường đo

Mỗi thư mục con là một cặp client/server đã dùng để chạy thí nghiệm. Các tệp
`*.env` được chép sang máy client thành `config.env` của workload tương ứng:

```bash
# [client]
cd ~/SSH-SCRIPT
ENV=experiment_envs/pi-100.76.167.75_to_pi-192.168.1.202
for w in w1 w2 w3 w4; do cp "$ENV/$w-mux-tt.env" "$w-mux-tt/config.env"; done
```

`config.env` nằm trong `.gitignore`, nên các tệp ở đây là bản duy nhất được
theo dõi. Khi đổi tham số đo, sửa tệp trong thư mục môi trường rồi chép lại.

## Các môi trường

| Thư mục | Client | Server |
|---|---|---|
| `pi-100.76.167.75_to_pi-192.168.1.202` | Pi `100.76.167.75` | Pi `192.168.1.202` (LAN) |
| `pi-100.72.249.11_to_pc-192.168.2.101` | Pi `100.72.249.11` | PC Linux `192.168.2.101` |

## Kiểm tra trước khi đo

`stream_mux/scripts/preflight.py` kiểm tra cả hai đầu trong một lượt: công cụ
trên client và server, thuật toán congestion control đã nướng vào binary SSH3
của **cả client lẫn server**, rồi mở thật một connection cho từng giao thức.

```bash
# [client]
cd ~/SSH-SCRIPT
for w in w1 w2 w3 w4; do
  python3 stream_mux/scripts/preflight.py $w-mux-tt/config.env || break
done
```

Mã thoát khác 0 nghĩa là còn mục chưa đạt; mỗi mục hỏng đều in kèm lệnh sửa.
