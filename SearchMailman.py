#!/usr/bin/env python
#
# Copyright (C) 2015-2017, Red Hat Inc.
#
# Search a mailman archive and extract all emails which match the filters
# specified, storing the results in an mbox-style file, appending if one
# already exists).
#
# Options:
#  -o output
#
# filters are available as and, or filters such as:
# and body contains foo or from contains phil from contains paul
# that converts to
# if message['body'].contains('foo') && (message['from'].contains('phil') ||
#                                        message['from'].contains('paul') ):
#    return MATCHED
#
# The symbols "and", "&" mean logical AND
# The symbols "or", "|" mean logical OR
# The symbols "not", "!" means negate the current filter
# The symbols "equals", "eq", "is", "==" mean MATCH EXACT
# The symbols "contains", "~=" mean MATCH PARTIAL/REGEX (either will suffice)
# The symbols "present", "available" check whether the specific entry exists
# Date based filtering will be forthcoming

import gzip
import StringIO
import urllib
import urllib2
import re
import sys
import mailbox
import os
import datetime
import time
import getopt
import ssl
import email.utils
import timestring
import subprocess

__patch_id = re.compile(r'^\[.*PATCH.* (?P<patch_num>[0-9]+)/([0-9]+).*] (?P<patch_subj>.*)')

accept_all_certs = False
login_user = None
login_pass = None
opener = None

class streammedMbox(mailbox.mbox):
    def __init__(self, stringOfBytes):
        self._file = StringIO.StringIO(stringOfBytes)
        self._toc = None
        self._next_key = 0
        self._pending = False
        self._locked = False
        self._file_length = None
        self._factory = None
        self._path='/dev/null'
        self._message_factory = mailbox.mboxMessage

def webdatetime(txt):
    if txt is None or txt == '':
        return datetime.now()
    
    try:
        txt_split = txt.split(" ")
        txt_split = txt_split[:-1]
        checktxt = ' '.join(txt_split)
        return datetime.datetime.strptime(checktxt, '%a, %d %b %Y %H:%M:%S')
    except ValueError, v:
        print "Error converting %s" % txt
        raise v

def cached_url_filename(url):
    fs_converted_url = re.sub('[/:@]+', '_', url)

    if os.getenv("SMA_CACHE_LOCATION"):
        path_to_fs = os.getenv("SMA_CACHE_LOCATION")
    else:
        path_to_fs = os.path.expanduser('~') + '/.sma_cache'
        
    if not os.path.exists(path_to_fs):
        os.makedirs(path_to_fs)
    return path_to_fs + '/' + fs_converted_url

def url_open_resp(url):
    global opener, login_user, login_pass
    if not opener and login_user:
        if login_user:
            opener = urllib2.build_opener(urllib2.HTTPCookieProcessor())
        else:
            opener = urllib2.build_opener()
        urllib2.install_opener(opener)

    params=None
    if login_user:
        params = urllib.urlencode(dict(username=login_user,
                                       password=login_pass))

    if not accept_all_certs:
        response = urllib2.urlopen(url, data=params)
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        response = urllib2.urlopen(url, context=ctx)

    return response

def url_open(url):
    response = url_open_resp(url)
    return response.read()

def cached_url_open(url, is_zipped=False):
    fs_converted_url = cached_url_filename(url)
    if os.path.exists(fs_converted_url):
        request = urllib2.Request(url)
        request.get_method = lambda : 'HEAD'
        try:
            response = url_open_resp(request)
            headers = response.info()
            webdate = webdatetime(headers['last-modified'])
        except:
            webdate = datetime.datetime.fromtimestamp(0.0)
        filedate = datetime.datetime.fromtimestamp(os.path.getmtime(fs_converted_url))
        if filedate >= webdate:
            fileop = open(fs_converted_url, 'r')
            return fileop.read()

    result = url_open(url)

    if is_zipped:
        zipdata = gzip.GzipFile(fileobj=StringIO.StringIO(result))
        result = zipdata.read()

    fileop = open(fs_converted_url, 'w')
    fileop.write(result)

    return result

def mailman_archives(MailmanUrl):
    try:
        html = cached_url_open(MailmanUrl)
    except:
        print "Unable to open [%s]" % MailmanUrl
        return []
    
    grp = re.findall('href="[^"]+.txt.gz"', html)

    archives = []

    for archive in grp:
        archive1 = re.sub(r'href=', '', archive)
        app = re.sub(r'"', '', archive1)
        archives.append(app)

    return archives

def get_mailman_mailbox_from_archive(ArchiveUrl):
    # print "Scanning %s" % ArchiveUrl
    try:
        unzipped = cached_url_open(ArchiveUrl, True)
    except:
        print "Unable to open mailbox [%s]" % ArchiveUrl
        return None
    #unzipped = gzip.GzipFile(fileobj=StringIO.StringIO(zipped))

    return streammedMbox(unzipped)

