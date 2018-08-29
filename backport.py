#!/usr/bin/env python
"""Cherry pick and backport a PR"""

import sys
import os
import argparse
from os.path import expanduser
import re
from subprocess import check_call, call, check_output
import requests
from pprint import pprint
import json

usage = """
Example usage:

./backport.py -b 6.x,6.3 2565 6490604aa0cf7fa61932a90700e6ca988fc8a527

In case of backporting errors, fix them, then run:

git cherry-pick --continue
./dev-tools/cherrypick_pr --create_pr 5.0 2565 6490604aa0cf7fa61932a90700e6ca988fc8a527 --continue

This script does the following:

* cleanups both from_branch and to_branch (warning: drops local changes)
* creates a temporary branch named something like "branch_2565"
* calls the git cherry-pick command in this branch
* after fixing the merge errors (if needed), pushes the branch to your
  remote
* if the --create_pr flag is used, it uses the GitHub API to create the PR
  for you. Note that this requires you to have a Github token with the
  public_repo scope in the `~/.elastic/github.token` file

Note that you need to take the commit hashes from `git log` on the
from_branch, copying the IDs from Github doesn't work in case we squashed the
PR.
"""


def main():
    parser = argparse.ArgumentParser(
        description="Creates a PR for cherry-picking commits",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=usage)
    parser.add_argument("pr_number",
                        help="The PR number being merged (e.g. 2345)")
    parser.add_argument("--to_branches", "-b",
                        help="To branch (e.g 5.0)")
    parser.add_argument("--commit_hashes", metavar="hash", nargs="+",
                        help="The commit hashes to cherry pick." +
                        " You can specify multiple.")
    parser.add_argument("--yes", action="store_true",
                        help="Assume yes. Warning: discards local changes.")
    parser.add_argument("--continue", action="store_true",
                        help="Continue after fixing merging errors.")
    parser.add_argument("--from_branch", default="master",
                        help="From branch")
    parser.add_argument("--remote", "-r",
                        help="Remote to which to push (your fork)")
    parser.add_argument("--project", default="elastic/beats",
                        help="The Github project")
    parser.add_argument("--no_version", action="store_true",
                        help="Skip setting version labels.")
    args = parser.parse_args()

    print(args)

    token = open(expanduser("~/.elastic/github.token"), "r").read().strip()
    base = "https://api.github.com/repos/{}".format(args.project)
    session = requests.Session()
    session.headers.update({"Authorization": "token " + token})

    # get PR
    request = session.get(base + "/pulls/{}".format(args.pr_number))
    if request.status_code > 299:
        print("Getting PR failed: {}".format(request.json()))
        sys.exit(1)
    pr = request.json()

    if not pr["merged"]:
        print("PR is not merged")
        return 1

    if not args.commit_hashes:
        args.commit_hashes = [pr["merge_commit_sha"]]

    pprint(pr)
    #return 1


    continue_backport = False
    to_branches = args.to_branches.split(",")
    if vars(args)["continue"]:
        if len(check_output("git status -s", shell=True).strip()) > 0:
            print("Looks like you have uncommitted changes." +
                  " Please execute first: git cherry-pick --continue")
            return 1

        to_branches = load_state(args)
        continue_backport = True
    else:
        if not args.yes and raw_input("This will destroy all local changes. " +
                                      "Continue? [y/n]: ") != "y":
            return 1
        check_call("git reset --hard", shell=True)
        check_call("git clean -df", shell=True)
        check_call("git fetch", shell=True)

        check_call("git checkout {}".format(args.from_branch), shell=True)
        check_call("git pull", shell=True)

    if args.remote:
        remote = args.remote
    else:
        remote = raw_input("To which remote should I push? (your fork): ")

    for i, to_branch in enumerate(to_branches):
        tmp_branch = "backport_{}_{}".format(args.pr_number, to_branch)

        if continue_backport:
            continue_backport = False
        else:
            check_call("git checkout {}".format(to_branch), shell=True)
            check_call("git pull", shell=True)

            call("git branch -D {} > /dev/null".format(tmp_branch), shell=True)
            check_call("git checkout -b {}".format(tmp_branch), shell=True)
            if call("git cherry-pick -x {}".format(" ".join(args.commit_hashes)),
                    shell=True) != 0:
                print("Looks like you have cherry-pick errors.")
                print("Fix them, then run: ")
                print("    git cherry-pick --continue")
                print("    {} --continue".format(" ".join(sys.argv)))
                save_state(args, to_branches[i:])
                return 1

        if len(check_output("git log HEAD...{}".format(to_branch),
                            shell=True).strip()) == 0:
            print("No commit to push")
            continue

        print("Ready to push branch.")
        call("git push {} :{} > /dev/null".format(remote, tmp_branch),
             shell=True)
        check_call("git push --set-upstream {} {}"
                   .format(remote, tmp_branch), shell=True)


        original_pr = session.get(base + "/pulls/" + args.pr_number).json()

        # get the github username from the remote where we pushed
        remote_url = check_output("git remote get-url {}".format(remote),
                                  shell=True)
        remote_user = re.search("github.com:(.+)/.+", remote_url).group(1)

        # create PR
        request = session.post(base + "/pulls", json=dict(
            title="Cherry-pick #{} to {}: {}".format(args.pr_number, to_branch, original_pr["title"]),
            head=remote_user + ":" + tmp_branch,
            base=to_branch,
            body="Cherry-pick of PR #{} to {} branch. Original message: \n\n{}"
            .format(args.pr_number, to_branch, original_pr["body"])
        ))
        if request.status_code > 299:
            print("Creating PR failed: {}".format(request.json()))
            sys.exit(1)
        new_pr = request.json()

        # add labels
        session.post(
            base + "/issues/{}/labels".format(new_pr["number"]), json=["backport", "review"])

        # remove needs backport label from the original PR
        session.delete(base + "/issues/{}/labels/needs_backport".format(args.pr_number))

        # get version and set a version label on the original PR
        if not args.no_version:
            version = get_version(os.getcwd())
            if version:
                session.post(
                    base + "/issues/{}/labels".format(args.pr_number), json=["v" + version])

        print("\nDone. PR created: {}".format(new_pr["html_url"]))
        print("Please go and check it and add the review tags")

def get_version(beats_dir):
    pattern = re.compile(r'(const\s|)\w*(v|V)ersion\s=\s"(?P<version>.*)"')
    with open(os.path.join(beats_dir, "libbeat/version/version.go"), "r") as f:
        for line in f:
            match = pattern.match(line)
            if match:
                return match.group('version')

def save_state(args, remaining_branches):
    with open(".backport.state", "w") as f:
        json.dump({"args": vars(args), "remaining_branches": remaining_branches}, f)

def load_state(args):
    with open(".backport.state", "r") as f:
        obj = json.load(f)
        args.__dict__.update(obj["args"])
        return obj["remaining_branches"]


if __name__ == "__main__":
    sys.exit(main())
