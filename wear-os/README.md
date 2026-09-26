# zCloud Wear OS

Native Wear OS client for zCloud, optimized for the round Samsung Galaxy Watch 7 display.

## v0.1

- Home: overall project progress, active projects, CPU/RAM, warnings and project cards.
- Project detail: progress, current milestone, AI/activity age, progress sparkline, services, next step and recent activity.
- Manual refresh only; there is no background polling in v0.1.
- API calls use a compact read-only endpoint with a Bearer token.
- Existing zCloud desktop/mobile UI remains separate and unchanged.

## Architecture

`data/ZennayCloudRepository.kt` owns the HTTP/API boundary and model mapping.
`ui/ZennayWearApp.kt` owns watch navigation and Compose UI.
This separation leaves room for Tiles, complications, notifications, project actions and Raise AI integrations without coupling them to screen code.

## Build

The VPS build uses JDK 17, Android SDK 36 and Gradle 8.13.

```bash
export JAVA_HOME=/home/ubuntu/.local/jdk17
export ANDROID_HOME=/home/ubuntu/Android/Sdk
export ZENNAY_API_BASE=http://198.244.191.182:8765
export ZENNAY_WATCH_TOKEN="$(cat /home/ubuntu/zennay-cloud/.watch-token)"
cd /home/ubuntu/zennay-cloud/wear-os
/home/ubuntu/.local/gradle-8.13/bin/gradle --no-daemon :app:assembleDebug
```

APK: `app/build/outputs/apk/debug/app-debug.apk`

## Install with ADB

Copy the APK to a computer that can reach the watch over ADB, then:

```bash
adb devices
adb install -r app-debug.apk
```

The current backend uses the existing HTTP zCloud endpoint and limits cleartext access in the app to the VPS IP. Move the API behind HTTPS before adding write/actions endpoints.