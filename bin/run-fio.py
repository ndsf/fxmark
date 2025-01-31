#!/usr/bin/env python3
import os
import sys
import signal
import subprocess
import datetime
import tempfile
import optparse
import time
import pdb
import re
from os.path import join

CUR_DIR = os.path.abspath(os.path.dirname(__file__))

class FIO(object):
    WORKLOAD_DIR = os.path.normpath(os.path.join(CUR_DIR, "fio-workloads"))
    PRE_SCRIPT = os.path.normpath(os.path.join(CUR_DIR, "turnoff-aslr"))
    PERF_STR = "WRITE: bw="

    def __init__(self, type_, ncore_, duration_, root_,
                 profbegin_, profend_, proflog_):
        def extract_blocksize(input_string):
            match = re.search(r'blocksize_(\d+)', input_string)
            if match:
                return int(match.group(1))
            else:
                return None
        def extract_filesize(input_string):
            match = re.search(r'filesize_(\d+)', input_string)
            if match:
                return int(match.group(1))
            else:
                return None
            
        self.config = None
        self.bench_out = None
        # take configuration parameters
        self.workload = type_
        self.ncore = 20 # int(ncore_)
        
        self.bs = extract_blocksize(self.workload)
        if self.bs is None:
            self.bs = 4096

        self.size = extract_filesize(self.workload) # in MB
        if self.size is None:
            self.size = 64

        self.rw = "randwrite"
        if "sequential" in self.workload:
            self.rw = "write"

        self.duration = int(duration_)

        self.ZIPF = not float(ncore_).is_integer()

        if self.ZIPF: # ncore_ is float, so we use it as zipf
            self.zipf = float(ncore_)
        else: # compatible with old version
            self.zipf = 1.2
            self.ncore = int(ncore_)
            
        self.root = root_
        self.profbegin = profbegin_
        self.profend = profend_
        self.proflog = proflog_
        self.profenv = ' '.join(["PERFMON_LEVEL=%s" %
                                 os.environ.get('PERFMON_LEVEL', "x"),
                                 "PERFMON_LDIR=%s"  %
                                 os.environ.get('PERFMON_LDIR',  "x"),
                                 "PERFMON_LFILE=%s" %
                                 os.environ.get('PERFMON_LFILE', "x")])
        self.perf_msg = None

        self.DEBUG_OUT     = False

    def __del__(self):
        # clean up
        try:
            if self.config:
                os.unlink(self.config.name)
            if self.bench_out:
                os.unlink(self.bench_out.name)
        except:
            pass

    def run(self):
        # set up benchmark configuration
        # run pre-script then sync
        self._exec_cmd("sudo %s; sync" % FIO.PRE_SCRIPT).wait()
        # start performance profiling
        self._exec_cmd("%s %s" % (self.profenv, self.profbegin)).wait()
        # run fio
        self._run_fio()
        # stop performance profiling
        self._exec_cmd("%s %s" % (self.profenv, self.profend)).wait()
        return 0

    def _run_fio(self):
        with tempfile.NamedTemporaryFile(delete=False) as self.bench_out: # --fsync=1
            zipf = True
            if not self.ZIPF or self.zipf == 0.1:
                zipf = False # 0.1 means random, just a symbol, not for zipf parameter
            if self.zipf == 1.2:
                self.zipf = 0.99

            if zipf:
                cmd = "sudo fio --name=rand_write_4k --ioengine=mmap --fdatasync=1 --rw=%s --random_distribution=zipf:%s --numjobs=%s --bs=%d --size=%dm --runtime=%s --ramp_time=30 --time_based=1 --gtod_reduce=1 --group_reporting=1 --directory=%s/" % (self.rw, self.zipf, self.ncore, self.bs, self.size, self.duration, self.root)
                if "sync" in self.workload:#--fsync=256--rw=randwrite --random_distribution=zipf:%s 
                    cmd = "sudo fio --name=rand_write_4k --ioengine=sync --rw=%s --random_distribution=zipf:%s --numjobs=%s --bs=%d --size=%dm --runtime=%s --ramp_time=30 --time_based=1 --gtod_reduce=1 --group_reporting=1 --directory=%s/" % (self.rw, self.zipf, self.ncore, self.bs, self.size, self.duration, self.root)
            else:# --ramp_time=120
                cmd = "sudo fio --name=rand_write_4k --ioengine=mmap --fdatasync=1 --rw=%s --numjobs=%s --bs=%d --size=%dm --runtime=%s --ramp_time=30 --time_based=1 --gtod_reduce=1 --group_reporting=1 --directory=%s/" % (self.rw, self.ncore, self.bs, self.size, self.duration, self.root)
                if "sync" in self.workload:#--fsync=256--rw=randwrite --random_distribution=zipf:%s 
                    cmd = "sudo fio --name=rand_write_4k --ioengine=sync --rw=%s --numjobs=%s --bs=%d --size=%dm --runtime=%s --ramp_time=30 --time_based=1 --gtod_reduce=1 --group_reporting=1 --directory=%s/" % (self.rw, self.ncore, self.bs, self.size, self.duration, self.root)
            p = self._exec_cmd(cmd, subprocess.PIPE)
            while True:
                for l in p.stdout.readlines():
                    if self.DEBUG_OUT:
                        print(l)

                    self.bench_out.write("#@ ".encode("utf-8"))
                    self.bench_out.write(l)
                    l_str = str(l)
                    idx = l_str.find(FIO.PERF_STR)
                    if idx is not -1:
                        self.perf_msg = l_str[idx+len(FIO.PERF_STR):]
                # if not p.poll():
                #    break
                if self.perf_msg:
                    break
            self.bench_out.flush()

    def report(self):
        # 65231: 31.114: IO Summary: 34453 ops, 1148.248 ops/s, (177/177 r/w),   4.0mb/s, 420us cpu/op,   5.4ms latency
        work = 0
        work_sec = 0 # runtime
        # for item in self.perf_msg.split(','):
        vk = self.perf_msg.split(',')[0].strip().split()

        if len(vk) == 2:
            if vk[0].endswith("MiB/s"):
                work_sec = vk[0].replace("MiB/s", "")
                work = str(float(work_sec) * float(self.duration))
                # work_sec = str(float(work) / float(self.duration))
            elif vk[0].endswith("GiB/s"):
                work_sec = str(float(vk[0].replace("GiB/s", "")) * 1024)
                work = str(float(work_sec) * float(self.duration))
                # work_sec = str(float(work) / float(self.duration))
            elif vk[0].endswith("KiB/s"):
                work_sec = str(float(vk[0].replace("KiB/s", "")) / 1024)
                work = str(float(work_sec) * float(self.duration))
                # work_sec = str(float(work) / float(self.duration))

        profile_name = ""
        profile_data = ""
        try:
            with open(self.proflog, "r") as fpl:
                l = fpl.readlines()
                if len(l) >= 2:
                    profile_name = l[0]
                    profile_data = l[1]
        except:
            pass
        if self.ZIPF:
            print("# ncpu secs works works/sec %s" % profile_name)
            print("%s %s %s %s %s" %
                (self.zipf, self.duration, work, work_sec, profile_data))
        else:
            print("# ncpu secs works works/sec %s" % profile_name)
            print("%s %s %s %s %s" %
                (self.ncore, self.duration, work, work_sec, profile_data))

    def _append_to_config(self, config_str):
        self._exec_cmd("echo \'%s\' >> %s" % (config_str, self.config.name)).wait()

    def _exec_cmd(self, cmd, out=None):
        # write cmd to a log file
        # with open("./fio-cmd.txt", "a") as f:
        #     f.write(cmd)
        #     f.write("\n")
        # execute cmd

        p = subprocess.Popen(cmd, shell=True, stdout=out, stderr=out)
        return p

if __name__ == "__main__":
    parser = optparse.OptionParser()
    parser.add_option("--type", help="workload name")
    parser.add_option("--ncore", help="number of core")
    parser.add_option("--nbg", help="not used")
    parser.add_option("--directio", help="not used")
    parser.add_option("--duration", help="benchmark time in seconds")
    parser.add_option("--root", help="benchmark root directory")
    parser.add_option("--profbegin", help="profile begin command")
    parser.add_option("--profend", help="profile end command")
    parser.add_option("--proflog", help="profile log path")
    (opts, args) = parser.parse_args()

    # check options
    for opt in vars(opts):
        val = getattr(opts, opt)
        if val == None:
            print("Missing options: %s" % opt)
            parser.print_help()
            exit(1)

    # run benchmark
    fio = FIO(opts.type, opts.ncore, opts.duration, opts.root,
                          opts.profbegin, opts.profend, opts.proflog)
    rc = fio.run()
    fio.report()
    exit(rc)

