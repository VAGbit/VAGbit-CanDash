# VAGbit CAN Dashboard v0.4.3

- CSV recording: rows written in batches of up to 1,000, with one statistics-lock acquisition per batch rather than per frame.
- Fast live telemetry: lightweight `/api/live` endpoint sampled every 150 ms, independent from slower heavy CAN ID tables (2 s).
- Bounds: logging queue remains capped at 20,000 frames; full-queue drops are still counted and visible.
- ESP32 serial: preserves DTR/RTS disabled as tested on COM18.
- Replay and decoding are unchanged.

## Notes
- This reduces application overhead but cannot guarantee a machine freeze was caused by the application. Monitor Windows Event Viewer (System), storage health and USB drivers if it recurs.
- A CSV file may still grow without a size limit. Confirm free disk space before prolonged recording.
- Avoid stress-testing this revision with the vehicle moving until it has been validated in a safe stationary setup.
