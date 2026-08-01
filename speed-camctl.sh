#!/bin/bash
# speed-camctl.sh - Linux control script for speed-cam.py
# Works from any directory (auto-detects script location)

DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PROGNAME="speed-cam.py"
LOGFILE="$DIR/speed-cam.log"

cd "$DIR" || exit 1

check_running() {
    PID=$(pgrep -f "$PROGNAME" | head -n1)
    if [ -n "$PID" ]; then
        echo "running|$PID"
        return 0
    else
        echo "stopped|0"
        return 1
    fi
}

case "$1" in
    start)
        check_running >/dev/null
        if [ $? -eq 0 ]; then
            echo "INFO: $PROGNAME is already running (PID: $PID)"
            exit 0
        fi
        echo "INFO: Starting $PROGNAME ..."
        nohup python3 "$DIR/$PROGNAME" > "$LOGFILE" 2>&1 &
        sleep 2
        check_running
        ;;
    stop)
        check_running >/dev/null
        if [ $? -ne 0 ]; then
            echo "INFO: $PROGNAME is not running."
            exit 0
        fi
        echo "INFO: Stopping $PROGNAME (PID: $PID) ..."
        kill "$PID" 2>/dev/null
        sleep 2
        # Force kill if still running
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null
        fi
        check_running
        ;;
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;
    status)
        check_running
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        echo ""
        echo "  start   - Start speed-cam.py in background"
        echo "  stop    - Stop speed-cam.py"
        echo "  restart - Restart speed-cam.py"
        echo "  status  - Check if speed-cam.py is running"
        exit 1
        ;;
esac
