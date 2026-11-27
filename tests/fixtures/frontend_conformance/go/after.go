package sample

func run(enabled bool) {
    resource := acquire()
    release(resource)
    if enabled {
        consume(resource)
    } else {
        return
    }
}
