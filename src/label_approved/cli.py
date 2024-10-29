import argparse
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from github import Github
from github.Commit import Commit
from github.Consts import DEFAULT_PER_PAGE
from github.PullRequest import PullRequest

DEFAULT_REPO = "NixOS/nixpkgs"


def ghtoken() -> Optional[str]:
    for env_key in ("INPUT_GITHUB_TOKEN", "GITHUB_BOT_TOKEN", "GITHUB_TOKEN"):
        token = os.getenv(env_key)
        if token:
            return token

    if shutil.which("gh"):
        r = subprocess.run(["gh", "auth", "token"], stdout=subprocess.PIPE, text=True)
        if r.returncode == 0:
            return r.stdout.strip()

    print("need a github token")
    sys.exit(1)


label_dict: dict[int, str] = {
    -1: "12.approved-by: package-maintainer",
    1: "12.approvals: 1",
    2: "12.approvals: 2",
    3: "12.approvals: 3+",
}


@dataclass
class Settings:
    input_debug: Optional[bool] = False


@dataclass
class Review:
    author: str
    state: str
    submitted_at: datetime


@dataclass
class Status:
    context: str
    target_url: str


@dataclass
class PrWithApprovals:
    p_r: PullRequest
    dry_run: bool
    last_commit: Optional[Commit] = None

    def get_number(self) -> int:
        return self.p_r.number

    def get_reviews(self) -> list[Review]:
        reviews: list[Review] = []
        for review in self.p_r.get_reviews():
            # can be None if the account has been removed
            author = review.user.login if review.user is not None else "ghost"
            reviews.append(Review(author, review.state, review.submitted_at))
        return reviews

    def get_last_commit_date(self) -> Optional[datetime]:
        commits = list(self.p_r.get_commits())
        if not commits:
            return None
        self.last_commit = commits[-1]
        return self.last_commit.commit.committer.date

    def get_last_commit_statuses(self) -> list[Status]:
        if self.last_commit is None:
            return []
        return [Status(status.context, status.target_url) for status in self.last_commit.get_statuses()]

    def get_labels(self) -> set[str]:
        return {label.name for label in self.p_r.get_labels()}

    def add_labels(self, labels: set[str]) -> None:
        for label in labels:
            logging.info("Adding label '%s' to PR: '%s' %s", label, self.p_r.number, self.p_r.html_url)
            if not self.dry_run:
                self.p_r.add_to_labels(label)

    def remove_labels(self, labels: set[str]) -> None:
        for label in labels:
            logging.info("Removing label '%s' from PR: '%s' %s", label, self.p_r.number, self.p_r.html_url)
            if not self.dry_run:
                self.p_r.remove_from_labels(label)


settings = Settings()
if settings.input_debug:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)


def get_maintainers(g_h: Github, p_r_object: PrWithApprovals) -> set[str]:
    maintainers: set[str] = set()
    for status in p_r_object.get_last_commit_statuses():
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
                    maintainers.add(maintainer)
    return maintainers


def process_pr(g_h: Github, p_r_object: PrWithApprovals) -> None:
    logging.info("Processing %s", p_r_object.get_number())

    logging.debug(p_r_object)

    p_r_reviews = p_r_object.get_reviews()

    approved_users: set[str] = set()
    last_approved_review_date = None
    for review in p_r_reviews:
        reviewed_user = review.author.lower()
        if review.state == "APPROVED":
            approved_users.add(reviewed_user)
            last_approved_review_date = review.submitted_at
        else:
            approved_users.discard(reviewed_user)

    old_labels: set[str] = p_r_object.get_labels() & set(label_dict.values())

    labels: set[str] = set()
    if last_approved_review_date is not None:
        last_commit_date = p_r_object.get_last_commit_date()
        # if there are no commits, the PR is closed; no need to update labels
        if last_commit_date is None:
            return

        logging.info("lastappdate: %s", last_approved_review_date)
        logging.info("lastcommitdate: %s", last_commit_date)

        if last_commit_date <= last_approved_review_date:
            approval_count = min(len(approved_users), max(label_dict.keys()))
            if approval_count:
                labels.add(label_dict[approval_count])

                maintainers: set[str] = get_maintainers(g_h, p_r_object)
                if approved_users & maintainers:
                    labels.add(label_dict[-1])

    p_r_object.remove_labels(old_labels - labels)
    p_r_object.add_labels(labels - old_labels)


def main() -> None:
    parser = argparse.ArgumentParser(description="nixpkgs PR approvals labeler")
    parser.add_argument("--dry_run", action="store_true")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--single_pr", type=int, help="Run on a single PR instead of crawling the repository")
    parser.add_argument("--enable_throttling", action="store_true", help="Enable default throttling mechanism to mitigate secondary rate limit errors")
    parser.add_argument("--paginate", action="store_true", help="Make additional requests to run on all PRs")
    args = parser.parse_args()

    if args.enable_throttling:
        g_h = Github(ghtoken())
    else:
        g_h = Github(ghtoken(), seconds_between_requests=0, seconds_between_writes=0)

    if args.dry_run:
        logging.warning("Running in dry run mode, no changes will be applied")

    if args.single_pr is not None:
        repo = g_h.get_repo(args.repo)
        p_r = repo.get_pull(args.single_pr)
        process_pr(g_h, PrWithApprovals(p_r, args.dry_run))
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

        paginated_pulls = [g_h.search_issues(query=" ".join(query))]
        last_pulls = paginated_pulls[-1]
        total_count = last_pulls.totalCount

        while args.paginate and last_pulls.totalCount:
            last_pull = last_pulls.get_page((last_pulls.totalCount - 1) // DEFAULT_PER_PAGE)[-1]
            last_created_at = last_pull.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")

            paginated_pulls.append(g_h.search_issues(query=" ".join(query) + f" created:<{last_created_at}"))
            last_pulls = paginated_pulls[-1]
            total_count += last_pulls.totalCount

        logging.info("Pulls total: %s", total_count)
        for pulls in paginated_pulls:
            for p_r_as_issue in pulls:
                p_r = p_r_as_issue.as_pull_request()
                process_pr(g_h, PrWithApprovals(p_r, args.dry_run))


if __name__ == "__main__":
    main()
