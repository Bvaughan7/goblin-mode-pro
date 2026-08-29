import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import community


class SlugSafety(unittest.TestCase):
    def test_accepts_plain_slugs(self):
        self.assertEqual(community._safe_slug("cyberpunk2077"), "cyberpunk2077")
        self.assertEqual(community._safe_slug("half-life_2"), "half-life_2")

    def test_rejects_traversal_and_leading_dots(self):
        for bad in ("../etc/passwd", "..", ".hidden", "-x", "", "a/../b"):
            with self.assertRaises(community.CommunityError):
                community._safe_slug(bad)

    def test_strips_slashes(self):
        # a slash is removed entirely, never a path separator in the URL
        self.assertEqual(community._safe_slug("a/b"), "ab")


class HostPinning(unittest.TestCase):
    def test_refuses_urls_outside_the_profiles_base(self):
        for bad in (
            "https://evil.example.com/x.json",
            "https://raw.githubusercontent.com/other/repo/main/x.json",
            "http://raw.githubusercontent.com/Bvaughan7/goblin-mode-pro/main/profiles/x.json",
        ):
            with self.assertRaises(community.CommunityError):
                community._get(bad)

    def test_base_is_https_and_the_pinned_host(self):
        self.assertTrue(community._BASE.startswith("https://raw.githubusercontent.com/"))
        self.assertIn("/profiles", community._BASE)

    def test_shareable_field_set_excludes_dangerous_keys(self):
        for k in ("enabled", "auto_created", "__class__"):
            self.assertNotIn(k, community.SHAREABLE)
        self.assertIn("gamescope", community.SHAREABLE)


if __name__ == "__main__":
    unittest.main()
