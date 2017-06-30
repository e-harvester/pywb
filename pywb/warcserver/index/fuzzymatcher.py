from warcio.utils import to_native_str
from pywb.utils.loaders import load_yaml_config

import re
import os

from six import iterkeys
from six.moves.urllib.parse import urlsplit
from collections import namedtuple


# ============================================================================
FuzzyRule = namedtuple('FuzzyRule',
                       'url_prefix, regex, replace_after, filter_str, ' +
                       'match_type, match_filters')


# ============================================================================
class FuzzyMatcher(object):
    DEFAULT_FILTER = ['~urlkey:{0}']
    DEFAULT_MATCH_TYPE = 'prefix'
    DEFAULT_REPLACE_AFTER = '?'

    FUZZY_SKIP_PARAMS = ('alt_url', 'reverse', 'closest', 'end_key',
                         'url', 'matchType', 'filter')

    def __init__(self, filename):
        config = load_yaml_config(filename)
        self.rules = []
        for rule in config.get('rules'):
            rule = self.parse_fuzzy_rule(rule)
            if rule:
                self.rules.append(rule)

    def parse_fuzzy_rule(self, rule):
        """ Parse rules using all the different supported forms
        """
        url_prefix = rule.get('url_prefix')
        config = rule.get('fuzzy_lookup')
        if not config:
            return

        if not isinstance(url_prefix, list):
            url_prefix = [url_prefix]

        if not isinstance(config, dict):
            regex = self.make_regex(config)
            replace_after = self.DEFAULT_REPLACE_AFTER
            filter_str = self.DEFAULT_FILTER
            match_type = self.DEFAULT_MATCH_TYPE
            match_filters = None

        else:
            regex = self.make_regex(config.get('match'))
            replace_after = config.get('replace', self.DEFAULT_REPLACE_AFTER)
            filter_str = config.get('filter', self.DEFAULT_FILTER)
            match_type = config.get('type', self.DEFAULT_MATCH_TYPE)
            match_filters = self._init_match_filters(config.get('match_filters'))

        return FuzzyRule(url_prefix, regex, replace_after, filter_str,
                         match_type, match_filters)

    def _init_match_filters(self, filter_config):
        if not filter_config:
            return

        filters = []
        for filter_ in filter_config:
            filter_['match'] = re.compile(filter_['match'])
            filters.append(filter_)

        return filters

    def get_fuzzy_match(self, params):
        urlkey = to_native_str(params['key'], 'utf-8')

        filters = []
        matched_rule = None

        for rule in self.rules:
            if not any((urlkey.startswith(prefix) for prefix in rule.url_prefix)):
                continue

            m = rule.regex.search(urlkey)
            if not m:
                continue

            matched_rule = rule
            for g in m.groups():
                for f in matched_rule.filter_str:
                    filters.append(f.format(g))

            break

        if not matched_rule:
            return None

        url = params['url']

        inx = url.find(matched_rule.replace_after)
        if inx > 0:
            url = url[:inx + len(matched_rule.replace_after)]

        if matched_rule.match_type == 'domain':
            host = urlsplit(url).netloc
            url = host.split('.', 1)[1]

        fuzzy_params = {'url': url,
                        'matchType': matched_rule.match_type,
                        'filter': filters,
                        'is_fuzzy': True}

        for key in iterkeys(params):
            if key not in self.FUZZY_SKIP_PARAMS:
                fuzzy_params[key] = params[key]

        return matched_rule, fuzzy_params

    def make_regex(self, config):
        if isinstance(config, list):
            string = self.make_query_match_regex(config)

        elif isinstance(config, dict):
            string = config.get('regex', '')
            string += self.make_query_match_regex(config.get('args', []))

        else:
            string = str(config)

        return re.compile(string)

    def make_query_match_regex(self, params_list):
        params_list.sort()

        def conv(value):
            return '[?&]({0}=[^&]+)'.format(re.escape(value))

        return '.*'.join([conv(param) for param in params_list])

    def __call__(self, index_source, params):
        cdx_iter, errs = index_source(params)
        return self.get_fuzzy_iter(cdx_iter, index_source, params), errs

    def get_fuzzy_iter(self, cdx_iter, index_source, params):
        found = False
        for cdx in cdx_iter:
            found = True
            yield cdx

        if found:
            return

        url = params['url']

        res = self.get_fuzzy_match(params)
        if not res:
            return

        rule, fuzzy_params = res

        new_iter, errs = index_source(fuzzy_params)

        for cdx in new_iter:
            if self.allow_fuzzy_result(rule, url, cdx):
                cdx['is_fuzzy'] = True
                yield cdx

    def allow_fuzzy_result(self, rule, url, cdx):
        if not rule.match_filters:
            return True

        mime = cdx.get('mime')
        if not mime:
            return False

        for match_filter in rule.match_filters:
            not_ext = match_filter.get('not_ext')
            if not_ext:
                ext = self.get_ext(url)
                if not ext or ext in not_ext:
                    continue

            elif match_filter.get('mime', '--') not in (mime, '*'):
                continue

            return match_filter['match'].search(url)

        return False

    def get_ext(self, url):
        # check last path segment
        # if contains '.', likely a file, so fuzzy match!
        last_path = url.split('?', 1)[0].rsplit('/', 1)[-1]
        return os.path.splitext(last_path)[1][1:]
