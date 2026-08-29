# Environment: Pi client to Pi server

- Client/driver: `trungnt@100.76.167.75`
- Server/target: `trungnt@192.168.1.202` (LAN)
- SSH3 endpoint: UDP `443`, path `/ssh3-term`

Each `*.env` file is copied to the matching workload directory on the
client as `config.env`.

Kiểm tra trước khi đo:

```bash
cd ~/SSH-SCRIPT
for w in w1 w2 w3 w4; do
  python3 stream_mux/scripts/preflight.py $w-mux-tt/config.env || break
done
```
