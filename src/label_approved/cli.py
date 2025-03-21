import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from string import Template
from typing import Any, Optional

import requests
from github import Github
from github.Consts import DEFAULT_SECONDS_BETWEEN_REQUESTS, DEFAULT_SECONDS_BETWEEN_WRITES

DEFAULT_REPO = "NixOS/nixpkgs"


def ghtoken() -> str:
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


@dataclass
class GraphQL:
    token: str
    retries: int = 5
    repo: str = DEFAULT_REPO
    seconds_between_requests: float = DEFAULT_SECONDS_BETWEEN_REQUESTS
    seconds_between_writes: float = DEFAULT_SECONDS_BETWEEN_WRITES
    metadata: str = """
        id
        number
        url
        createdAt
        commits(last: 1) {
          edges {
            node {
              commit {
                committedDate
                status {
                  contexts {
                    context
                    targetUrl
                  }
                }
              }
            }
          }
        }
        labels(first: 100) {
          edges {
            node {
              id
              name
            }
          }
        }
        reviews(first: 100) {
          edges {
            node {
              state
              submittedAt
              author {
                login
              }
            }
          }
        }
        timelineItems(first: 100) {
          nodes {
            __typename
            ... on ReviewRequestedEvent {
              actor {
                login
              }
              requestedReviewer {
                __typename
              	... on User {
                  login
                }
              }
            }
          }
        }
    """

    def query(self, query: str) -> Any:
        headers = {"Authorization": f"token {self.token}"}
        for _ in range(self.retries):
            time.sleep(self.seconds_between_writes if "mutation" in query else self.seconds_between_requests)
            try:
                r = requests.post("https://api.github.com/graphql", headers=headers, json={"query": query})
                r.raise_for_status()
                metadata = r.json()
                if "errors" in metadata:
                    logging.warning("Failed to query GraphQL: %s", metadata["errors"])
                    continue
                return metadata
            except requests.exceptions.RequestException as e:
                logging.warning("Failed to query GraphQL: %s", e)
        raise Exception("Failed to query GraphQL after multiple retries")

    def search_issues(self, filters: str) -> Any:
        batch_size = 50
        query_template = Template("""
            query {
              rateLimit {
                limit
                cost
                remaining
                resetAt
              }
              search(
                first: $batch_size,
                query: "repo:$repo $filters",
                type: ISSUE,
              ) {
                issueCount
                nodes {
                  ... on PullRequest {
                    $metadata
                  }
                }
              }
            }
        """)
        while batch_size:
            query = query_template.substitute(
                repo=self.repo, filters=filters, metadata=self.metadata, batch_size=batch_size
            )
            if batch_size == 1:
                return self.query(query)
            try:
                return self.query(query)
            except Exception as e:
                batch_size //= 2
                logging.warning("Reducing batch size to %s", batch_size)

    def get_pull(self, number: int) -> Any:
        query_template = Template("""
            query {
              repository(owner: "$owner", name: "$name") {
                pullRequest(number: $number) {
                  $metadata
                }
              }
            }
        """)
        owner = self.repo.split("/")[0]
        name = self.repo.split("/")[1]
        query = query_template.substitute(owner=owner, name=name, number=number, metadata=self.metadata)
        return self.query(query)["data"]["repository"]["pullRequest"]

    def get_label_id(self, label: str) -> str:
        query_template = Template("""
            query {
              repository(owner: "$owner", name: "$name") {
                label(name: "$label") {
                  id
                }
              }
            }
        """)
        owner = self.repo.split("/")[0]
        name = self.repo.split("/")[1]
        query = query_template.substitute(owner=owner, name=name, label=label)
        return str(self.query(query)["data"]["repository"]["label"]["id"])

    def add_labels_to_pr(self, pr_id: str, label_ids: list[str]) -> None:
        query_template = Template("""
            mutation {
              addLabelsToLabelable(input: {labelableId: "$pr_id", labelIds: [$label_ids]}) {
                clientMutationId
              }
            }
        """)
        query = query_template.substitute(pr_id=pr_id, label_ids=", ".join(f'"{label_id}"' for label_id in label_ids))
        self.query(query)

    def remove_labels_from_pr(self, pr_id: str, label_ids: list[str]) -> None:
        query_template = Template("""
            mutation {
              removeLabelsFromLabelable(input: {labelableId: "$pr_id", labelIds: [$label_ids]}) {
                clientMutationId
              }
            }
        """)
        query = query_template.substitute(pr_id=pr_id, label_ids=", ".join(f'"{label_id}"' for label_id in label_ids))
        self.query(query)


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
class PrWithGraphQL:
    g_h_graphql: GraphQL
    metadata: Any
    label_ids: dict[str, str]
    dry_run: bool

    def get_number(self) -> int:
        return int(self.metadata["number"])

    def get_reviews(self) -> list[Review]:
        reviews: list[Review] = []
        for review in self.metadata["reviews"]["edges"]:
            # can be None if the account has been removed
            author = review["node"]["author"]["login"] if review["node"]["author"] else "ghost"
            submitted_at = datetime.fromisoformat(review["node"]["submittedAt"][:-1])
            reviews.append(Review(author, review["node"]["state"], submitted_at))
        return reviews

    def get_last_commit_date(self) -> Optional[datetime]:
        commits = self.metadata["commits"]["edges"]
        if not commits:
            return None
        return datetime.fromisoformat(commits[0]["node"]["commit"]["committedDate"][:-1])

    def get_last_commit_statuses(self) -> list[Status]:
        commits = self.metadata["commits"]["edges"]
        if not commits or not commits[0]["node"]["commit"]["status"]:
            return []
        contexts = commits[0]["node"]["commit"]["status"]["contexts"]
        return [Status(context["context"], context["targetUrl"]) for context in contexts]

    def get_maintainers(self) -> Optional[set[str]]:
        has_github_eval = False
        for status in self.get_last_commit_statuses():
            # ofborg eval was gone on 2024-12-31
            # if the status is still there, the PR is too old; no need to update labels
            if status.context == "ofborg-eval-check-maintainers":
                return None
            if status.context == "Eval / Summary":
                has_github_eval = True
        # if the GHA eval is not done, do not update labels either
        if not has_github_eval:
            return None

        maintainers: set[str] = set()
        for event in self.metadata["timelineItems"]["nodes"]:
            if event["__typename"] != "ReviewRequestedEvent":
                continue
            if not event["requestedReviewer"] or event["requestedReviewer"]["__typename"] != "User":
                continue
            if event["actor"]["login"] == "nix-owners":
                maintainers.add(event["requestedReviewer"]["login"])
        return maintainers

    def get_labels(self) -> set[str]:
        return {label["node"]["name"] for label in self.metadata["labels"]["edges"]}

    def add_labels(self, labels: set[str]) -> None:
        if not labels:
            return
        for label in labels:
            logging.info("Adding label '%s' to PR: '%s' %s", label, self.metadata["number"], self.metadata["url"])
        if not self.dry_run:
            self.g_h_graphql.add_labels_to_pr(self.metadata["id"], [self.label_ids[label] for label in labels])

    def remove_labels(self, labels: set[str]) -> None:
        if not labels:
            return
        for label in labels:
            logging.info("Removing label '%s' from PR: '%s' %s", label, self.metadata["number"], self.metadata["url"])
        if not self.dry_run:
            self.g_h_graphql.remove_labels_from_pr(self.metadata["id"], [self.label_ids[label] for label in labels])


