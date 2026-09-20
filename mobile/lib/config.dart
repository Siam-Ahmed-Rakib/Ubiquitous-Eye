import 'package:flutter/foundation.dart' show kIsWeb;

/// Base URL of the Flask backend (`server/api_server.py`), which exposes
/// `POST /api/sentinel/analyze` and `POST /api/sentinel/classify`.
///
/// Resolution order:
///  1. `--dart-define=BACKEND_URL=...` if one was supplied at build time.
///  2. On **web**, the origin the app was served from. The deployed image bakes
///     the web build into the backend's own static root, so the API is a
///     same-origin `/api/...` away. Deriving it at runtime rather than baking a
///     hostname means one image works on localhost, on the Azure URL, and on
///     any custom domain later, with no rebuild -- and there is no cross-origin
///     request to configure.
///  3. Off web there is no serving origin to inherit, so fall back to the local
///     dev backend.
///
/// Override at build/run time without editing code:
/// ```sh
/// flutter run -d chrome  --dart-define=BACKEND_URL=http://localhost:5001
/// flutter run -d android --dart-define=BACKEND_URL=http://10.0.2.2:5000
/// ```
/// Notes:
/// * **Android emulator** can't reach the host via `localhost` -- use
///   `http://10.0.2.2:5000`.
/// * **Physical device** -- use your machine's LAN IP, e.g.
///   `http://192.168.0.10:5000` (device and PC on the same network).
const String _configuredBackendUrl = String.fromEnvironment('BACKEND_URL');

/// Not `const`: case 2 reads `Uri.base`, which is only known once running.
/// Both call sites use it as a plain runtime expression
/// (`baseUrl ?? kBackendBaseUrl`), so `final` is a drop-in.
final String kBackendBaseUrl = _configuredBackendUrl.isNotEmpty
    ? _configuredBackendUrl
    : (kIsWeb ? Uri.base.origin : 'http://localhost:5000');

/// How long the client waits for a classification or change-detection request.
///
/// These requests are genuinely slow: the backend searches the Sentinel Hub
/// catalogue, downloads every clear acquisition in the window, builds a temporal
/// median composite and then classifies every cell. A month over a small area is
/// a couple of minutes; a large area, or one that makes the adaptive compositor
/// widen its window looking for clear looks, is much longer.
///
/// **Keep this at or below gunicorn's `--timeout` in `server/Dockerfile`.** If
/// the client waits longer than the server is willing to work, gunicorn kills
/// the worker and the browser sits there until its own timer runs out, reporting
/// a connection problem for what was really a server-side abort.
const Duration kBackendRequestTimeout = Duration(minutes: 30);
