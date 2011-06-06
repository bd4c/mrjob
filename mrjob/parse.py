# Copyright 2009-2010 Yelp
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Utilities for parsing errors, counters, and status messages."""
from optparse import OptionValueError
import re

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

# match the filename of a hadoop streaming jar
HADOOP_STREAMING_JAR_RE = re.compile(r'^hadoop.*streaming.*\.jar$')

# match an mrjob job name (these are used to name EMR job flows)
JOB_NAME_RE = re.compile(r'^(.*)\.(.*)\.(\d+)\.(\d+)\.(\d+)$')

# match a job output line containing counter data
COUNTER_LINE_RE = re.compile(r'Job \w+=".*?"(\s+\w+=".*?")+\s+COUNTERS="(?P<counters>.+?)"') 
COUNTER_RE = re.compile(r'(?P<group>.+?)[.](?P<name>.+?):(?P<value>\d+)')

def find_python_traceback(lines):
    """Scan a log file or other iterable for a Python traceback,
    and return it as a list of lines.

    In logs from EMR, we find python tracebacks in ``task-attempts/*/stderr``
    """
    for line in lines:
        if line.startswith('Traceback (most recent call last):'):
            tb_lines = []
            for line in lines:
                tb_lines.append(line)
                if not line.startswith(' '):
                    break
            return tb_lines
    else:
        return None

def find_hadoop_java_stack_trace(lines):
    """Scan a log file or other iterable for a java stack trace from Hadoop,
    and return it as a list of lines.

    In logs from EMR, we find java stack traces in ``task-attempts/*/syslog``

    Sample stack trace::

        2010-07-27 18:25:48,397 WARN org.apache.hadoop.mapred.TaskTracker (main): Error running child
        java.lang.OutOfMemoryError: Java heap space
                at org.apache.hadoop.mapred.IFile$Reader.readNextBlock(IFile.java:270)
                at org.apache.hadoop.mapred.IFile$Reader.next(IFile.java:332)
                at org.apache.hadoop.mapred.Merger$Segment.next(Merger.java:147)
                at org.apache.hadoop.mapred.Merger$MergeQueue.adjustPriorityQueue(Merger.java:238)
                at org.apache.hadoop.mapred.Merger$MergeQueue.next(Merger.java:255)
                at org.apache.hadoop.mapred.Merger.writeFile(Merger.java:86)
                at org.apache.hadoop.mapred.Merger$MergeQueue.merge(Merger.java:377)
                at org.apache.hadoop.mapred.Merger.merge(Merger.java:58)
                at org.apache.hadoop.mapred.ReduceTask.run(ReduceTask.java:277)
                at org.apache.hadoop.mapred.TaskTracker$Child.main(TaskTracker.java:2216)

    (We omit the "Error running child" line from the results)
    """
    for line in lines:
        if line.rstrip('\n').endswith("Error running child"):
            st_lines = []
            for line in lines:
                st_lines.append(line)
                for line in lines:
                    if not line.startswith('        at '):
                        break
                    st_lines.append(line)
                return st_lines
    else:
        return None

_OPENING_FOR_READING_RE = re.compile("^.*: Opening '(.*)' for reading$")

def find_input_uri_for_mapper(lines):
    """Scan a log file or other iterable for the path of an input file
    for the first mapper on Hadoop. Just returns the path, or None if
    no match.

    In logs from EMR, we find python tracebacks in ``task-attempts/*/syslog``

    Matching log lines look like::

        2010-07-27 17:54:54,344 INFO org.apache.hadoop.fs.s3native.NativeS3FileSystem (main): Opening 's3://yourbucket/logs/2010/07/23/log2-00077.gz' for reading
    """
    for line in lines:
        match = _OPENING_FOR_READING_RE.match(line)
        if match:
            return match.group(1)
    else:
        return None

_HADOOP_STREAMING_ERROR_RE = re.compile(r'^.*ERROR org\.apache\.hadoop\.streaming\.StreamJob \(main\): (.*)$')

def find_interesting_hadoop_streaming_error(lines):
    """Scan a log file or other iterable for a hadoop streaming error
    other than "Job not Successful!". Return the error as a string, or None
    if nothing found.

    In logs from EMR, we find java stack traces in ``steps/*/syslog``

    Example line::

        2010-07-27 19:53:35,451 ERROR org.apache.hadoop.streaming.StreamJob (main): Error launching job , Output path already exists : Output directory s3://yourbucket/logs/2010/07/23/ already exists and is not empty
    """
    for line in lines:
        match = _HADOOP_STREAMING_ERROR_RE.match(line)
        if match:
            msg = match.group(1)
            if msg != 'Job not Successful!':
                return msg
    else:
        return None

_TIMEOUT_ERROR_RE = re.compile(r'Task.*?TASK_STATUS="FAILED".*?ERROR=".*?failed to report status for (\d+) seconds. Killing!"')

def find_timeout_error(lines):
    """Scan a log file or other iterable for a timeout error from Hadoop.
    Return the number of seconds the job ran for before timing out, or None if
    nothing found.

    In logs from EMR, we find timeouterrors in ``jobs/*.jar``

    Example line::

        Task TASKID="task_201010202309_0001_m_000153" TASK_TYPE="MAP" TASK_STATUS="FAILED" FINISH_TIME="1287618918658" ERROR="Task attempt_201010202309_0001_m_000153_3 failed to report status for 602 seconds. Killing!"
    """
    result = None
    for line in lines:
        match = _TIMEOUT_ERROR_RE.match(line)
        if match:
            result = match.group(1)
    return int(result)

# recognize hadoop streaming output
_COUNTER_RE = re.compile(r'reporter:counter:([^,]*),([^,]*),(-?\d+)$')
_STATUS_RE = re.compile(r'reporter:status:(.*)$')

def parse_mr_job_stderr(stderr, counters=None, step_num=0):
    """Parse counters and status messages out of MRJob output.

    :param data: a filehandle, a list of lines, or a str containing data
    :type counters: Counters so far, to update; a map from group to counter name to count.

    Returns a dictionary with the keys *counters*, *statuses*, *other*:

    - *counters*: counters so far; same format as above
    - *statuses*: a list of status messages encountered
    - *other*: lines that aren't either counters or status messages
    """
    # For the corresponding code in Hadoop Streaming, see ``incrCounter()`` in
    # http://svn.apache.org/viewvc/hadoop/mapreduce/trunk/src/contrib/streaming/src/java/org/apache/hadoop/streaming/PipeMapRed.java?view=markup
    if isinstance(stderr, str):
        stderr = StringIO(stderr)

    if counters is None:
        counters = {}
    statuses = []
    other = []

    for line in stderr:
        m = _COUNTER_RE.match(line)
        if m:
            group, counter, amount_str = m.groups()
            counters.setdefault(step_num, {})           # steps
            counters[step_num].setdefault(group, {})    # groups
            counters[step_num][group].setdefault(counter, 0)
            counters[step_num][group][counter] += int(amount_str)
            continue

        m = _STATUS_RE.match(line)
        if m:
            statuses.append(m.group(1))
            continue

        other.append(line)

    return {'counters': counters, 'statuses': statuses, 'other': other}

def parse_hadoop_counters_from_line(line, step_num=0, counters=None):
    """Parse Hadoop counter values from a log line.

    :param line: log line containing counter data
    :type line: str

    Example line: Job JOBID="job_201106061823_0001" FINISH_TIME="1307384737542" JOB_STATUS="SUCCESS" FINISHED_MAPS="2" FINISHED_REDUCES="1" FAILED_MAPS="0" FAILED_REDUCES="0" COUNTERS="File Systems.S3N bytes read:3726,File Systems.Local bytes read:4164,File Systems.S3N bytes written:1663,File Systems.Local bytes written:8410,Job Counters .Launched reduce tasks:1,Job Counters .Rack-local map tasks:2,Job Counters .Launched map tasks:2,Map-Reduce Framework.Reduce input groups:154,Map-Reduce Framework.Combine output records:0,Map-Reduce Framework.Map input records:68,Map-Reduce Framework.Reduce output records:154,Map-Reduce Framework.Map output bytes:3446,Map-Reduce Framework.Map input bytes:2483,Map-Reduce Framework.Map output records:336,Map-Reduce Framework.Combine input records:0,Map-Reduce Framework.Reduce input records:336,profile.reducer step 0 estimated IO time: 0.00:1,profile.mapper step 0 estimated IO time: 0.00:2,profile.reducer step 0 estimated CPU time: 0.00:1,profile.mapper step 0 estimated CPU time: 0.00:2"
From file named: (log_uri)/jobs/ip-10-168-73-57.us-west-1.compute.internal_1307384628708_job_201106061823_0001_hadoop_streamjob5884999361405044030.jar
    """
    counters = counters or {}

    m = COUNTER_LINE_RE.match(line)
    if not m:
        return counters
    
    counters = {}
    for counter_line in m.group('counters').split(','):
        counter_re = COUNTER_RE.match(counter_line)
        group, name, amount_str = counter_re.groups()

        counters.setdefault(step_num, {})
        counters[step_num].setdefault(group, {})    # groups
        counters[step_num][group].setdefault(name, 0)
        counters[step_num][group][name] += int(amount_str)
    
    return counters


def parse_port_range_list(range_list_str):
    all_ranges = []
    for range_str in range_list_str.split(','):
        if ':' in range_str:
            a, b = [int(x) for x in range_str.split(':')]
            all_ranges.extend(range(a, b+1))
        else:
            all_ranges.append(int(range_str))
    return all_ranges


def check_kv_pair(option, opt, value):
    items = value.split('=', 1)
    if len(items) == 2:
        return items 
    else:
        raise OptionValueError(
            "option %s: value is not of the form KEY=VALUE: %r" % (opt, value))


def check_range_list(option, opt, value):
    try:
        ports = parse_port_range_list(value)
        return ports
    except ValueError, e:
        raise OptionValueError('option %s: invalid port range list "%s": \n%s' % (opt, value, e.args[0]))

