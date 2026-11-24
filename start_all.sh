#!/bin/sh
# start_all.sh — chạy cả flow2api (host-run) + Chrome farm harvester dưới nền,
# terminal không bị giữ. Chạy lại nhiều lần không sinh trùng instance.
#   ./start_all.sh        -> khởi động những gì chưa chạy
#   ./start_all.sh stop   -> dừng cả hai
#
# Sau reboot máy chỉ cần chạy lại file này.

cd "$(dirname "$0")" || exit 1
mkdir -p tmp

if [ "$1" = "stop" ]; then
  pkill -f "venvs/flow2api/bin/python flow2api.py" 2>/dev/null
  if [ -f tmp/farm_run.pid ]; then kill "$(cat tmp/farm_run.pid)" 2>/dev/null; rm -f tmp/farm_run.pid; fi
  echo "Đã dừng flow2api + farm harvester (farm Chrome vẫn mở, đóng tay bằng `browser_farm.py kill`)."
  exit 0
fi

# 1) flow2api — check theo port thay vì tên process, nên đổi tên file cũng không sao
if curl -s -m 3 http://127.0.0.1:38000/health >/dev/null 2>&1; then
  echo "flow2api: đang chạy sẵn (port 38000) — bỏ qua"
else
  DISPLAY="${DISPLAY:-:0}" setsid nohup "$HOME/.venvs/flow2api/bin/python" flow2api.py \
    >> tmp/host_run.log 2>&1 &
  echo "flow2api: đã khởi động nền (log tmp/host_run.log) — app cần ~30-40s mới sẵn sàng"
fi

# 2) farm harvester — tự mở Chrome cho token ENABLE, đẩy cookie mỗi 15 phút.
#    Guard bằng pidfile (không dùng pgrep pattern — tránh tự khớp cmdline).
FARM_PID_FILE=tmp/farm_run.pid
if [ -f "$FARM_PID_FILE" ] && kill -0 "$(cat "$FARM_PID_FILE")" 2>/dev/null; then
  echo "farm harvester: đang chạy sẵn (pid $(cat "$FARM_PID_FILE")) — bỏ qua"
else
  nohup "$HOME/.venvs/flow2api/bin/python" -u browser_farm.py run \
    >> tmp/farm_run.log 2>&1 &
  echo $! > "$FARM_PID_FILE"
  echo "farm harvester: đã khởi động nền (pid $(cat "$FARM_PID_FILE"), log tmp/farm_run.log)"
  echo "  - vòng đầu sẽ tự mở Chrome cho các token đang ENABLE"
  echo "  - lần đầu mỗi acc cần đăng nhập Google trong cửa sổ farm của nó"
fi

echo "Xem log: tail -f tmp/host_run.log tmp/farm_run.log"
