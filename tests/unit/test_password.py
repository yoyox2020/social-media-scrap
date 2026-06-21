from app.infrastructure.security.password import hash_password, verify_password


def test_hash_password_produces_different_hash():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2  # bcrypt includes random salt


def test_verify_password_correct():
    hashed = hash_password("mypassword")
    assert verify_password("mypassword", hashed) is True


def test_verify_password_wrong():
    hashed = hash_password("mypassword")
    assert verify_password("wrongpassword", hashed) is False
