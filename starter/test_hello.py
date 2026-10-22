import unittest

from hello import greet


class GreetTest(unittest.TestCase):
    def test_name(self):
        self.assertEqual(greet("Ana"), "Hello, Ana!")

    def test_empty(self):
        self.assertEqual(greet("  "), "Hello, team!")


if __name__ == "__main__":
    unittest.main()
