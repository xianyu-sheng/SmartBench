def consume(resource):
    resource.read()


def load(path):
    resource = open(path)
    consume(resource)
    return None
