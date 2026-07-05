from app.pipeline.dedup import compute_raw_hash, normalize_url


def test_normalize_url_strips_tracking_params():
    assert (
        normalize_url("https://example.com/news/article?utm_source=rss&utm_medium=feed&id=42")
        == "https://example.com/news/article?id=42"
    )


def test_normalize_url_strips_fragment_and_trailing_slash():
    assert (
        normalize_url("https://example.com/news/article/#comments")
        == "https://example.com/news/article"
    )


def test_normalize_url_lowercases_scheme_and_host_but_not_path():
    assert normalize_url("HTTPS://Example.COM/News") == "https://example.com/News"


def test_normalize_url_makes_equivalent_urls_identical():
    a = normalize_url("https://example.com/article?utm_campaign=x")
    b = normalize_url("https://EXAMPLE.com/article/")
    assert a == b


def test_compute_raw_hash_stable_across_url_and_title_variants():
    h1 = compute_raw_hash("https://example.com/a?utm_source=x", "Mon  Titre")
    h2 = compute_raw_hash("https://EXAMPLE.com/a/", "mon titre")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex, tient dans la colonne String(64)


def test_compute_raw_hash_differs_for_different_titles():
    h1 = compute_raw_hash("https://example.com/a", "Titre A")
    h2 = compute_raw_hash("https://example.com/a", "Titre B")
    assert h1 != h2
