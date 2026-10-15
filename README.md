# VAGbit CAN Dashboard v0.1

Windows demo dashboard for the existing ESP32 TWAI listen-only serial logger.

## Start

1. Install Python 3.10+ on Windows.
2. Run `install_dependencies.bat` once (`pyserial`).
3. Run `start_dashboard.bat`. A browser opens at `http://127.0.0.1:8765`.
4. Select **Live ESP32 / Serial**, choose the COM port, and select `921600` baud.
5. Press CONNECT. The ESP32 must already be running the logger: this app sends **no commands** to it.
6. Alternatively select **Replay CAN log**, enter `sample_log.txt`, press START REPLAY.

## Command line

- `py -3 app.py --port COM16 --baud 921600`
- `py -3 app.py --replay sample_log.txt`
- `py -3 app.py --http-port 8766`

## Input format

```text
13806,280,0,8,01 12 FB 0D 12 00 18 12
13806,588,0,8,EB BF 80 02 32 01 04 64
# STATS uptime=14 received=5044 serial_dropped=0 can_missed=0 can_overrun=0 queue=0 heap=314404
```

Also supports `CAN,timestamp_ms,can_id,dlc,data` from the diagnostic logger. Strict frame parsing skips incomplete/corrupted lines. The supplied sample is the original user log; it contains corrupt serial text which is intentionally discarded. RPM timestamps are taken from the ESP32 on reception, not hardware timestamped at SOF.

## Validation and safety

- Most signal mapping comes from a **PQ46** list or older VW/T4 CAN references; the mappings have **not been verified** for this specific PQ25/MED9.1 vehicle.
- Data labeled **SRC** = source-described, vehicle-unverified. **TEST** = experimental scaling / cross-platform inference.
- The `0x588 b4 × 0.02` bar boost estimate is a working hypothesis. It is *absolute* pressure; gauge boost requires measuring atmospheric pressure, not just subtracting a fixed value unconditionally.
- `0x588 b7 - 60` oil temperature has independent corroboration. The dashboard does not hide values below 50°C although some clusters do.
- Tank fuel level (`0x320 b2`) is displayed as RAW, not a percentage; `0x520` odometer is RAW, not kilometers.
- Steering, brake pressure and wheel speeds are RAW. Avoid interpreting their displayed values as physical units.
- A very busy bus or USB connection can lose frames; watch CAN missed, overrun, and serial dropped counters. The Windows serial reader can also lose lines if overwhelmed.
- The tool has no CAN transmit capability. Your physical CAN interface still must be in listen-only mode and correctly connected with appropriate transceiver hardware.

## Sources

- https://github.com/v-ivanyshyn/parse_can_logs/blob/master/VW%20CAN%20IDs%20Summary.md
- https://www.vwbuswiki.de/index.php?title=CAN_IDs
- https://github.com/an-ven/VW-Instrument-Cluster-Controller
- https://hncreations.blog/hacking-vw-gauges

## Known limitations

- Signal freshness is not yet visibly dimmed for stale values.
- Switching a source while the previous source is still running can briefly overlap readers. Close/restart the dashboard when changing source if needed.
- No offline storage of live CAN frames in this version.


## V0.3 VAG-inspired instruments

- Redesigned four primary gauges as live-drawn circular cockpit instruments with white/lavender tick marks, physical needles, red warning ranges and VAGbit orange accents in the surrounding interface.
- Min/Max/Peak refer to values observed by this dashboard process and reset on reload; not historical ECU values.
- Existing serial COM mode and offline log replay remain handled by the original backend.
- Boost, torque and other unverified PQ25 CAN mappings are experimental; never use this demonstration for vehicle control.

## v0.4 - CSV recording

Choose **Live ESP32 / Serial** and connect the device, then select **START LOGGING**. The backend writes all valid received CAN frames into `logs/VAGbit_YYYYMMDD_HHMMSS_microseconds.csv`. Select **STOP & SAVE** to flush the recording and **DOWNLOAD CSV** to save a copy through the browser. The five most recent sessions are linked below the recorder controls.

Recording also works with **Replay CAN log**, which is useful for testing, but creates a new copy of replayed data. The output uses `timestamp_ms,can_id,extended,dlc,data` and can be replayed with the dashboard. The CSV preserves received raw frames rather than current interpretations of the data. The first header row is ignored as an invalid non-CAN row in the current replay implementation.

The dashboard uses an asynchronous 20,000-frame disk-write queue. Watch `dropped`: a value above zero indicates lost frames between the parser and CSV writer. The ESP32's own `serial_dropped`, `can_missed`, and `can_overrun` counters must also be monitored; this application cannot recover frames that never reached it.

When switching sources, stop and save the current recording first to keep each file associated with one capture session. For reliable recordings, do not close the backend window before pressing **STOP & SAVE**.


## v0.4.1 connection fix

Cancelled browser requests (Windows WinError 10053, 10054, broken pipe) are silently ignored by the local HTTP server. CAN logging and replay logic remain unchanged.

## v0.4.2 large-log stability

- Replay processes CSV line by line, not as a complete in-memory data set.
- Replay caps processing at 350 CAN frames/second to protect the computer on multi-million-line files. Large files consequently replay more slowly. Data is not skipped by the limiter.
- Browser polls once per second and never overlaps two status requests; background tabs suspend polling.
- Serial port open explicitly starts with DTR and RTS disabled (some ESP32 USB setups need this).
- Existing live recording remains a streamed CSV writer. Do not open a multi-GB CSV in Excel/Notepad while recording or playing back; use replay directly.

The dashboard remains local-only and the ESP32/CAN interface is listen-only.


## v0.5.0 Telemetry Health

The new Telemetry Health panel displays live receive rate, RPM age, USB read backlog, timestamp problems, recording queue depth, and dropped recording frames. Start with live data without recording, then test a short 30-second recording. Do not operate the dashboard while driving. If Windows previously froze during recording, monitor CPU, memory, USB driver and disk load and avoid long unattended sessions until the cause has been established.
