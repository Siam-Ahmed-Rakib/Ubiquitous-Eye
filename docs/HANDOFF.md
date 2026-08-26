# Handoff — 2026-08-26, ~18:15 (+06)

Written mid-session on a Windows machine, to be resumed on an Ubuntu machine.
Everything below is verified unless marked otherwise.

## The one thing to do first

The user has a **presentation at 19:30 on 2026-08-26** and needs a working public
link. Azure is the goal; a tunnel is the fallback.

```bash
# On the Ubuntu machine, where `az account show` already works:
git clone https://github.com/Siam-Ahmed-Rakib/Ubiquitous-Eye.git
cd Ubiquitous-Eye
cat > .env <<'EOF'
SH_CLIENT_ID=<from the Windows box: E:\Ubiquitous-Eye\.env>
SH_CLIENT_SECRET=<same>
DATABASE_URL=<same>
EOF
bash deploy/azure-deploy.sh
```

That script needs **no Docker** — `az acr build` uploads only the build context
(~30 MB after `.dockerignore`) and builds the image inside Azure. It provisions a
resource group, an ACR (Basic), a Container Apps environment, and the app at
0.5 vCPU / 1 GiB, min-replicas 1, in **southeastasia**. It prints the live URL and
the exact `flutter build apk` command with that URL substituted.

Expect ~15-20 minutes, most of it the image build.

## Why Azure could not be done from Windows

`az login` fails with **`AADSTS530035`, `Device state: Unregistered`**, for
`2105158@ugrad.cse.buet.ac.bd` on tenant `10d93f4f-3089-4c95-8cea-c56f9dea2aa7`
("Default Directory"). That is a Conditional Access policy requiring a
registered/compliant device, and it blocks the **Azure Portal** too — so it is a
tenant policy, not a CLI or auth-method problem. Device-code flow is *also* blocked
by security defaults. Only a BUET tenant admin can change this. The user's Ubuntu
machine has a working Azure session, which is why the deploy moved there.

Do not spend time retrying `az login` on Windows.

## Fallback if Azure fails: temporary public link

Local stack is verified working. `cloudflared` is already downloaded to
`C:\Users\USER\cloudflared.exe` on the Windows box.

```
docker compose --project-directory . up -d          # if not already running
& "C:\Users\USER\cloudflared.exe" tunnel --url http://localhost:5001
```

It prints `https://<random>.trycloudflare.com`. Leave that terminal open.

Caveats the user has been told: the link dies with the laptop/terminal, the URL
changes on every restart, and all traffic crosses their home upload. Fine for a
live demo, **not** something to hand a supervisor for later use.

An earlier `ssh -R 80:localhost:5001 nokey@localhost.run` tunnel worked but
**collapsed under load** — a ~890 KB classify response took 65 s and then the
tunnel started returning 503. Do not use localhost.run for this payload size.

## What was completed today

**Rebuilt the image from HEAD.** It had been running 2026-07-29 code — `analyze_v4`,
1425 lines, no `_class_map_png` at all — because `docker compose build` had been
failing. Cause was pip dying mid-download after several large wheels (a resolver
giving out under load, not absent DNS); fixed with `--retries 10 --timeout 120`.

**Swapped the served UI from React to Flutter.** `server/Dockerfile` stage one is now
`ghcr.io/cirruslabs/flutter:stable` building `mobile/`. Verified serving through the
container: `/` (title "Ubiquitous Eye"), `main.dart.js` 2.87 MB, `canvaskit.wasm`
7.23 MB as `application/wasm`, `manifest.json`, SPA fallback, and the `/api/*` 404
guard all correct.

**Made the web client origin-relative.** `mobile/lib/config.dart` now resolves
`BACKEND_URL` → `Uri.base.origin` on web → localhost off web. One image works at any
URL with no rebuild and no CORS. `kBackendBaseUrl` changed `const` → `final`; both
call sites use it as a runtime expression so this is a drop-in.

**Fixed the class-map latency regression** (commit 2583694, "coloring map converted
but latency increased"). `_encode_png` used Pillow `optimize=True` everywhere. On
flat banded class-map content that is pathological. Measured at 1500x1500:

| setting | time | b64 size |
|---|---|---|
| `optimize=True` | 5.72 s | 5748 KB |
| default (level 6) | 0.89 s | 5847 KB |
| **`compress_level=3`** | **0.47 s** | **4956 KB** |

12x faster *and* smaller, byte-identical pixels. `_class_map_png` runs twice per
analyze → **~10.5 s off every change-detection request**. Photographic true-colour
scenes keep `optimize=True`, where level 3 would be 2x faster but 15% *larger*.

**Renamed the project** terrascope → Ubiquitous Eye across 15 files, regenerated
`android/` as `com.ubiquitouseye.ubiquitous_eye`. `flutter analyze` clean.

**Fixed a release-APK blocker.** Flutter's template grants `INTERNET` only in
`debug/` and `profile/` manifests; release builds use `main/`. Without the fix the
APK installs, launches, and then fails every backend call and every map tile.

**Cleared a false alarm.** sklearn warns the pickles were written by 1.6.1 while the
image ships 1.9.0. Ran both versions against the same models: `scaler.mean_`,
`scaler.scale_`, the full scaled matrix, the decision-tree predictions and the label
encoder classes all hash **identically**. The warning is benign; do not change the pin.

**Database.** Supabase had paused; the user restored it and rotated the password.
Verified live on the new credentials: 47 rows in `analysis_cache`, 44 in
`classification_result`, **168,777** in `classification_point`, 4 in
`land_cover_class`. 133 MB of a 500 MB free tier.

## Not finished

**APK build.** Gradle died with `Gradle build daemon disappeared unexpectedly` and
JVM crash dumps, almost certainly memory contention — Docker Desktop, a Flutter web
build and a Gradle daemon at once. Not yet retried in isolation. The Android
toolchain itself is fine: Flutter 3.35.2, Android SDK 36.1.0-rc1, Android Studio
2025.1.3 all report `[√]`. Once a URL exists:

```sh
cd mobile
flutter build apk --release --dart-define=BACKEND_URL=https://<live-url>
# -> build/app/outputs/flutter-apk/app-release.apk
```

The `--dart-define` is **mandatory** for the APK. `config.dart`'s origin fallback is
web-only; a phone app has no serving origin and falls back to `http://localhost:5000`,
which on a phone means the phone itself.

**Registry choice.** `deploy/azure-deploy.sh` uses ACR Basic (~$5.07/mo). The user
leaned toward ghcr.io + GitHub Actions (free) but that needs a workflow file only
they can push. On a $100 student grant this is roughly the difference between 6 and
8.5 months of runway. Worth revisiting after the deadline.

**Sizing rationale, so it is not re-litigated.** Measured, not guessed: a cold
classify is 98% serial Sentinel Hub round-trips; the 4-model ensemble over 31,659
cells takes **0.30 s**; container CPU peaked at 51% (never saturated one core) and
RAM at 266 MB. Core count buys nothing — nothing in the pipeline is parallel. The
user has explicitly declined parallelising the fetch loops ("that fetching wait is
acceptable"). Do not re-propose it unprompted.

## Repository state

Last commit `c8fa60d`. **The user pushes, never Claude.** If `git log
origin/master..master` is non-empty, ask them to push rather than doing it.
