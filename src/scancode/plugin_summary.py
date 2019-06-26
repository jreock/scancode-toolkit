#
# Copyright (c) 2019 nexB Inc. and others. All rights reserved.
# http://nexb.com and https://github.com/nexB/scancode-toolkit/
# The ScanCode software is licensed under the Apache License version 2.0.
# Data generated with ScanCode require an acknowledgment.
# ScanCode is a trademark of nexB Inc.
#
# You may not use this software except in compliance with the License.
# You may obtain a copy of the License at: http://apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
#
# When you publish or redistribute any data created with ScanCode or any ScanCode
# derivative work, you must accompany this data with the following acknowledgment:
#
#  Generated with ScanCode and provided on an "AS IS" BASIS, WITHOUT WARRANTIES
#  OR CONDITIONS OF ANY KIND, either express or implied. No content created from
#  ScanCode should be considered or used as legal advice. Consult an Attorney
#  for any legal advice.
#  ScanCode is a free software code scanning tool from nexB Inc. and others.
#  Visit https://github.com/nexB/scancode-toolkit/ for support and download.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from collections import Counter
from collections import defaultdict
from collections import OrderedDict

import attr

from commoncode.text import python_safe_name
from packagedcode.utils import combine_expressions
from plugincode.post_scan import PostScanPlugin
from plugincode.post_scan import post_scan_impl
from scancode import CommandLineOption
from scancode import POST_SCAN_GROUP


# Tracing flags
TRACE = True


def logger_debug(*args):
    pass


if TRACE:
    import logging
    import sys

    logger = logging.getLogger(__name__)
    # logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
    logging.basicConfig(stream=sys.stdout)
    logger.setLevel(logging.DEBUG)

    def logger_debug(*args):
        return logger.debug(
            ' '.join(isinstance(a, unicode) and a or repr(a) for a in args))


@attr.s
class Summary(object):
    identifier = attr.ib()
    license_expression = attr.ib()
    holders = attr.ib()
    type=attr.ib()


@post_scan_impl
class OriginSummary(PostScanPlugin):
    """
    Summarize copyright holders and license expressions to the directory level if a copyright holder
    or license expression is detected in 75% or more of total files in a directory
    """
    codebase_attributes = dict(
        summaries=attr.ib(default=attr.Factory(list))
    )

    resource_attributes = dict(
        origin_summary=attr.ib(default=attr.Factory(OrderedDict)),
        summarized_to=attr.ib(default=None, type=str)
    )

    sort_order = 8

    options = [
        CommandLineOption(('--origin-summary',),
            is_flag=True, default=False,
            help='Summarize copyright holders and license expressions to the directory level '
                 'if a copyright holder or license expression is detected in 75% or more of '
                 'total files in a directory.',
            help_group=POST_SCAN_GROUP
        ),
        CommandLineOption(('--origin-summary-threshold',),
            is_flag=False, type=float,
            help='Set a custom threshold for origin summarization.',
            required_options=['origin_summary'],
            help_group=POST_SCAN_GROUP
        )
    ]

    def is_enabled(self, origin_summary, **kwargs):
        return origin_summary

    def process_codebase(self, codebase, origin_summary_threshold=None, **kwargs):
        root = codebase.get_resource(0)

        # TODO: Should we activate or deactivate certain summarization options based on
        # the attributes that are present in the codebase?
        if not hasattr(root, 'copyrights') or not hasattr(root, 'licenses'):
            # TODO: Raise warning(?) if these fields are not there
            return

        base_identifier_counts = Counter()

        collecters = [stat_summary, tag_package_files]
        for collecter in collecters:
            bics = collecter(codebase, base_identifier_counts, origin_summary_threshold=origin_summary_threshold)
            base_identifier_counts.update(bics)

        # Pick one summary for a Resource
        for resource in codebase.walk(topdown=True):
            resource_summaries = sorted(resource.extra_data.get('summaries', []))
            if not resource_summaries:
                continue
            for summary in resource_summaries:
                # TODO: Be smarter about this. Consider more criteria picking a summary.
                if 'package' in summary.type:
                    # Package data has higher precedence over other types
                    resource.extra_data['summary'] = summary
                    break
                elif ('license', 'holder',) in summary.type:
                    resource.extra_data['summary'] = summary
                    break
            resource.save(codebase)
            resource_summary = resource.extra_data['summary']
            codebase.attributes.summaries.append(resource_summary)
            resource_summary_identifier = resource_summary.identifier
            resource.summarized_to = resource_summary_identifier

            children = resource.children(codebase)
            if not children:
                continue
            for child in children:
                child.summarized_to = resource_summary_identifier


