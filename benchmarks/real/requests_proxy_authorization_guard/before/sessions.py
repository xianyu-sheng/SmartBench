"""Scoped excerpt from psf/requests requests/sessions.py at 3022253."""


class Session:
    def rebuild_proxies(self, prepared_request, proxies):
        """Re-evaluate proxy configuration after a redirect."""
        headers = prepared_request.headers
        scheme = urlparse(prepared_request.url).scheme
        new_proxies = resolve_proxies(prepared_request, proxies, self.trust_env)

        if "Proxy-Authorization" in headers:
            del headers["Proxy-Authorization"]

        try:
            username, password = get_auth_from_url(new_proxies[scheme])
        except KeyError:
            username, password = None, None

        if username and password:
            headers["Proxy-Authorization"] = _basic_auth_str(username, password)

        return new_proxies
