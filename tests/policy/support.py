"""Default-deny machine policy fixture."""


def policy_text() -> str:
    return """policy_version = 2
[remote]
url = "https://bus.test"
machine_id = "b"
credential_file = "b.token"
[[repositories]]
path = "repo"
repo_id = "xyz"
[[repositories.peers]]
machine_id = "a"
"""
