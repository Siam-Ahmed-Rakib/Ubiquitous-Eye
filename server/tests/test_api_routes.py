import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_server


def test_root_endpoint_serves_frontend_index(tmp_path):
    index_file = tmp_path / "index.html"
    index_file.write_text("<html><body>frontend app</body></html>", encoding="utf-8")

    api_server.FRONTEND_DIST = tmp_path
    client = api_server.app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert response.content_type.startswith("text/html")
    assert "frontend app" in response.get_data(as_text=True)
