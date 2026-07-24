def worker():
    if completed():
        return
    retry()


def run():
    event()
    worker()
