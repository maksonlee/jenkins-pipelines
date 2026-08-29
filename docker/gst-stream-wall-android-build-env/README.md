# gst-stream-wall Android build environment

This image extends the qualified `cdlee/android-build-env` digest used by the
first local Android release build. It adds the Linux native compiler packages,
release utilities, Rust 1.98.0, the `aarch64-linux-android` Rust target, and the
official bundletool 1.18.3 all-in-one JAR verified by its published SHA-256.
Jenkins can therefore run the release container as the unprivileged `ubuntu`
user without installing release tools at build time.

The Android SDK/NDK and official GStreamer Android SDK remain versioned,
read-only Jenkins mounts. They are intentionally not duplicated in this image.
The Cargo registry cache is also mounted separately from the image's immutable
Rust toolchain.

Build and publish the qualified revision from the repository root:

```bash
image=harbor.maksonlee.com/library/gst-stream-wall-android-build-env:1.98.0-2
docker build --pull=false -t "$image" \
  docker/gst-stream-wall-android-build-env
docker push "$image"
```

After pushing, use the registry-reported digest in the release Jenkinsfile;
do not leave the job on a mutable tag.
