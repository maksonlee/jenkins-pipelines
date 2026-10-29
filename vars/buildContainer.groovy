// Resource limits shared by every Android and Flutter build container. The projects' Gradle and Kotlin daemons may
// each use a 2 GB heap, so the limit leaves room for both plus metaspace, code cache and native memory.
def resourceArgs() {
    return [
        '--cpus=2',
        '--memory=6g',
        '--memory-reservation=2g',
        '--memory-swap=7g'
    ]
}
