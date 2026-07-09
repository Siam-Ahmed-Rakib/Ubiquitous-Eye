/// Base URL of the Flask backend (`server/api_server.py`), which exposes
/// `POST /api/sentinel/analyze` for the change-detection pipeline.
///
/// Override at build/run time without editing code:
/// ```sh
/// flutter run -d chrome  --dart-define=BACKEND_URL=http://localhost:5000
/// flutter run -d android --dart-define=BACKEND_URL=http://10.0.2.2:5000
/// ```
///
/// Defaults to `http://localhost:5000`, which is correct for the web build
/// (`flutter run -d chrome`) and desktop. Notes:
/// * **Android emulator** can't reach the host via `localhost` — use
///   `http://10.0.2.2:5000`.
/// * **Physical device** — use your machine's LAN IP, e.g.
///   `http://192.168.0.10:5000` (device and PC on the same network).
const String kBackendBaseUrl = String.fromEnvironment(
  'BACKEND_URL',
  defaultValue: 'http://localhost:5000',
);
