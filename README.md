SearchMailman
=============
A GPLv2 python script for combing through mailman archives for specific content.

Features:
* Offline search support (requires at least one successful search online)
* Optionally specify a unix mbox-style mailbox to accumulate results
* Expresive match-filter framework

Installation
============
This software may be installed in your PATH or in your python path.

The following packages are required (NOTE: some of these are included with any
python installation):

* gzip
* urllib2
* StringIO
* re
* sys
* mailbox
* os
* datetime
* getopt

Usage
=====
The following Environment variables may be set to alter the behavior of the
SearchMailman program:

* SMA_CACHE_LOCATION
Path which specifies where cached entries should be placed. If not set, the
cached files will be placed in ~/.sma_cache/

* SMA_ARCHIVE_URL 
Specifies a prefix which must match http://somelink.com/mailman/listinfo and
will be replaced with the standard archive path. *NOTE*: This option may change
in the future, it is just an experiment at the moment.

The following options are understood by SearchMailman:

* -c
Clears the cache for the specified URL

* -o path_to_mbox
Outputs matching mails into the mailbox found at path_to_mbox. If one does
not exist, it will be created

* -u
Reconstruct possible matching URLs. This option requires internet access to
be successful.


Filters
=======

Filters are specified in the generic form FIELD OPERATION [VALUE]. The
filter will extract message fields by name (which is the FIELD above),
and run the OPERATION against that field. If OPERATION requires data
the VALUE of that data must be specified after. Optionally, the 'not' or
the '!' value can be used to specify the negative.

Valid OPERATIONS are:
is, equals, ==            Match the FIELD against VALUE exactly
contains, ~=              Match the FIELD against VALUE weakly, or as a
                          pythonic regular expression
present, available        Match whether the FIELD exists

Filters can appear sequentially, and are by default joined together as
LOGICAL AND filters. Support for LOGICAL OR is provided with either the 'or'
or '|' values. Likewise, LOGICAL AND support can be re-enabled using the
'and' or '&' values. These behave as polish-notation operators, so they
apply to every filter specified AFTER their appearance.

ex:

SearchMailman.py http://myarch/pipermail/dev body contains "user:" or \
    from contains "[pP]hil" from contains "[aA]ndy" and \
    subject is "setting user mode"

Translates to a filter that looks like:

IF body.contains("user:")
  AND (from.contains('[pP]hil') OR from.contains('[aA]ndy'))
  AND subject == "setting user mode" => THEN MATCH

