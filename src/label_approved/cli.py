import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from github import Github
from github.Commit import Commit
from github.PullRequest import PullRequest


DEFAULT_REPO = "NixOS/nixpkgs"


def ghtoken() -> Optional[str]:
    token = os.getenv("GITHUB_BOT_TOKEN")
    if not token:
        token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("need a github token")
        sys.exit(1)
    return token


label_dict: dict[int, str] = {
    1: "12.approvals: 1",
    2: "12.approvals: 2",
    3: "12.approvals: 3+",
}


@dataclass
class Settings:
    input_debug: Optional[bool] = False


@dataclass
class PrWithApprovals:
    p_r: PullRequest
    new_label: int
    previous_label: int = 0

    def same_as_before(self) -> bool:
        return self.new_label == self.previous_label


settings = Settings()
if settings.input_debug:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)


def get_maintainers(g_h: Github, commit: Commit) -> list[str]:
    maintainers: list[str] = []
    for status in commit.get_statuses():
        if status.context == "ofborg-eval-check-maintainers":
            gist_url = status.target_url
            if gist_url:
                gist_id = gist_url.rsplit("/", 1)[-1]

                gist = g_h.get_gist(gist_id)
                pot_maint_file_contents = gist.files["Potential Maintainers"].content
                for line in pot_maint_file_contents.splitlines():
                    if line == "Maintainers:":
                        continue
                    maintainer = line.split(":")[0].strip()
                    maintainers.append(maintainer)
    return maintainers


def process_pr(g_h: Github, p_r: PullRequest, *, dry_run: bool = False) -> None:
    logging.info("Processing %s", p_r.number)
    last_commit = list(p_r.get_commits())[-1]
    last_commit_date = datetime.min.date()
    last_commit_date = last_commit.commit.committer.date

    p_r_reviews = list(p_r.get_reviews())

    approvals = dict()
    last_approved_review_date = datetime.min.date()
    for review in p_r_reviews:
        if review.state == "APPROVED":
            approvals[review.user] = review
            last_approved_review_date = review.submitted_at
        else:
            try:
                del approvals[review.user]
            except KeyError:
                pass

    approval_count = len(approvals)

    pr_labels = list(p_r.get_labels())
    pr_label_by_name = {label.name: label for label in pr_labels}
    old_approval_count = 0
    if o_a_c := [k for k, v in label_dict.items() if v in pr_label_by_name]:
        old_approval_count = o_a_c[0]

    pr_object = PrWithApprovals(p_r, approval_count, old_approval_count)
    logging.debug(pr_object)

    p_r_url = pr_object.p_r.html_url
    p_r_num = pr_object.p_r.number

    if last_approved_review_date != datetime.min.date():
        logging.info("lastappdate: %s", last_approved_review_date)
        logging.info("lastcommitdate: %s", last_commit_date)
        if last_commit_date > last_approved_review_date:
            if pr_object.previous_label != 0:
                label_to_remove = label_dict[pr_object.previous_label]
                logging.info("Removing label '%s' from PR: '%s' %s", label_to_remove, p_r_num, p_r_url)
                if not dry_run:
                    pr_object.p_r.remove_from_labels(label_to_remove)
            return


    if pr_object.same_as_before():
        return

    label_to_add = ""
    if approval_count >= 3:
        label_to_add = label_dict[3]
    else:
        label_to_add = label_dict[pr_object.new_label]

    if pr_object.previous_label > 0:
        if pr_object.previous_label == 3 and approval_count >= 3:
            return
        label_to_remove = label_dict[pr_object.previous_label]
        logging.info("Removing label '%s' from PR: '%s' %s", label_to_remove, p_r_num, p_r_url)
        if not dry_run:
            pr_object.p_r.remove_from_labels(label_to_remove)

    logging.info("Adding label '%s' to PR: '%s' %s", label_to_add, p_r_num, p_r_url)
    if not dry_run:
        pr_object.p_r.add_to_labels(label_to_add)

    maintainers: list[str] = get_maintainers(g_h, last_commit)

    for a_u in approvals.keys():
        if a_u.login in maintainers:
            logging.info("Adding label '12.approved-by: package-maintainer' to PR: '%s' %s", p_r_num, p_r_url)
            if not dry_run:
                pr_object.p_r.add_to_labels("12.approved-by: package-maintainer")


def main() -> None:
    parser = argparse.ArgumentParser(description="nixpkgs PR approvals labeler")
    parser.add_argument("--dry_run", action="store_true")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--single_pr", type=int, help="Run on a single PR instead of crawling the repository")
    args = parser.parse_args()

    g_h = Github(ghtoken())

    if args.dry_run:
        logging.warning("Running in dry run mode, no changes will be applied")

    if args.single_pr is not None:
        repo = g_h.get_repo(args.repo)
        p_r = repo.get_pull(args.single_pr)
        process_pr(g_h, p_r, dry_run=args.dry_run)
    else:
        query: list[str] = [
            # "author:r-ryantm",
            #'label:"10.rebuild-linux: 1-10"',
            #'-label:"10.rebuild-linux: 1-10"'
            #'label:"12.approvals: 1"',
            #'-label:"12.approvals: 2"',
            #'-label:"12.approvals: 3+',
            # "base:staging",
            # "sort:updated-desc",
            # "sort:created-desc",
            "draft:false",
            "is:pr",
            "is:open",
            f"repo:{args.repo}",
        ]
        pulls = g_h.search_issues(query=" ".join(query))

        logging.info("Pulls total: %s", pulls.totalCount)
        for p_r_as_issue in pulls:
            process_pr(g_h, p_r_as_issue.as_pull_request(), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
