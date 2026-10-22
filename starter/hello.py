"""Example: a tiny function with a test next to it (test_hello.py). Replace it with your own work."""


def greet(name):
    name = (name or "").strip() or "team"
    return "Hello, %s!" % name


if __name__ == "__main__":
    print(greet("team"))
