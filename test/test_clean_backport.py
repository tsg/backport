from unittest import TestCase
from subprocess import check_call, call, check_output
import shutil
import os
from os.path import expanduser
from pprint import pprint
import requests

class Test(TestCase):

    def setUp(self):
        self.repo = 'tsg/test-backports'
        shutil.rmtree('test-backports', ignore_errors=True)
        check_call('git clone git@github.com:{}.git'.format(self.repo), shell=True)
        os.chdir('test-backports')

        token = open(expanduser("~/.elastic/github.token"), "r").read().strip()
        self.base = "https://api.github.com/repos/{}".format(self.repo)
        self.session = requests.Session()
        self.session.headers.update({"Authorization": "token " + token})

    def tearDown(self):
        os.chdir('..')

    def open_pr(self, branch, target, test_number):

        remote_user = self.repo.split("/")[0]

        request = self.session.post(self.base + "/pulls", json=dict(
            title="PR test {}".format(test_number),
            head=remote_user + ":" + branch,
            base=target,
            body="Automatic PR created for testing"
        ))
        if request.status_code > 299:
            self.fail("Creating PR failed: {}".format(request.json()))
        new_pr = request.json()
        return new_pr["number"]

    def merge_pr(self, pr_number):
        request = self.session.put(
            self.base + '/pulls/{}/merge'.format(pr_number), json=dict(
            merge_method='squash'
        ))
        if request.status_code > 299:
            self.fail("Merging PR failed: {}".format(request.json()))

    def test_clean_backport(self):
        """
        Tests that a simple PR is backported cleanly.
        """
        with open('test', 'r') as f:
            test_number = int(f.read().strip()) + 1
        check_call('git co -b branch_{}'.format(test_number), shell=True)
        with open('test', 'w') as f:
            f.write("{}\n".format(test_number))
        check_call('git ci -a -m "Commit {}"'.format(test_number), shell=True)
        check_call('git push --set-upstream origin branch_{}'.format(test_number),
                   shell=True)
        pr_number = self.open_pr('branch_{}'.format(test_number), 'master', test_number)
        self.merge_pr(pr_number)

        check_call('../backport.py --project "{}" --yes --no_version -b 6.x,6.4 -r origin {}'
                   .format(self.repo, pr_number), shell=True)


        # check and merge 6.x backport
        request = self.session.get(self.base + "/pulls/{}".format(pr_number+1))
        if request.status_code > 299:
            self.fail("Getting PR failed: {}".format(request.json()))
        pr = request.json()
        pprint(pr)
        assert 'backport' in [label['name'] for label in pr["labels"]]
        assert pr['base']['ref'] == '6.x'
        self.merge_pr(pr_number+1)

        # check and merge 6.4 backport
        request = self.session.get(self.base + "/pulls/{}".format(pr_number+2))
        if request.status_code > 299:
            self.fail("Getting PR failed: {}".format(request.json()))
        pr = request.json()
        assert 'backport' in [label['name'] for label in pr["labels"]]
        assert pr['base']['ref'] == '6.4'
        self.merge_pr(pr_number+2)
