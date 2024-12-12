# -*- coding: utf-8 -*-
"""
@Time    : 2024/12/12 下午10:45
@Author  : Kend
@FileName: test.py
@Software: PyCharm
@modifier:
"""


import pytest
from fastapi.testclient import TestClient
from static_folder_server import StaticFileServer

@pytest.fixture
def client():
    server = StaticFileServer(folder_path=r"D:\kend\tests")
    app = server.create_app()
    return TestClient(app)

def test_serve_file(client):
    response = client.get("/test01.jpg")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.headers["Content-Disposition"] == "attachment; filename=test01.jpg"