settings = Settings()
if settings.input_debug:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)


def process_pr(g_h: Github, p_r_object: PrWithGraphQL) -> None:
    logging.info("Processing %s", p_r_object.get_number())

    logging.debug(p_r_object)

    p_r_reviews = p_r_object.get_reviews()

    approved_users: set[str] = set()
    last_approved_review_date = None
    for review in p_r_reviews:
        reviewed_user = review.author
        if review.state == "APPROVED":
            approved_users.add(reviewed_user)
            last_approved_review_date = review.submitted_at
        else:
            approved_users.discard(reviewed_user)

    old_labels: set[str] = p_r_object.get_labels() & set(label_dict.values())

    labels: set[str] = set()
    approval_count = min(len(approved_users), max(label_dict.keys()))

    if last_approved_review_date is not None:
        last_commit_date = p_r_object.get_last_commit_date()
        # if there are no commits, the PR is closed; no need to update labels
        if last_commit_date is None:
            return

        logging.info("lastappdate: %s", last_approved_review_date)
        logging.info("lastcommitdate: %s", last_commit_date)

        if last_commit_date <= last_approved_review_date:
            if approval_count:
                labels.add(label_dict[approval_count])

                maintainers = p_r_object.get_maintainers()
                if maintainers is None:
                    old_labels.discard(label_dict[-1])
                elif approved_users & maintainers:
                    labels.add(label_dict[-1])

    # never remove the maintainer label
    if label_dict[-1] in old_labels - labels:
        old_labels.remove(label_dict[-1])

    p_r_object.remove_labels(old_labels - labels)
    p_r_object.add_labels(labels - old_labels)


def main() -> None:
    parser = argparse.ArgumentParser(description="nixpkgs PR approvals labeler")
    parser.add_argument("--dry_run", action="store_true")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--single_pr", type=int, help="Run on a single PR instead of crawling the repository")
    args = parser.parse_args()

    g_h_token = ghtoken()
    g_h = Github(g_h_token)
    g_h_graphql = GraphQL(g_h_token)

    if args.dry_run:
        logging.warning("Running in dry run mode, no changes will be applied")

    label_ids = {}
    for label in label_dict.values():
        logging.info("Retrieving label id for '%s'", label)
        label_ids[label] = g_h_graphql.get_label_id(label)

    if args.single_pr is not None:
        p_r = g_h_graphql.get_pull(args.single_pr)
        process_pr(g_h, PrWithGraphQL(g_h_graphql, p_r, label_ids, args.dry_run))
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
        metadata = g_h_graphql.search_issues(" ".join(query))
        logging.info("Pulls total: %s", metadata["data"]["search"]["issueCount"])

        pulls = metadata["data"]["search"]["nodes"]
        while pulls:
            for p_r in pulls:
                process_pr(g_h, PrWithGraphQL(g_h_graphql, p_r, label_ids, args.dry_run))

            logging.info("Remaining GraphQL API rate limit: %s", metadata["data"]["rateLimit"]["remaining"])
            logging.info("Remaining REST API rate limit: %s", g_h.get_rate_limit().core.remaining)

            metadata = g_h_graphql.search_issues(f'{" ".join(query)} created:<{pulls[-1]["createdAt"]}')
            pulls = metadata["data"]["search"]["nodes"]


if __name__ == "__main__":
    main()
