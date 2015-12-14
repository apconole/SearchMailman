#!/usr/bin/env python
#
# Copyright (C) 2015, Red Hat Inc.
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
import urllib2
import re
import sys
import mailbox
import os
import datetime
import getopt

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
    HOMEDIR=os.path.expanduser('~')
    fs_converted_url = re.sub('[/:@]*', '_', url)
    if not os.path.exists(HOMEDIR + '/.sma_cache'):
        os.makedirs(HOMEDIR + '/.sma_cache')
    return HOMEDIR + '/.sma_cache/' + fs_converted_url
    
def cached_url_open(url):
    fs_converted_url = cached_url_filename(url)
    if os.path.exists(fs_converted_url):
        request = urllib2.Request(url)
        request.get_method = lambda : 'HEAD'
        try:
            response = urllib2.urlopen(request)
            headers = response.info()
            webdate = webdatetime(headers['last-modified'])
        except:
            webdate = datetime.datetime.fromtimestamp(0.0)
        filedate = datetime.datetime.fromtimestamp(os.path.getmtime(fs_converted_url))
        if filedate >= webdate:
            fileop = open(fs_converted_url, 'r')
            return fileop.read()
        
    response = urllib2.urlopen(url)
    result = response.read()

    fileop = open(fs_converted_url, 'w')
    fileop.write(result)
    
    return result

def mailman_archives(MailmanUrl):
    html = cached_url_open(MailmanUrl)
    grp = re.findall('href="[^"]+.txt.gz"', html)

    archives = []
    
    for archive in grp:
        archive1 = re.sub(r'href=', '', archive)
        app = re.sub(r'"', '', archive1)
        archives.append(app)

    return archives

def get_mailman_mailbox_from_archive(ArchiveUrl):
    # print "Scanning %s" % ArchiveUrl
    zipped = cached_url_open(ArchiveUrl)
    unzipped = gzip.GzipFile(fileobj=StringIO.StringIO(zipped))

    return streammedMbox(unzipped.read())

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
        elif self._match_regex:
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

# TODO: need to write a proper date matching entity
class date_filter(match_filter):
    BEFORE_DATE = 0
    AFTER_DATE = 1
    def __init__(self, dateToMatchStr):
        self._match_regex = False
        
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

def make_filters(argslist):
    part = None
    op = None
    valu = None

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
            part = None
            op = None
            valu = None
        elif string_match_in_list(op, ["==", "equals", "is"]):
            if valu is not None:
                if not negateFlag:
                    current_filter_list.push_filter(match_filter(part, match_filter.REQUIRED_MATCH, valu))
                else:
                    current_filter_list.push_filter(match_filter(part, match_filter.REQUIRED_NOT_MATCH, valu))
                op = None
                part = None
                valu = None
            else:
                getVal = True
                continue
        elif string_match_in_list(op, ["contains", "~="]):
            if valu is not None:
                if not negateFlag:
                    current_filter_list.push_filter(match_filter(part, match_filter.NOT_REQUIRED_EXACT_MATCH, valu))
                else:
                    current_filter_list.push_filter(match_filter(part, match_filter.REQUIRED_NOT_MATCH, valu))
                op = None
                part = None
                valu = None
            else:
                getVal = True
                continue
        else:
            print "Unknown op: %s" % (op)
            sys.exit(1)

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
    print " -c                        Clear archive cache instead of search"
    print " -o [PATH]                 Save off matches to the path specified"
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

if __name__ == "__main__":

    mbx = None

    try:
        optlist, args = getopt.getopt(sys.argv[1:], 'o:ch')
    except:
        print "Failed to getopt:"
        print args
        sys.exit(1)

    bClear = False
        
    for o,a in optlist:
        if o == '-o':
            mbx = mailbox.mbox(a)
        elif o == '-c':
            bClear = True
        elif o == '-h':
            usage()
            sys.exit(0)

    bFound = False
    filters = None
    for arch in mailman_archives(args[0]):
        mailarch_url = args[0] + arch
        if not bClear:
            if filters is None:
                filters = make_filters(args[1:])
            newmsgs = mbox_messages_matching(mailarch_url, filters)
            for message in newmsgs:
                bFound = True
                if mbx is not None: mbx.add(message)
                print "%s (%s) %s" % (message['from'], message['subject'], message['date'])
        else:
            delfile = cached_url_filename(mailarch_url)
            print "Removing [%s]" % delfile
            os.remove(delfile)
            
    if bFound:
        sys.exit(0)
    sys.exit(1)
