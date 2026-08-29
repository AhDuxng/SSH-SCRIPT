# Environment: Pi client to PC Linux server

- Client/driver: `trungnt@100.72.249.11`
- Server/target: `trungnt@192.168.2.101`
- SSH3 endpoint: UDP `443`, path `/ssh3-term`

Each `*.env` file is copied to the matching workload directory on the Pi as
`config.env`.

Kiểm tra trước khi đo:

```bash
cd ~/SSH-SCRIPT
for w in w1 w2 w3 w4; do
  python3 stream_mux/scripts/preflight.py $w-mux-tt/config.env || break
done
```
