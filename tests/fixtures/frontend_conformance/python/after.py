def run(enabled, acquire, release, consume):
    resource = acquire()
    release(resource)
    if enabled:
        consume(resource)
    return None
