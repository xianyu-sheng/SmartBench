import requests


def fetch_all(urls):
    with requests.Session() as session:
        results = []
        for url in urls:
            resp = session.get(url)
            results.append(resp.text)
    return results
