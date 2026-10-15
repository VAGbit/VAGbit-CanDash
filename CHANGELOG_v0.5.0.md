# VAGbit CAN Dashboard v0.5.0 — Telemetry Health

- Adds a professional Telemetry Health diagnostics section.
- Reduces live polling interval to 90 ms; draw gauges only when values or dimensions change.
- Adds a lightweight /api/diagnostics endpoint and health statistics to /api/state.
- Shows CAN receive rate (two-second rolling window), RPM sample age, driver serial bytes awaiting read, malformed timestamps, out-of-order timestamps, recorder queue depth and dropped frames.
- Rejects implausible timestamps (>999,999,999 ms) to protect calculations from merged serial lines.
- Limits serial line reads to 160 bytes and drains oversize lines without unbounded allocation.
- Keeps DTR/RTS disabled for USB COM-port reliability and keeps CSV recording separate.

Note: These changes can reduce rendering overhead, but do not guarantee elimination of hardware, driver, firmware, or serial buffering latency. Recordings and CAN IDs may contain corrupt lines; bad frames are counted and excluded.
