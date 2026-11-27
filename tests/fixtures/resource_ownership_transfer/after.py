def close_resource(resource):
    resource.close()


def load(path):
    resource = open(path)
    close_resource(resource)
    return resource.read()
