from app.security import compute_signature, verify_signature


def test_valid_signature_passes():
    body = b'{"hello": "world"}'
    sig = compute_signature("s3cret", body)
    assert verify_signature("s3cret", body, sig)


def test_tampered_body_fails():
    sig = compute_signature("s3cret", b"original")
    assert not verify_signature("s3cret", b"tampered", sig)


def test_wrong_secret_fails():
    sig = compute_signature("right", b"body")
    assert not verify_signature("wrong", b"body", sig)


def test_missing_header_fails():
    assert not verify_signature("s3cret", b"body", None)


def test_empty_secret_never_validates():
    sig = compute_signature("", b"body")
    assert not verify_signature("", b"body", sig)
