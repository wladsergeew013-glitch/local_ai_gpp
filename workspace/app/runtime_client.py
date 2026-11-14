"""The only Workspace interface to Local AI Runtime. No shared files/imports."""

from urllib.parse import quote, urlsplit
import ipaddress
import httpx


def local_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Use an http:// loopback URL without credentials, query or fragment."
        )
    host = parsed.hostname or ""
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.lower() == "localhost"
    if not local:
        raise ValueError("This local profile accepts loopback endpoints only.")
    return value.strip().rstrip("/")


class RuntimeClient:
    def __init__(self, provider):
        self.base_url = local_url(provider["base_url"])
        self.model = provider["model"]
        self.key = provider.get("api_key") or ""

    def request(self, method, path, **kwargs):
        headers = {"Authorization": "Bearer " + self.key} if self.key else {}
        with httpx.Client(
            base_url=self.base_url + "/",
            headers=headers,
            timeout=180,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            result = client.request(method, path, **kwargs)
            result.raise_for_status()
            return result.json()

    def capabilities(self, load=False):
        encoded = quote(self.model, safe="")
        result = self.request("GET", "models/" + encoded + "/capabilities")
        if load and not result.get("loaded"):
            result = self.request("POST", "models/" + encoded + "/load")
        if str(result.get("api_version", "")).split(".")[0] != "1":
            raise ValueError("Unsupported Runtime API version.")
        return result

    def cancel_owner(self, owner_id, token):
        return self.request(
            "POST",
            "owners/" + quote(owner_id, safe="") + "/cancel",
            headers={"X-Local-AI-Owner-Token": token}, timeout=3,
        )
