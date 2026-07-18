# Headless deploy (Raspberry Pi / always-on host)

## Why routines missed this morning

Sunrise/sunset fire only while a process is running the schedule loop:

- **Dashboard** (`iot/server.py`) — includes the loop  
- **Worker** (`iot/worker.py`) — headless loop only  

If the Mac is **asleep**, neither runs → no sunrise off / sunset on.

## Options

| Option | Pros | Cons |
|--------|------|------|
| **A. Headless worker on Pi** | Light, always-on, CLI/SSH | No UI on Pi unless you also run dashboard |
| **B. Full dashboard on Pi** | UI at `http://pi:8780` + routines | Slightly heavier |
| **C. Cron `--once` every minute** | Simple | No continuous logs; still need always-on host |

Recommended: **A or B on a Pi that stays powered**.

## Quick headless (already supported)

On any always-on Linux host with this tree and `pywizlight`:

```bash
cd /path/to/iot-workspace   # parent of iot/
export PYTHONPATH=.
python3 iot/worker.py --interval 30
# or single evaluation (cron):
python3 iot/worker.py --once
```

Dashboard without browser (still serves HTTP if you want remote UI):

```bash
python3 iot/server.py --host 0.0.0.0 --port 8780 --no-browser
```

## Deploy over SSH from your Mac

```bash
# From monorepo root — SSH must work first:
ssh pi@YOUR_PI_IP

# Deploy worker (systemd, auto-start on boot):
bash iot/deploy/install_remote.sh pi@YOUR_PI_IP

# Or full dashboard on the LAN:
bash iot/deploy/install_remote.sh pi@YOUR_PI_IP --dashboard
```

The script:

1. `rsync`s `iot/` to `~/iot-workspace/iot/`  
2. `pip install --user pywizlight`  
3. Installs `iot-worker.service` or `iot-dashboard.service`  
4. `systemctl enable --now`  

### After deploy

```bash
ssh pi@YOUR_PI_IP 'journalctl -u iot-worker -f'
ssh pi@YOUR_PI_IP 'systemctl status iot-worker'
```

Confirm `schedule.json` on the Pi has **latitude/longitude** (already in the repo copy). Same LAN as Wiz bulbs is required (UDP).

### Firewall

- Worker: outbound UDP to bulbs only  
- Dashboard mode: allow **TCP 8780** inbound if you want phone/Mac UI  

## macOS note

Keeping the Mac awake (`caffeinate`) is a temporary workaround, not a reliable schedule host.
