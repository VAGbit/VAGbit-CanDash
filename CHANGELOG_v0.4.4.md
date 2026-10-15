# v0.4.4 - Connection diagnostics

- Keep DTR and RTS disabled before serial open.
- Display serial worker exceptions immediately in the fast update.
- Show connection attempt status and poll for the actual worker result.
- Keep batch CSV writer and 150 ms gauge updates from v0.4.3.
- If a port changes, select the new COM port and ensure Arduino Serial Monitor is closed.
