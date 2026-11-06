# Unity 6000.6.7f1 Android build environment

This image pins GameCI Editor `ubuntu-6000.6.7f1-android-3` at OCI index digest
`sha256:f69fc708d7d6c0aa03521b6facb22c56744d43da2537d213e3a2e9f5bfb61e50`
(linux/amd64 manifest `sha256:2f3d44a05737a7e5d7369a530d4e4697a08f7d6691c6c2ab8972089a87a17a16`).
It retains the Editor's Android SDK, NDK and OpenJDK under
`/opt/unity/Editor/Data/PlaybackEngines/AndroidPlayer`. The base already has
`git`, `curl`, `jq`, `zip` and `unzip`; the Dockerfile checks these tools without
installing a second Android or Java toolchain.

The default user remains root, with `/root` as its home. There is no ENTRYPOINT,
and the default command is `/bin/bash`, so the GameCI CLI can mount its dedicated
home directory at `/root`, mount a project at `/github/workspace`, and invoke its
`/entrypoint.sh`. Use a dedicated, minimal home mount for that CLI, rather than
mounting the Jenkins agent's whole home directory. Unity credentials and project
secrets are supplied only at runtime; this image contains none.

An optional direct `docker run -u 1000:1000` mode can use `/workspace` and
`/home/ubuntu`, which are owned by UID/GID 1000. Set `-e HOME=/home/ubuntu`,
mount the project at `/workspace`, and use a writable host checkout. For another
UID, mount a home directory owned by that UID and set `HOME` to that mounted path.
The scoped smoke test checks workspace and home writes only; this route does not
establish Android SDK installation, Unity licensing, or AAB build capability.
GameCI CLI starts as root for its Android setup and license lifecycle. Do not
change ownership of the Unity installation. Unity needs more than 1 GiB shared
memory at runtime; use at least `--shm-size=1025m` when launching the container.

Build from the repository root on the Linux amd64 Docker host:

```bash
image=harbor.maksonlee.com/library/unity-android-build-env:6000.6.7f1-android-1
docker build --platform=linux/amd64 --pull=false -t "$image" \
  docker/unity-android-build-env
docker push "$image"
```

The published revision, read back from Harbor on 2026-11-06, is
`harbor.maksonlee.com/library/unity-android-build-env@sha256:c45b47f9999a81df26f664419725ab5895438e5dbf6123bc002c766ecf3c4cf3`.
Its manifest references config digest
`sha256:087b65f7903573240615692e4ccebe9294440c8e3630f39bac73723f4326c1a1`,
matching the validated local image. Use the immutable digest in a future Jenkins
job or `vars/buildImages.groovy` entry. This image build does not activate Unity,
build a project or publish an Android App Bundle.
