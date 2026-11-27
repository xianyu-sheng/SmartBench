def run(enabled, acquire, release, consume):
    resource = acquire()
    if enabled:
        consume(resource)
    return None
