import unittest


class TestPackageImport(unittest.TestCase):
    def test_version_is_exported(self):
        import langgraph_runner

        self.assertEqual(langgraph_runner.__all__, ["__version__"])
        self.assertRegex(langgraph_runner.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