def stat_summary(codebase, base_identifier_counts=None, origin_summary_threshold=None, **kwargs):
    base_identifier_counts = base_identifier_counts or Counter()

    # Summarize origin clues to directory level and tag summarized Resources
    for resource in codebase.walk(topdown=False):
        # TODO: Consider facets for later

        if resource.is_file:
            continue

        children = resource.children(codebase)
        if not children:
            continue

        # Collect license expression and holders count for stat-based summarization
        origin_count = Counter()
        child_rids = []
        for child in children:
            if child.is_file:
                license_expression = combine_expressions(child.license_expressions)
                holders = tuple(h['value'] for h in child.holders)
                if not license_expression or not holders:
                    continue
                origin = holders, license_expression
                origin_count[origin] += 1
            else:
                # We are in a subdirectory
                child_origin_count = child.extra_data.get('origin_count', {})
                if not child_origin_count:
                    continue
                origin_count.update(child_origin_count)
            child_rids.append(child.rid)

        if origin_count:
            resource.extra_data['origin_count'] = origin_count
            resource.save(codebase)

            origin, top_count = origin_count.most_common(1)[0]
            # TODO: Check for contradictions when performing summarizations
            if is_majority(top_count, resource.files_count, origin_summary_threshold):
                holders, license_expression = origin
                resource.origin_summary['license_expression'] = license_expression
                resource.origin_summary['holders'] = holders
                resource.origin_summary['count'] = top_count
                codebase.save_resource(resource)

                holder = '\n'.join(holders)
                base_identifier = python_safe_name('{}_{}'.format(license_expression, holder))
                # Keep track of identifiers so we can enumerate them properly
                base_identifier_counts[base_identifier] += 1
                identifier = python_safe_name('{}_{}'.format(base_identifier, base_identifier_counts[base_identifier]))

                resource_summaries = resource.extra_data.get('summaries', [])
                resource_summaries.append(
                    Summary(
                        identifier=identifier,
                        license_expression=license_expression,
                        holders=holders,
                        type=['license', 'holder']
                    )
                )
                resource.extra_data['summaries'] = resource_summaries
                resource.save(codebase)

    return base_identifier_counts


def tag_package_files(codebase, base_identifier_counts=None, **kwargs):
    base_identifier_counts = base_identifier_counts or Counter()

    # Summarize origin clues to directory level and tag summarized Resources
    for resource in codebase.walk(topdown=False):
        # TODO: Consider facets for later
        if resource.is_file:
            continue

        resource_packages = resource.packages
        for package in resource_packages:
            license_expression = package['license_expression']
            identifier = python_safe_name(package['purl'])

            resource_summaries = resource.extra_data.get('summaries', [])
            resource_summaries.append(
                Summary(
                    identifier=identifier,
                    license_expression=license_expression,
                    type=['package']
                )
            )
            resource.extra_data['summaries'] = resource_summaries
            resource.save(codebase)

    return base_identifier_counts


def tag_nr_files(codebase, **kwargs):
    # TODO: Load set of extensions to NR from somewhere
    nr_exts = []
    # Summarize origin clues to directory level and tag summarized Resources
    for resource in codebase.walk(topdown=False):
        if resource.extension in nr_exts:
            resource.extra_data['NR'] = True
            resource.save(codebase)


def is_majority(count, files_count, threshold=None):
    """
    Return True if `count` divided by `files_count` is greater than or equal to `threshold`
    """
    # TODO: Increase this and test with real codebases
    threshold = threshold or 0.75
    return count / files_count >= threshold