class match_filter(object):
    REQUIRED_MATCH=0
    REQUIRED_NOT_MATCH = 1
    NOT_REQUIRED_EXACT_MATCH = 2

    def __init__(self, mail_section, match_type=REQUIRED_MATCH, match_data=''):
        self._mail_section = mail_section
        self._match_type = match_type
        self._match_data = match_data
        self._match_regex = False
        if match_type != match_filter.REQUIRED_MATCH:
            self._match_regex = True

    MATCH_TYPE_EXACT=0
    MATCH_TYPE_PARTIAL = 1
    MATCH_TYPE_UNMATCHED = 2
    MATCH_TYPE_REGEX = 3

    def length(self):
        return 0

    def part_match(self, part_text):
        matching_type = None

        if part_text is not None and part_text == self._match_data:
            matching_type = match_filter.MATCH_TYPE_EXACT
        elif self._match_regex and self._match_data is not None and part_text is not None:
            result = re.findall(self._match_data, part_text)
            if result is not None and len(result) > 0:
                matching_type = match_filter.MATCH_TYPE_REGEX

        if part_text is not None and matching_type is None and self._match_data in part_text:
            matching_type = match_filter.MATCH_TYPE_PARTIAL

        if self._match_type == match_filter.REQUIRED_MATCH:
            if matching_type != match_filter.MATCH_TYPE_EXACT:
                matching_type = None

        if matching_type is None:
            matching_type = match_filter.MATCH_TYPE_UNMATCHED

        if self._match_type == match_filter.REQUIRED_NOT_MATCH:
            if matching_type is match_filter.MATCH_TYPE_UNMATCHED:
                return match_filter.MATCH_TYPE_EXACT

        return matching_type

    def does_match(self, message, recur = False):
        if recur or self._mail_section == 'body':
            if message.is_multipart():
                for part in message.get_payload():
                    return self.does_match(part, True)
            else:
                return self.part_match(message.get_payload())
        else:
            return self.part_match(message[self._mail_section])

class date_filter(match_filter):
    BEFORE_DATE = 0
    AFTER_DATE = 1
    def __init__(self, dateToMatchStr, before):
        self._match_regex = False
        self._match_data = timestring.Date(dateToMatchStr).to_unixtime()
        self._mail_section = 'Date'
        self._before = before

    def part_match(self, date_string):
        date_to_check = email.utils.mktime_tz(email.utils.parsedate_tz(date_string))
        if self._before:
            if self._match_data > date_to_check:
                return match_filter.MATCH_TYPE_EXACT
        else:
            if self._match_data <= date_to_check:
                return match_filter.MATCH_TYPE_EXACT

        return match_filter.MATCH_TYPE_UNMATCHED

class and_filter(match_filter):
    def __init__(self, filter_list):
        self._filters = filter_list

    def push_filter(self, mfilter):
        self._filters.append(mfilter)

    def length(self):
        return len(self._filters)

    def part_match(self, part_text):
        for mfilter in self._filters:
            if mfilter.does_match(part_text) == match_filter.MATCH_TYPE_UNMATCHED:
                return match_filter.MATCH_TYPE_UNMATCHED

        return match_filter.MATCH_TYPE_EXACT

    def does_match(self, message):
        return self.part_match(message)

class threaded_and_filter(and_filter):
    def __init__(self, filter_list):
        self._filters = filter_list
        self._replyto_ids = []

    def does_match(self, message):
        result = self.part_match(message)
        inreplyto = message['In-Reply-To']
        if inreplyto is None:
            return result
        if result is match_filter.MATCH_TYPE_UNMATCHED:
            if inreplyto in self._replyto_ids:
                result = match_filter.MATCH_TYPE_PARTIAL
                self._replyto_ids.append(inreplyto)
                print "added in-reply-to %s" % inreplyto
        else:
            self._replyto_ids.append(inreplyto)
            print "added in-reply-to %s" % inreplyto
        return result

class or_filter(match_filter):
    def __init__(self, filter_list):
        self._filters = filter_list

    def push_filter(self, mfilter):
        self._filters.append(mfilter)

    def length(self):
        return len(self._filters)

    def part_match(self, part_text):
        for mfilter in self._filters:
            if mfilter.does_match(part_text) != match_filter.MATCH_TYPE_UNMATCHED:
                return match_filter.MATCH_TYPE_EXACT

        return match_filter.MATCH_TYPE_UNMATCHED

    def does_match(self, message):
        return self.part_match(message)

def mbox_messages_matching(ArchiveUrl, TopFilter):
    mbx = get_mailman_mailbox_from_archive(ArchiveUrl)
    matchingMsgs = []
    for msgkey in mbx.iterkeys():
        message = mbx[msgkey]
        matched = TopFilter.does_match(message) != match_filter.MATCH_TYPE_UNMATCHED
        if matched:
            matchingMsgs.append(message)

    return matchingMsgs

