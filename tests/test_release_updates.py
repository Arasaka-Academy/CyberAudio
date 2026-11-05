import importlib.util
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
spec = importlib.util.spec_from_file_location("release_updates", Path(__file__).parents[1] / "scripts/release_updates.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def info(version="0.2.0", build="4"):
    return {"CFBundleShortVersionString": version, "CFBundleVersion": build}


def feed(version="0.1.0", build="3"):
    return f'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><sparkle:version>{build}</sparkle:version><sparkle:shortVersionString>{version}</sparkle:shortVersionString></item></channel></rss>'.encode()


def test_tag_matches_app_and_prereleases_are_identified():
    assert release.validate_version("v0.2.0", info()) == (4, False)
    assert release.validate_version("v0.2.0-rc.1", info()) == (4, True)
    for tag, value in [("v0.2.1", info()), ("v0.2.0;anything", info()), ("v0.2.0", info(build="4.1"))]:
        with pytest.raises(ValueError): release.validate_version(tag, value)


def test_published_feed_never_moves_backwards():
    release.validate_advance(info(), feed())
    for value in [info(build="3"), info(build="2"), info("0.0.9", "5")]:
        with pytest.raises(ValueError): release.validate_advance(value, feed())
    with pytest.raises(ValueError): release.validate_advance(info(), feed("0.2.0", "4"))


def test_malformed_previous_feed_fails_closed():
    for previous in [b"not XML", b"<rss><channel/></rss>", feed(build="bad")]:
        with pytest.raises(ValueError): release.validate_advance(info(), previous)


def test_only_bootstrap_build_accepts_an_unpublished_feed():
    release.validate_advance(info(), None)
    for value in [info("0.2.1", "5"), info("0.9.0", "4")]:
        with pytest.raises(ValueError): release.validate_advance(value, None)


def test_release_classification_must_match_tag():
    release.validate_release("v0.2.0", info(), {"prerelease": False, "draft": False})
    release.validate_release("v0.2.0-rc.1", info(), {"prerelease": True, "draft": False})
    for tag, metadata in [("v0.2.0-rc.1", {"prerelease": False}),
                          ("v0.2.0", {"prerelease": True}),
                          ("v0.2.0", {"prerelease": False, "draft": True})]:
        with pytest.raises(ValueError): release.validate_release(tag, info(), metadata)
