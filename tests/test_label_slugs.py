import unittest
from pipeline.by_label import slug


class LabelSlugTests(unittest.TestCase):
    def test_slug_preserves_distinct_labels_that_normalize_to_same_text(self):
        self.assertNotEqual(slug("A/B"), slug("A?B"))


if __name__ == "__main__":
    unittest.main()
