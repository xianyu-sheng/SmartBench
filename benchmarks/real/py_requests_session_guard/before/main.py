import requests


def fetch_all(urls):
    session = requests.Session()
    results = []
    for url in urls:
        resp = session.get(url)
        results.append(resp.text)
    return results
