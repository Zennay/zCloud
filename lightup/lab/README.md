# LightUp local lab

The local lab is for integration testing LightUp itself. It must not expose services beyond loopback by default.

Current fixture:

```bash
python3 http_fixture.py
```

It binds to `127.0.0.1:18080` and returns a deterministic JSON response. Future lab components should remain disposable, resettable and isolated from production credentials and data.
