// Build images for the Android, Flutter and gst-stream-wall pipelines, pinned by digest so that a rebuilt or
// re-pulled tag cannot change CI without a commit here. Move pipelines to a rebuilt image by changing it here.

def android() {
    // android-build-env:jdk25-20261029 (Temurin 25.0.5 default, 21.0.13); Dockerfile in docker/android-build-env
    return 'harbor.maksonlee.com/library/android-build-env@sha256:b88ae3849ada50e9943fb7cab8b1073063e1be23189c132395f9a853bb6154d7'
}

def flutter() {
    // flutter-android-build-env:jdk25-20261029 (android-build-env + Dart 3.13.7, FVM 4.1.0); Dockerfile in docker/flutter-android-build-env
    return 'harbor.maksonlee.com/library/flutter-android-build-env@sha256:82ad7cf9da263d1761c22c19822f317cdd3a17a8ffd8e81e22584eae85107c20'
}

def gstStreamWall() {
    // gst-stream-wall-android-build-env:1.98.0-2, built 2026-08-30; Dockerfile in docker/gst-stream-wall-android-build-env
    return 'harbor.maksonlee.com/library/gst-stream-wall-android-build-env@sha256:11e2a4f7a262afd5a28c2280b7b0114db541d24e7eff498dee79e4a55a10dad1'
}

def python() {
    // python:3.13-bookworm as last pulled by the Flutter job (image created 2026-11-05); runs repository-root
    // Python tests and Play metadata validators.
    return 'python@sha256:cc55e4ded9c95f6ce2133b3d096db43f214d280c2a8af65d83e246c8fa7d168c'
}

def postgis() {
    // postgis/postgis:18-3.6 as last pulled by the Flutter job (image created 2026-10-26); disposable test database.
    return 'postgis/postgis@sha256:4a43e3c3e5d55b9208240fc71db543b79c638766280c625a425bd0a2390989c4'
}

def unityAndroid() {
    // unity-android-build-env:6000.6.7f1-android-1; Unity 6000.6.7f1 with embedded Android tools.
    return 'harbor.maksonlee.com/library/unity-android-build-env@sha256:c45b47f9999a81df26f664419725ab5895438e5dbf6123bc002c766ecf3c4cf3'
}