def string_match_in_list(string, lst):
    return any(string in item for item in lst)

def make_filters(argslist, threaded_search=False):
    part = None
    op = None
    valu = None

    if threaded_search:
        return_filter = threaded_and_filter([])
    else:
        return_filter = and_filter([])

    current_filter_list = and_filter([])

    getVal = False
    negateFlag = False

    for arg in argslist:
        if string_match_in_list(arg, ["not" "!"]):
            negateFlag = True
            continue

        if part is None:
            part = arg

            if string_match_in_list(part, ["and" "or" "&" "|"]):
                return_filter.push_filter(current_filter_list)
                if part in ["and" "&"]:
                    current_filter_list = and_filter([])
                else:
                    current_filter_list = or_filter([])
                    part = None
            elif string_match_in_list(part, ["before", "earlier", "after", "since"]):
                op = part
                getVal = True
            continue

        if op is None:
            op = arg

        if getVal:
            valu = arg
            getVal = False

        if string_match_in_list(op, ["present" "available"]):
            if not negateFlag:
                current_filter_list.push_filter(match_filter(part, match_filter.NOT_REQUIRED_EXACT_MATCH, '.*'))
            else:
                current_filter_list.push_filter(match_filter(part, match_filter.REQUIRED_NOT_MATCH, '.*'))
        elif string_match_in_list(op, ["==", "equals", "is"]):
            if valu is not None:
                if not negateFlag:
                    current_filter_list.push_filter(match_filter(part, match_filter.REQUIRED_MATCH, valu))
                else:
                    current_filter_list.push_filter(match_filter(part, match_filter.REQUIRED_NOT_MATCH, valu))
            else:
                getVal = True
                continue
        elif string_match_in_list(op, ["contains", "~="]):
            if valu is not None:
                if not negateFlag:
                    current_filter_list.push_filter(match_filter(part, match_filter.NOT_REQUIRED_EXACT_MATCH, valu))
                else:
                    current_filter_list.push_filter(match_filter(part, match_filter.REQUIRED_NOT_MATCH, valu))
            else:
                getVal = True
                continue
        elif string_match_in_list(op, ["before", "earlier", "after", "since"]):
            isBefore = True
            if string_match_in_list(op, ["after", "since"]):
                isBefore = False
            if negateFlag:
                isBefore = not isBefore

            if valu is not None:
                current_filter_list.push_filter(date_filter(valu, isBefore))
            else:
                getVal = True
                continue
        else:
            print "Unknown op: %s" % (op)
            sys.exit(1)

        part = None
        op = None
        valu = None

    if current_filter_list.length():
        return_filter.push_filter(current_filter_list)

    if return_filter.length() == 0:
        print "Error: Must have at least one filter"
        sys.exit(1)

    return return_filter

def usage():
    print "Usage: %s [OPTIONS] ARCHIVE FILTER..." % sys.argv[0]
    print "Search mailman archives"
    print "Entries are reported in time descending order (most recent first)"
    print ""
    print "Options:"
    print " -a                        Accept all SSL Certificates"
    print " -c                        Clear archive cache instead of search"
    print " -l [USER:PASSWORD]        Set login information"
    print " -o [PATH]                 Save off matches to the path specified"
    print " -u                        Seek the Mailman URL for this message (net only)"
    print " -t                        Threaded searching (tries to follow replies)"
    print " -h                        This help message"
    print ""
    print "Filter:"
    print "Filters are specified in the generic form FIELD OPERATION [VALUE]. The"
    print "filter will extract message fields by name (which is the FIELD above),"
    print "and run the OPERATION against that field. If OPERATION requires data"
    print "the VALUE of that data must be specified after. Optionally, the 'not' or"
    print "the '!' value can be used to specify the negative."
    print ""
    print "Valid OPERATIONS are:"
    print "is, equals, ==            Match the FIELD against VALUE exactly"
    print "contains, ~=              Match the FIELD against VALUE weakly, or as a"
    print "                          pythonic regular expression"
    print "present, available        Match whether the FIELD exists"
    print ""
    print "Filters can appear sequentially, and are by default joined together as"
    print "LOGICAL AND filters. Support for LOGICAL OR is provided with either the 'or'"
    print "or '|' values. Likewise, LOGICAL AND support can be re-enabled using the"
    print "'and' or '&' values. These behave as polish-notation operators, so they"
    print "apply to every filter specified AFTER their appearance."

def conv_subj(subject, match):
    if match:
        real_subj = match.group('patch_subj')
    else:
        real_subj = subject
    return real_subj.replace('/', '_').replace(' ', '_').replace('\\','_').replace('*', '_')

