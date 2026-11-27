package sample

func run(enabled bool) {
    resource := acquire()
    if enabled {
        consume(resource)
    } else {
        return
    }
}
