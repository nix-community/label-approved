import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

from github import Github


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
#    input_config: dict[ApprovalLabels] = {
#        1: "12.approvals: 1",
#        2: "12.approvals: 2",
#        3: "12.approvals: 3+",
#    }


def main() -> None:
    settings = Settings()
    if settings.input_debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    g_h = Github(ghtoken())
    query: list[str] = [
         "author:r-ryantm",
        #'label:"6.topic: kernel"',
        'label:"10.rebuild-linux: 1-10"',
        #'-label:"10.rebuild-linux: 1-10"'
        '-label:"12.approvals: 1"',
        '-label:"12.approvals: 2"',
        '-label:"12.approvals: 3+',
        "draft:false",
        "is:pr",
        "is:open",
        "repo:NixOS/nixpkgs",
    ]
    #print(*query)
    pulls = g_h.search_issues(query=" ".join(query))

    logging.info(pulls.totalCount)
    for p_r_as_issue in pulls:
        p_r = p_r_as_issue.as_pull_request()
        p_r_reviews = list(p_r.get_reviews())

        approved_reviews = [review for review in p_r_reviews if review.state == "APPROVED"]
        approval_count = len(approved_reviews)

        if approval_count > 0:
            pr_labels = list(p_r.get_labels())
            pr_label_by_name = {label.name: label for label in pr_labels}
            for amount, label in label_dict.items():
                label_to_add = ""
                if approval_count >= 3:
                    label_to_add = label_dict[3]
                elif amount == approval_count:
                    label_to_add = label
                else:
                    continue

                logging.info("Adding label '%s' to PR: '%s' %s", label_to_add, p_r.number, p_r.html_url)
                p_r.add_to_labels(label_to_add)


if __name__ == "__main__":
    main()
