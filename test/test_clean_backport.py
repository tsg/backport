from unittest import TestCase
from subprocess import check_call, call, check_output

class Test(TestCase):

    def test_clean_backport(self):
        """
        Tests that a simple PR is backported cleanly.
        """
        if len(check_output("git status -s", shell=True).strip()) > 0:
            self.fail("This tests needs a clean repo to work with")
