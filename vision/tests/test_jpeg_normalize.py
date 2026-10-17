import numpy as np

from service import normalize_jpeg


def test_strips_leading_crlf_before_soi():
    inner = b"\xff\xd8\xff\xe0" + b"x" * 10 + b"\xff\xd9"
    wrapped = b"\r\n" + inner
    out = normalize_jpeg(wrapped)
    assert out.startswith(b"\xff\xd8")
    assert out.endswith(b"\xff\xd9")