if __name__ == "__main__":

    mbx = None
    try:
        optlist, args = getopt.getopt(sys.argv[1:], 'l:o:e:achtu')
    except:
        print "Failed to getopt: %s" % (' '.join(sys.argv[1:]))
        sys.exit(1)

    clear_cached_files = False
    individual_files = False
    find_mailman_url = False
    threaded_search = False
    exec_arg = None

    for o,a in optlist:
        if o == '-o':
            if not os.path.isdir(a):
                mbx = mailbox.mbox(a)
            else:
                mbx_dir = a
                individual_files = True
        elif o == '-l':
            first_split = a.find(':')
            login_user = a[:first_split]
            if first_split != -1:
                login_pass = a[first_split+1:]
        elif o == '-a':
            accept_all_certs = True
        elif o == '-e':
            exec_arg = a
        elif o == '-c':
            clear_cached_files = True
        elif o == '-h':
            usage()
            sys.exit(0)
        elif o == '-t':
            threaded_search = True
        elif o == '-u':
            find_mailman_url = True


    if len(args) == 0:
        usage()
        sys.exit(1)

    if os.getenv('SMA_LOGIN_USER'):
        login_user = os.getenv('SMA_LOGIN_USER')
    if os.getenv('SMA_LOGIN_PASSWORD'):
        login_pass = os.getenv('SMA_LOGIN_PASS')
        
    found_message = False
    filters = None

    BaseUrl = args[0]

    if os.getenv('SMA_ARCHIVE_URL') and not (BaseUrl.find("http://") == 0 or BaseUrl.find("https://") == 0):
        MailMan, instances = re.subn("/mailman(/listinfo)?/?$", "/archives/", os.getenv('SMA_ARCHIVE_URL'))
        BaseUrl = MailMan + args[0] + "/"

    for arch in mailman_archives(BaseUrl):
        if find_mailman_url:
            try:
                archive_list = url_open(BaseUrl + arch.replace('.txt.gz', '/thread.html')).replace('\n', ' ').replace('\r', ' ')
            except:
                archive_list = None

        mailarch_url = BaseUrl + arch
        mailnum = 0
        thread_replies_is = []
        if not clear_cached_files:
            if filters is None:
                filters = make_filters(args[1:], threaded_search)
            newmsgs = mbox_messages_matching(mailarch_url, filters)
            for message in newmsgs:
                found_message = True
                subj = message['subject']
                if subj is None:
                    continue
                subj = subj.replace('\r', ' ').replace('\n', ' ').replace('\t', '')
                print "%s (%s) %s" % (message['from'], subj, message['date'])
                match = None
                if 'PATCH' in subj:
                    match = __patch_id.match(subj)
                    if match:
                        mailnum = int(match.group('patch_num'))
                if individual_files:
                    mbx = mailbox.mbox('%s/%04d-%s.mbox' %
                                       (mbx_dir, mailnum,
                                        conv_subj(subj, match)))
                if mbx is not None: mbx.add(message)
                if individual_files and mbx is not None:
                    mbx.close()
                    mbx = None
                if exec_arg:
                    p = subprocess.Popen(exec_arg, stdin=subprocess.PIPE)
                    p.communicate(input=str(message))
                    p.stdin.close()
                    p.wait()

                mailnum += 1
                if find_mailman_url and archive_list:
                    print " * Searching URLs at %s" % BaseUrl + arch.replace('.txt.gz', '/thread.html')

                    # first, try for mhonarc
                    subj_find = '<(a|A) (name|NAME)="([0-9]*)" (href|HREF)="(msg[0-9]*).html">' + subj.replace('[', '\\[').replace(']', '\\]') + "</(a|A)>(</(strong|STRONG)>),? ?(<(em|EM)>)?" + message['from'] + "(</(em|EM)>)? ?</?(ul|li|UL|LI)"
                    matches = re.finditer(subj_find, archive_list)
                    matches = list(matches)
                    if len(matches) == 0:
                        print " * Possibly pipermail"
                        subj_find = '<LI><A HREF="([0-9]*.html)">' + subj.replace('[', '\\[').replace(']', '\\]')
                        matches = re.finditer(subj_find, archive_list)
                        matches = list(matches)

                    for match in matches:
                        msgurl_match = re.search('[0-9]*.html', match.string[match.start():match.end()])
                        msgurl = msgurl_match.string[msgurl_match.start():msgurl_match.end()]
                        print " *** %s" % BaseUrl + arch.replace('.txt.gz', '/') + msgurl
                    print " * Done."
        else:
            delfile = cached_url_filename(mailarch_url)
            print "Removing [%s]" % delfile
            if os.path.exists(delfile):
                os.remove(delfile)

    if found_message:
        sys.exit(0)
    sys.exit(1)
